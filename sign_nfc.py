"""HEXA SIGN — EZ Sign への送信 (RC-S380 + nfcpy + nfc-eink)

SendJob はスレッドで動き、UI は state / progress を見て描くだけ。
  wait → auth → send (ブロックごと) → refresh (書き換え待ち) → done
  どこかで失敗したら error (UI から ENTER で最初からやり直し)

プロトコル自体は nfc-eink に任せる (認証 / 機器情報 / 画像の圧縮・分割 / 書き換え命令)。
ブロック送信とリフレッシュ待ちだけ自分で回して、進み具合と中断に対応している。
"""
import threading
import time

WAIT_TIMEOUT = 60          # カードを待つ秒数
REFRESH_TIMEOUT = 90       # 書き換え完了を待つ秒数 (実測で数十秒)
POLL_INTERVAL = 0.5

MESSAGES = {
    "open": "リーダーを準備中",
    "wait": "EZ Sign をかざしてください",
    "auth": "カードを確認中",
    "send": "画像を送信中",
    "refresh": "書き換え中… 動かさないで",
    "done": "書き換え完了",
    "error": "失敗しました",
    "cancel": "中止しました",
}


def friendly_error(e):
    name = type(e).__name__
    s = str(e)
    if "Cannot open NFC reader" in s or "No such device" in s or "Errno 19" in s:
        return "リーダー (RC-S380) が見つかりません。USBを確認"
    if "Permission" in s or "Errno 13" in s or "access" in s.lower():
        return "リーダーを開く権限がありません (install.sh の udev 設定)"
    if "busy" in s.lower() or "Errno 16" in s:
        return "リーダーが使用中です (nfc-eink 等を終了して)"
    if name == "AuthenticationError":
        return "認証に失敗 (EZ Sign 以外のカード?)"
    if name == "CommunicationError" or "timed out" in s or "lost" in s:
        return "通信が切れました。置き直して ENTER"
    if name == "StatusWordError":
        return "カードがエラーを返しました: " + s
    return f"{name}: {s}"[:80]


class SendJob(threading.Thread):
    """make_pixels(info) → 色インデックス (numpy 2D)。info は機器情報 (画面サイズ・色数)"""

    def __init__(self, make_pixels, reader="usb", fake=False):
        super().__init__(daemon=True)
        self.make_pixels = make_pixels
        self.reader = reader
        self.fake = fake
        self.state = "open"
        self.blocks = (0, 15)            # (送った数, 全体)
        self.t_state = time.monotonic()  # いまの段階に入った時刻
        self.error = ""
        self.serial = ""
        self.cancel = threading.Event()

    # ---- 状態
    def _set(self, st):
        self.state = st
        self.t_state = time.monotonic()

    @property
    def elapsed(self):
        return time.monotonic() - self.t_state

    @property
    def finished(self):
        return self.state in ("done", "error", "cancel")

    @property
    def cancelable(self):
        return self.state in ("open", "wait")     # 送信が始まったら止めない (中途半端な画面になる)

    @property
    def message(self):
        return self.error if self.state == "error" else MESSAGES.get(self.state, self.state)

    # ---- 本体
    def run(self):
        try:
            if self.fake:
                self._run_fake()
            else:
                self._run_real()
        except _Canceled:
            self._set("cancel")
        except Exception as e:                       # noqa: BLE001  (UIに出すので全部拾う)
            import traceback
            traceback.print_exc()
            self.error = friendly_error(e)
            self._set("error")

    def _run_real(self):
        import nfc
        from nfc_eink import EInkCard
        from nfc_eink.image import encode_image
        from nfc_eink.protocol import build_poll_apdu, build_refresh_apdu, is_refresh_complete

        try:
            clf = nfc.ContactlessFrontend(self.reader)
        except IOError as e:
            raise IOError(f"Cannot open NFC reader '{self.reader}': {e}") from e
        try:
            self._set("wait")
            deadline = time.monotonic() + WAIT_TIMEOUT
            tag = clf.connect(
                rdwr={"on-connect": lambda t: False, "iterations": 1, "interval": 0.1},
                terminate=lambda: self.cancel.is_set() or time.monotonic() > deadline)
            if self.cancel.is_set():
                raise _Canceled()
            if not tag:
                raise RuntimeError("カードが見つかりませんでした (時間切れ)")
            if not hasattr(tag, "send_apdu"):
                raise RuntimeError(f"EZ Sign ではないカードです ({type(tag).__name__})")

            self._set("auth")
            card = EInkCard(tag)
            card.authenticate()
            card._read_device_info()               # nfc-eink の connect() と同じ手順
            info = card.device_info
            self.serial = card.serial_number

            pixels = self.make_pixels(info)
            apdus = encode_image(pixels.tolist() if hasattr(pixels, "tolist") else pixels, info)
            self.blocks = (0, len(apdus))
            self._set("send")
            for i, block in enumerate(apdus):
                for cla, ins, p1, p2, data in block:
                    card._send_apdu(cla, ins, p1, p2, data)
                self.blocks = (i + 1, len(apdus))

            self._set("refresh")
            cla, ins, p1, p2, data = build_refresh_apdu()
            card._send_apdu(cla, ins, p1, p2, data, mrl=256)
            cla, ins, p1, p2, data = build_poll_apdu()
            end = time.monotonic() + REFRESH_TIMEOUT
            while time.monotonic() < end:
                r = card._send_apdu(cla, ins, p1, p2, data, mrl=1, check_status=False)
                if r and is_refresh_complete(r):
                    self._set("done")
                    return
                time.sleep(POLL_INTERVAL)
            raise RuntimeError("書き換えが終わりません (時間切れ)")
        finally:
            clf.close()

    def _run_fake(self):
        """リーダーなしで画面の流れを試す (--fake)"""
        time.sleep(0.4)
        self._set("wait")
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            if self.cancel.is_set():
                raise _Canceled()
            time.sleep(0.05)
        self._set("auth")
        self.serial = "FAKE000000"

        class Info:
            width, height, num_colors = 400, 300, 4
        self.make_pixels(Info())
        time.sleep(0.3)
        self._set("send")
        for i in range(15):
            time.sleep(0.12)
            self.blocks = (i + 1, 15)
        self._set("refresh")
        time.sleep(3.0)
        self._set("done")


class _Canceled(Exception):
    pass
