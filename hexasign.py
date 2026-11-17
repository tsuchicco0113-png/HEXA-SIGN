#!/usr/bin/env python3
"""HEXA SIGN — CardputerZero から EZ Sign (NFC電子ペーパー 4.2インチ 4色) を書き換える (v0.1)

構成: CardputerZero + Sony RC-S380 (USB) + nfcpy + nfc-eink

起動するとホーム画面 (六角形を選んで ENTER、1〜4 キーで直接も):
  1 テンプレ : 営業プレート / 本日の豆 / 名札 / お知らせ / QRコード / フリーテキスト
               項目を ←→ で候補から選ぶ、ENTER で打ち込む、P で HEXA CLIP の最新を貼る
  2 画像     : ~/clip (HEXA CLIP で送ったファイル) / ~/Pictures / USBメモリ の画像
               確認画面で ↑↓ 項目 ←→ 変更: 色(4色/単色) / ディザ(なし/Floyd-Steinberg/Atkinson)
               / 強さ(10〜100%) / 配置(全体/枠いっぱい/引き伸ばし) / 余白の色(全体のとき) / 補正 / 回転
               / 色足し(出てきた色ごとに -5〜+5)。V で等倍表示 (ディザの粒を確認)
  3 クリップ : HEXA CLIP で受け取った文章をそのまま掲示 (スマホで打った日本語を使える)
  4 履歴     : 送った絵をもう一度送る (同じ内容を何枚にも)
  どれも送る前に4色にした絵を本体で確認 → ENTER → EZ Sign をかざす → 完了まで動かさない

共通: ESC = 戻る / 方向キー = F(↑) X(↓) Z(←) C(→)  (文字入力中は fn を押しながら)

usage:
  hexasign                          # 画面アプリ (ホームのアイコンと同じ)
  hexasign --fake                   # リーダーなしで画面の流れだけ試す
  hexasign tpl status status=準備中 # 画面なしで送る (SSH / cron 用)
  hexasign send photo.jpg --colors mono --dither atkinson --strength 70
  hexasign render bean name="ケニア AA" -o /tmp/a.png   # 送らずに絵だけ作る
  hexasign list                     # テンプレートと項目の一覧
"""
import argparse
import datetime
import fcntl
import glob
import json
import math
import os
import select
import signal
import struct
import sys
import threading
import time
import urllib.request

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sign_render as SR                      # noqa: E402
from sign_nfc import SendJob                  # noqa: E402

VERSION = "0.1"
LCD_W, LCD_H = 320, 170
TOP_H, BOT_H = 18, 16
BG, FG, SUB, ACC = (14, 16, 22), (235, 238, 245), (150, 158, 175), (47, 214, 123)
BAR, WARN, ERR = (0, 90, 60), (240, 190, 40), (240, 80, 80)
SEL_BG = (24, 52, 40)

HOME = os.path.expanduser("~" + os.environ.get("SUDO_USER", ""))
DATA_DIR = os.path.join(HOME, ".local", "share", "hexasign")
HIST_DIR = os.path.join(DATA_DIR, "history")
PRESET_PATH = os.path.join(HOME, ".config", "hexasign", "presets.json")
IMAGE_DIRS = [os.path.join(HOME, "clip"), os.path.join(HOME, "Pictures"),
              os.path.join(HOME, "hexasign-images"), "/media/*/*", "/media/*"]
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
CLIP_URL = os.environ.get("HEXACLIP_URL", "http://127.0.0.1:8080")
HIST_MAX = 60
MASCOT_DIRS = [os.path.join(HOME, d, "assets", "mascot") for d in ("hexaclip", "HEXA-CLIP")]


def chown_user(*paths):
    """sudo で動いた時もファイルの持ち主を元のユーザーに"""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if not uid:
        return
    for p in paths:
        try:
            os.chown(p, int(uid), int(gid))
        except OSError:
            pass


# ---------------------------------------------------------------- keys (HEXA CLIP と同じ流儀)
EVIOCGRAB = 0x40044590
EV_FMT = "llHHi"
EV_SIZE = struct.calcsize(EV_FMT)
K_ESC, K_BS, K_TAB, K_ENTER = 1, 14, 15, 28
K_LSHIFT, K_RSHIFT, K_CAPS = 42, 54, 58
K_UP, K_LEFT, K_RIGHT, K_DOWN = 103, 105, 106, 108
CHARS = {
    2: "1!", 3: "2@", 4: "3#", 5: "4$", 6: "5%", 7: "6^", 8: "7&", 9: "8*", 10: "9(", 11: "0)",
    12: "-_", 13: "=+", 16: "qQ", 17: "wW", 18: "eE", 19: "rR", 20: "tT", 21: "yY", 22: "uU",
    23: "iI", 24: "oO", 25: "pP", 26: "[{", 27: "]}", 30: "aA", 31: "sS", 32: "dD", 33: "fF",
    34: "gG", 35: "hH", 36: "jJ", 37: "kK", 38: "lL", 39: ";:", 40: "'\"", 41: "`~", 43: "\\|",
    44: "zZ", 45: "xX", 46: "cC", 47: "vV", 48: "bB", 49: "nN", 50: "mM", 51: ",<", 52: ".>",
    53: "/?", 57: "  ",
}
ACTIONS = {K_UP: "UP", K_DOWN: "DOWN", K_LEFT: "LEFT", K_RIGHT: "RIGHT", K_BS: "BS",
           K_TAB: "TAB", K_ENTER: "ENTER", K_ESC: "ESC"}
KNOWN = set(CHARS) | set(ACTIONS) | {K_LSHIFT, K_RSHIFT, K_CAPS}
FN_ARROWS = {33: "UP", 45: "DOWN", 44: "LEFT", 46: "RIGHT"}      # fn + F / X / Z / C
LETTER_NAV = {"f": "UP", "x": "DOWN", "z": "LEFT", "c": "RIGHT"}


def nav(events):
    """文字入力のない画面: F/X/Z/C 単独を方向キーに"""
    out = []
    for kind, val in events:
        if kind == "CHAR" and val.lower() in LETTER_NAV:
            out.append(("ACT", LETTER_NAV[val.lower()]))
        else:
            out.append((kind, val))
    return out


class Keys:
    def __init__(self, dev):
        self.fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, EVIOCGRAB, 1)
        except OSError as e:
            os.close(self.fd)
            if e.errno == 16:
                raise SystemExit("キーボードが使用中です (別のアプリが起動中?)")
            raise
        self.held = set()
        self.shift_once = False
        self.caps = False

    def _raw(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return []
        try:
            data = os.read(self.fd, EV_SIZE * 64)
        except BlockingIOError:
            return []
        out = []
        for i in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
            _, _, etype, code, value = struct.unpack(EV_FMT, data[i:i + EV_SIZE])
            if etype == 1:
                out.append((code, value))
        return out

    def poll(self, timeout=0.0):
        events = []
        for code, value in self._raw(timeout):
            if value == 0:
                self.held.discard(code)
                continue
            if value == 1:
                self.held.add(code)
            if code in (K_LSHIFT, K_RSHIFT):
                if value == 1:
                    self.shift_once = True
                continue
            if code == K_CAPS:
                if value == 1:
                    self.caps = not self.caps
                continue
            if code not in KNOWN:
                continue                               # fn / sym (修飾キーとして held に残す)
            fn = any(c not in KNOWN for c in self.held if c != code)
            if fn and code in FN_ARROWS:
                events.append(("ACT", FN_ARROWS[code]))
                continue
            if code in ACTIONS:
                events.append(("ACT", ACTIONS[code]))
                continue
            shift = K_LSHIFT in self.held or K_RSHIFT in self.held or self.shift_once
            if self.caps and CHARS[code][0].isalpha():
                shift = not shift
            events.append(("CHAR", CHARS[code][1 if shift else 0]))
            self.shift_once = False
        return events

    def flush(self, sec=0.5):
        end = time.time() + sec
        while time.time() < end:
            self._raw(0.05)
        self.held.clear()
        self.shift_once = False

    def close(self):
        try:
            fcntl.ioctl(self.fd, EVIOCGRAB, 0)
        finally:
            os.close(self.fd)


def find_keyboard():
    name = None
    for line in open("/proc/bus/input/devices"):
        if line.startswith("N: Name="):
            name = line.strip().split("=", 1)[1].strip('"')
        elif line.startswith("H: Handlers=") and name and "tca8418" in name:
            for h in line.split("=", 1)[1].split():
                if h.startswith("event"):
                    return "/dev/input/" + h
    raise RuntimeError("keyboard (tca8418) not found")


class FrameBuffer:
    def __init__(self, dev="/dev/fb0"):
        base = f"/sys/class/graphics/{os.path.basename(dev)}"
        self.bpp = int(open(f"{base}/bits_per_pixel").read())
        try:
            self.stride = int(open(f"{base}/stride").read())
        except OSError:
            self.stride = LCD_W * self.bpp // 8
        self.f = open(dev, "r+b", buffering=0)

    def show(self, img):
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        if self.bpp == 16:
            buf = (((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)).astype("<u2")
        else:
            buf = np.zeros((LCD_H, LCD_W, 4), np.uint8)
            buf[..., 0], buf[..., 1], buf[..., 2], buf[..., 3] = a[..., 2], a[..., 1], a[..., 0], 255
        row = LCD_W * self.bpp // 8
        raw = buf.tobytes()
        if self.stride == row:
            self.f.seek(0)
            self.f.write(raw)
        else:
            for y in range(LCD_H):
                self.f.seek(y * self.stride)
                self.f.write(raw[y * row:(y + 1) * row])

    def clear(self):
        self.show(Image.new("RGB", (LCD_W, LCD_H)))


# ---- PC での確認用 (--sim): 画面は PNG に、キーは台本から
class SimFB:
    def __init__(self, outdir):
        self.outdir, self.last = outdir, None
        os.makedirs(outdir, exist_ok=True)

    def show(self, img):
        self.last = img.copy()

    def shot(self, name):
        if self.last is not None:
            self.last.resize((LCD_W * 2, LCD_H * 2), Image.NEAREST).save(
                os.path.join(self.outdir, name + ".png"))

    def clear(self):
        pass


class SimKeys:
    """台本: "ENTER DOWN c:abc wait:1.5 shot:name ESC" """

    def __init__(self, script, fb):
        self.todo, self.fb, self.until = script.split(), fb, 0

    def poll(self, timeout=0.0):
        time.sleep(min(timeout, 0.02))
        if time.time() < self.until:
            return []
        if not self.todo:
            return [("ACT", "QUIT")]
        tok = self.todo.pop(0)
        if tok.startswith("wait:"):
            self.until = time.time() + float(tok[5:])
            return []
        if tok.startswith("shot:"):
            self.fb.shot(tok[5:])
            return []
        if tok.startswith("c:"):
            return [("CHAR", ch) for ch in tok[2:].replace("_", " ")]
        return [("ACT", tok)]

    def flush(self, sec=0):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------- 画面の部品
_UI_FONTS = {}


def ui_font(size, bold=False):
    key = (size, bold)
    if key not in _UI_FONTS:
        p = SR.font_path(bold)
        _UI_FONTS[key] = ImageFont.truetype(p, size) if p else ImageFont.load_default()
    return _UI_FONTS[key]


def ellipsize(text, f, w):
    text = text.replace("\n", " ").replace("|", " ")
    if f.getlength(text) <= w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:                                 # 二分探索 (長い文でも固まらない)
        mid = (lo + hi + 1) // 2
        if f.getlength(text[:mid] + "…") <= w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


def hexpts(cx, cy, r, rot=30):
    return [(cx + r * math.cos(math.radians(60 * i + rot)),
             cy + r * math.sin(math.radians(60 * i + rot))) for i in range(6)]


def honeycomb_bg():
    img = Image.new("RGB", (LCD_W, LCD_H), BG)
    d = ImageDraw.Draw(img)
    for row in range(-1, 8):
        for col in range(-1, 12):
            d.polygon(hexpts(col * 30 + (15 if row % 2 else 0), 20 + row * 26, 17),
                      outline=(20, 24, 34))
    return img


def glow_hex(img, cx, cy, r, col):
    g = Image.new("RGB", img.size, (0, 0, 0))
    pts = hexpts(cx, cy, r)
    ImageDraw.Draw(g).line(pts + [pts[0]], fill=col, width=6)
    g = g.filter(ImageFilter.GaussianBlur(3))
    img.paste(ImageChops.lighter(img, g))
    ImageDraw.Draw(img).line(pts + [pts[0]], fill=col, width=2)


def draw_icon(d, kind, cx, cy, s, col):
    if kind == "tpl":                              # 看板
        d.rectangle((cx - s * .55, cy - s * .42, cx + s * .55, cy + s * .3), outline=col, width=2)
        d.rectangle((cx - s * .55, cy - s * .42, cx + s * .55, cy - s * .15), fill=col)
        d.line((cx, cy + s * .3, cx, cy + s * .55), fill=col, width=2)
    elif kind == "img":                            # 山と太陽
        d.rectangle((cx - s * .55, cy - s * .4, cx + s * .55, cy + s * .4), outline=col, width=2)
        d.polygon([(cx - s * .45, cy + s * .3), (cx - s * .1, cy - s * .1), (cx + s * .15, cy + s * .15),
                   (cx + s * .28, cy), (cx + s * .45, cy + s * .3)], fill=col)
        d.ellipse((cx + s * .15, cy - s * .32, cx + s * .35, cy - s * .12), fill=col)
    elif kind == "clip":                           # 吹き出し
        d.rounded_rectangle((cx - s * .55, cy - s * .4, cx + s * .55, cy + s * .22), 5, outline=col, width=2)
        d.polygon([(cx - s * .2, cy + s * .2), (cx - s * .35, cy + s * .5), (cx + s * .05, cy + s * .2)],
                  fill=col)
        for i in range(3):
            d.ellipse((cx - s * .3 + i * s * .3 - 2, cy - s * .1 - 2, cx - s * .3 + i * s * .3 + 2,
                       cy - s * .1 + 2), fill=col)
    else:                                          # 時計 (履歴)
        d.ellipse((cx - s * .5, cy - s * .5, cx + s * .5, cy + s * .5), outline=col, width=2)
        d.line((cx, cy, cx, cy - s * .32), fill=col, width=2)
        d.line((cx, cy, cx + s * .25, cy + s * .1), fill=col, width=2)


def thumb(rgb, w, h):
    t = rgb.copy()
    t.thumbnail((w, h), Image.LANCZOS)
    return t


def battery_percent():
    for p in glob.glob("/sys/class/power_supply/*/capacity"):
        try:
            return int(open(p).read().strip())
        except (OSError, ValueError):
            pass
    return None


def load_mascot():
    for d in MASCOT_DIRS:
        p = os.path.join(d, "happy.png")
        if not os.path.exists(p):
            p = os.path.join(d, "idle.png")
        try:
            im = Image.open(p).convert("RGBA")
            im = im.crop(im.getbbox())
            k = 80 / im.height
            return im.resize((max(1, int(im.width * k)), 80), Image.LANCZOS)
        except (OSError, ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------- データ
def load_presets():
    """~/.config/hexasign/presets.json で候補を足せる {"bean": {"name": ["...", ...]}}"""
    try:
        return json.load(open(PRESET_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def field_choices(t, key, presets):
    for k, _label, dflt, cands, _kind in t["fields"]:
        if k == key:
            extra = presets.get(t["id"], {}).get(key, [])
            out = []
            for c in list(extra) + list(cands) + [dflt]:
                if c not in out:
                    out.append(c)
            return out
    return []


def scan_images():
    out, seen = [], set()
    for pat in IMAGE_DIRS:
        for d in glob.glob(pat):
            if not os.path.isdir(d):
                continue
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                p = os.path.join(d, n)
                if n.lower().endswith(IMAGE_EXT) and os.path.isfile(p) and p not in seen:
                    seen.add(p)
                    out.append(p)
    out.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return out


def clip_items(limit=30):
    try:
        req = urllib.request.Request(f"{CLIP_URL}/api/items?limit={limit}&preview=400")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            items = json.loads(r.read())["items"]
        return [it for it in items if not it.get("binary") and (it.get("text") or "").strip()]
    except Exception:
        return None


def clip_full(item):
    if not item.get("truncated"):
        return item["text"]
    try:
        with urllib.request.urlopen(f"{CLIP_URL}/api/items/{item['id']}", timeout=2) as r:
            return json.loads(r.read())["text"]
    except Exception:
        return item["text"]


def clip_text_for_sign(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").strip().replace("\n", "|")


def history_list():
    out = []
    for p in sorted(glob.glob(os.path.join(HIST_DIR, "*.png")), reverse=True):
        meta = {}
        try:
            meta = json.load(open(p[:-4] + ".json", encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out.append({"path": p, "title": meta.get("title", os.path.basename(p)),
                    "time": meta.get("time", ""), "spec": meta.get("spec")})
    return out


def history_save(idx, title, spec):
    os.makedirs(HIST_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(HIST_DIR, stamp)
    im = Image.fromarray(idx.astype(np.uint8), "P")          # 色インデックスのまま保存
    im.putpalette([c for rgb in SR.PALETTE for c in rgb])
    im.save(base + ".png")
    json.dump({"title": title, "time": datetime.datetime.now().strftime("%m/%d %H:%M"),
               "spec": spec}, open(base + ".json", "w", encoding="utf-8"), ensure_ascii=False)
    chown_user(DATA_DIR, HIST_DIR, base + ".png", base + ".json")
    for old in history_list()[HIST_MAX:]:
        for ext in (".png", ".json"):
            try:
                os.remove(old["path"][:-4] + ext)
            except OSError:
                pass


# ---------------------------------------------------------------- 送るもの
class Spec:
    """送る内容。kind: tpl / image / hist"""

    def __init__(self, kind, **kw):
        self.kind = kind
        self.tid = kw.get("tid")
        self.values = dict(kw.get("values") or {})
        self.path = kw.get("path")
        self.opts = dict(SR.DEFAULT_OPTS)
        self.opts.update(kw.get("opts") or SR.PRESETS.get(kw.get("mode") or "photo", {}))
        self.opts["boost"] = list(self.opts.get("boost") or [0, 0, 0, 0])   # 別の画像と共有しない
        self.rotate = kw.get("rotate", 0)
        self._title = kw.get("title")
        self._src = None
        self._cache = {}                          # 変換結果 (送信時にもう一度変換しないように)

    @property
    def title(self):
        if self._title:
            return self._title
        if self.kind == "tpl":
            t = SR.get_template(self.tid)
            v = SR.default_values(t)
            v.update(self.values)
            first = v[t["fields"][0][0]].replace("|", " ")
            return f"{t['name']}: {first}"
        return os.path.basename(self.path or "")

    def to_json(self):
        return {"kind": self.kind, "tid": self.tid, "values": self.values,
                "path": self.path, "opts": self.opts, "rotate": self.rotate}

    def indices(self, width=SR.W, height=SR.H, num_colors=4):
        if self.kind == "tpl":
            img = SR.render(self.tid, self.values)
            if (width, height, num_colors) == (SR.W, SR.H, 4):
                return SR.exact_indices(img)
            return SR.to_indices(img, SR.PRESETS["illust"], width, height, num_colors)
        if self.kind == "hist":
            idx = np.array(Image.open(self.path))
            if idx.shape == (height, width) and num_colors == 4:
                return idx
            return SR.to_indices(SR.indices_to_rgb(idx, SR.PALETTE), SR.PRESETS["illust"],
                                 width, height, num_colors)
        key = (width, height, num_colors, self.rotate, json.dumps(self.opts, sort_keys=True))
        if key in self._cache:
            return self._cache[key]
        if self._src is None:
            self._src = SR.load_image(self.path)
        src = self._src.rotate(-90 * self.rotate, expand=True) if self.rotate else self._src
        idx = SR.dither_image(src, self.opts, width, height, num_colors)
        self._cache = {key: idx}
        return idx

    def make_pixels(self, info):
        return self.indices(info.width, info.height, info.num_colors)


# ---------------------------------------------------------------- アプリ
HOME_ITEMS = [("tpl", "テンプレ"), ("img", "画像"), ("clip", "クリップ"), ("hist", "履歴")]
HEX_POS = [(46, 66), (106, 66), (76, 118), (136, 118)]


class App:
    def __init__(self, fb, keys, args):
        self.fb, self.keys, self.args = fb, keys, args
        self.bg = honeycomb_bg()
        self.mascot = load_mascot()
        self.presets = load_presets()
        self.screen = "home"
        self.stack = []
        self.sel = {"home": 0, "tpl": 0, "fields": 0, "img": 0, "clip": 0, "hist": 0, "opt": 0}
        self.zoom = False
        self.zoom_pos = [0, 0]
        self.converting = False
        self.conv_due = 0
        self.seen_colors = set()
        self.tpl = None
        self.values = {}
        self.spec = None
        self.preview_idx = None
        self.job = None
        self.edit = None              # (key, text, cursor)
        self.toast = ("", 0)
        self.lists = {}
        self._thumbs = {}
        self.dirty = True
        self.confirm_quit = False
        self.last_opts = {}                       # 最後に送った変換設定 (写真/イラスト別に覚える)

    # ---- 画面遷移
    def go(self, screen):
        self.stack.append(self.screen)
        self.screen = screen
        self.dirty = True

    def back(self):
        self.screen = self.stack.pop() if self.stack else "home"
        self.dirty = True

    def say(self, msg, sec=2.0):
        self.toast = (msg, time.time() + sec)
        self.dirty = True

    # ---- 共通の描画
    def frame(self, title, hints, right=""):
        img = Image.new("RGB", (LCD_W, LCD_H), BG)
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, LCD_W, TOP_H - 1), fill=BAR)
        d.polygon(hexpts(9, TOP_H // 2, 6), outline=ACC)
        d.text((19, 1), title, font=ui_font(13, True), fill=FG)
        bat = battery_percent()
        r = right or (f"{bat}%" if bat is not None else "")
        if r:
            f = ui_font(11)
            d.text((LCD_W - 4 - f.getlength(r), 3), r, font=f, fill=FG)
        d.rectangle((0, LCD_H - BOT_H, LCD_W, LCD_H), fill=(24, 28, 38))
        d.text((4, LCD_H - BOT_H + 2), ellipsize(hints, ui_font(10), LCD_W - 8), font=ui_font(10), fill=SUB)
        return img, d

    def draw_toast(self, img):
        msg, until = self.toast
        if msg and time.time() < until:
            d = ImageDraw.Draw(img)
            f = ui_font(12, True)
            w = f.getlength(msg) + 16
            x0 = (LCD_W - w) / 2
            d.rounded_rectangle((x0, 70, x0 + w, 96), 6, fill=(40, 46, 60), outline=ACC)
            d.text((x0 + 8, 75), msg, font=f, fill=FG)

    def list_rows(self, d, items, sel, x0, y0, w, h, row_h=19, sub=None):
        """items: 表示文字のリスト。選択行が見えるようにスクロール"""
        n = max(1, h // row_h)
        top = min(max(0, sel - n // 2), max(0, len(items) - n))
        f = ui_font(12)
        for i in range(top, min(len(items), top + n)):
            y = y0 + (i - top) * row_h
            if i == sel:
                d.rounded_rectangle((x0, y, x0 + w, y + row_h - 2), 4, fill=SEL_BG, outline=ACC)
            d.text((x0 + 5, y + 2), ellipsize(items[i], f, w - 10), font=f, fill=FG if i == sel else SUB)
        if len(items) > n:                         # スクロールバー
            bh = max(8, h * n // len(items))
            by = y0 + (h - bh) * top // max(1, len(items) - n)
            d.rectangle((x0 + w + 2, by, x0 + w + 4, by + bh), fill=(70, 80, 95))

    def sign_thumb(self, key, make, w=150, h=112):
        """EZ Sign の絵の縮小 (枠つき)。make() → RGB 400x300"""
        k = (key, w, h)
        if k not in self._thumbs:
            try:
                self._thumbs[k] = thumb(make(), w, h)
            except Exception as e:                 # noqa: BLE001
                print("thumb failed:", key, e, flush=True)
                self._thumbs[k] = None
            if len(self._thumbs) > 40:
                self._thumbs.pop(next(iter(self._thumbs)))
        return self._thumbs[k]

    def paste_thumb(self, img, t, x, y):
        d = ImageDraw.Draw(img)
        if t is None:
            d.rectangle((x, y, x + 150, y + 112), outline=(70, 80, 95))
            d.text((x + 40, y + 48), "表示できません", font=ui_font(11), fill=SUB)
            return
        d.rectangle((x - 3, y - 3, x + t.width + 2, y + t.height + 2), fill=(60, 64, 72))
        img.paste(t, (x, y))

    # ---- 各画面の描画
    def draw_home(self):
        img = self.bg.copy()
        d = ImageDraw.Draw(img)
        d.rectangle((0, 0, LCD_W, TOP_H - 1), fill=BAR)
        d.text((8, 1), "HEXA SIGN", font=ui_font(13, True), fill=FG)
        d.text((92, 3), "EZ Sign 書き換え", font=ui_font(11), fill=FG)
        bat = battery_percent()
        if bat is not None:
            d.text((LCD_W - 34, 3), f"{bat}%", font=ui_font(11), fill=FG)
        sel = self.sel["home"]
        for i, ((kind, label), (cx, cy)) in enumerate(zip(HOME_ITEMS, HEX_POS)):
            on = i == sel
            if on:
                d.polygon(hexpts(cx, cy, 29), fill=(0, 60, 36))
                glow_hex(img, cx, cy, 29, ACC)
                d = ImageDraw.Draw(img)
            else:
                d.polygon(hexpts(cx, cy, 29), fill=(22, 26, 36), outline=(60, 70, 88))
            col = ACC if on else SUB
            draw_icon(d, kind, cx, cy - 8, 18, col)
            f = ui_font(10, True)
            d.text((cx - f.getlength(label) / 2, cy + 7), label, font=f, fill=FG if on else SUB)
        # 右: 前回送った絵
        hist = self.lists.get("hist_home")
        if hist is None:
            hist = self.lists["hist_home"] = history_list()[:1]
        x0 = 176
        d.text((x0, 20), "前回の表示", font=ui_font(10), fill=SUB)
        if hist:
            t = self.sign_thumb(("hist", hist[0]["path"]),
                                lambda: SR.indices_to_rgb(np.array(Image.open(hist[0]["path"]))), 136, 102)
            self.paste_thumb(img, t, x0 + 2, 38)
            d.text((x0, 140), ellipsize(f"{hist[0]['time']} {hist[0]['title']}", ui_font(10), 140),
                   font=ui_font(10), fill=SUB)
        else:
            d.rectangle((x0 + 2, 40, x0 + 138, 142), outline=(50, 56, 70))
            d.text((x0 + 22, 84), "まだありません", font=ui_font(11), fill=SUB)
        d.rectangle((0, LCD_H - BOT_H, LCD_W, LCD_H), fill=(24, 28, 38))
        d.text((4, LCD_H - BOT_H + 2), "ENTER 開く  1-4 直接  ESC 終了" + ("  [FAKE]" if self.args.fake else ""),
               font=ui_font(10), fill=SUB)
        if self.confirm_quit:
            d.rounded_rectangle((60, 56, 260, 112), 8, fill=(30, 34, 46), outline=ACC)
            d.text((96, 64), "終了しますか?", font=ui_font(14, True), fill=FG)
            d.text((84, 88), "ENTER 終了 / ESC 戻る", font=ui_font(11), fill=SUB)
        return img

    def draw_tpl(self):
        img, d = self.frame("テンプレートを選ぶ", "↑↓ 選ぶ  ENTER 決定  ESC 戻る")
        names = [t["name"] for t in SR.TEMPLATES]
        sel = self.sel["tpl"]
        self.list_rows(d, names, sel, 4, TOP_H + 4, 146, LCD_H - TOP_H - BOT_H - 6)
        t = SR.TEMPLATES[sel]
        self.paste_thumb(img, self.sign_thumb(("tpl", t["id"]), lambda: SR.indices_to_rgb(
            SR.exact_indices(SR.render(t["id"])))), 164, TOP_H + 6)
        d.text((162, TOP_H + 122), ellipsize(t["desc"], ui_font(10), 154), font=ui_font(10), fill=SUB)
        return img

    def cur_values(self):
        v = SR.default_values(self.tpl)
        v.update(self.values)
        return v

    def draw_fields(self):
        t = self.tpl
        img, d = self.frame(t["name"], "↑↓ 項目 ←→ 候補 ENTER 入力 P クリップ S 確認/送信")
        v = self.cur_values()
        sel = self.sel["fields"]
        rows = []
        for k, label, *_ in t["fields"]:
            rows.append(f"{label}: {v[k].replace('|', ' / ')}")
        rows.append("▶ 確認して送る")
        self.list_rows(d, rows, sel, 4, TOP_H + 4, 168, LCD_H - TOP_H - BOT_H - 6, row_h=21)
        key = ("tplv", t["id"], json.dumps(v, ensure_ascii=False, sort_keys=True))
        tn = self.sign_thumb(key, lambda: SR.indices_to_rgb(SR.exact_indices(SR.render(t["id"], v))),
                             136, 102)
        self.paste_thumb(img, tn, 180, TOP_H + 8)
        if sel < len(t["fields"]):
            n = len(field_choices(t, t["fields"][sel][0], self.presets))
            d.text((180, TOP_H + 118), f"候補 {n} 件 (←→)", font=ui_font(10), fill=SUB)
        return img

    def draw_edit(self):
        key, text, cur = self.edit
        label = next(lb for k, lb, *_ in self.tpl["fields"] if k == key)
        kind = next(kd for k, _l, _d, _c, kd in self.tpl["fields"] if k == key)
        hint = "ENTER 決定  ESC 取消  fn+Z/C 移動"
        img, d = self.frame(f"{label} を入力", hint)
        f = ui_font(14)
        # 折り返して表示 (カーソル位置に ▏)
        shown = text[:cur] + "▏" + text[cur:]
        lines = SR.wrap(shown.replace("|", "⏎\n") if kind == "multi" else shown, f, LCD_W - 16)[0]
        y = TOP_H + 8
        d.rounded_rectangle((4, TOP_H + 4, LCD_W - 4, TOP_H + 92), 5, outline=ACC, fill=(20, 24, 32))
        for line in lines[-4:]:
            d.text((8, y), line, font=f, fill=FG)
            y += 20
        tips = ["日本語はスマホ → HEXA CLIP で送り、前の画面で P"]
        if kind == "multi":
            tips.insert(0, "| (shift+\\) で改行")
        y = TOP_H + 98
        for tip in tips:
            d.text((6, y), tip, font=ui_font(10), fill=SUB)
            y += 15
        return img

    def draw_img(self):
        items = self.lists.get("img") or []
        img, d = self.frame(f"画像 ({len(items)})", "↑↓ 選ぶ  ENTER 確認へ  ESC 戻る")
        if not items:
            d.text((10, 40), "画像が見つかりません", font=ui_font(13, True), fill=FG)
            y = 64
            for line in ["~/clip  (HEXA CLIP の 📎 で送る)", "~/Pictures  ~/hexasign-images",
                         "/media/... (USBメモリ)", "に PNG / JPG を置いてください"]:
                d.text((14, y), line, font=ui_font(11), fill=SUB)
                y += 17
            return img
        sel = self.sel["img"]
        names = [os.path.basename(p) for p in items]
        self.list_rows(d, names, sel, 4, TOP_H + 4, 146, LCD_H - TOP_H - BOT_H - 6)
        p = items[sel]

        def mk():
            im = Image.open(p)
            im.draft("RGB", (300, 300))
            return SR.load_image(p) if im.mode not in ("RGB", "L") else im.convert("RGB")
        self.paste_thumb(img, self.sign_thumb(("img", p, os.path.getmtime(p)), mk), 164, TOP_H + 6)
        d.text((162, TOP_H + 122), ellipsize(os.path.dirname(p).replace(HOME, "~"), ui_font(10), 154),
               font=ui_font(10), fill=SUB)
        return img

    def draw_clip(self):
        items = self.lists.get("clip")
        img, d = self.frame("クリップから掲示", "↑↓ 選ぶ  ENTER この文で作る  ESC 戻る")
        if items is None:
            d.text((10, 44), "HEXA CLIP に接続できません", font=ui_font(13, True), fill=FG)
            d.text((14, 70), "systemctl status hexaclip を確認", font=ui_font(11), fill=SUB)
            return img
        if not items:
            d.text((10, 44), "文章のクリップがありません", font=ui_font(13, True), fill=FG)
            d.text((14, 70), "スマホの HEXA CLIP 画面から送ってください", font=ui_font(11), fill=SUB)
            return img
        sel = self.sel["clip"]
        rows = [it["text"].strip().split("\n")[0] for it in items]
        self.list_rows(d, rows, sel, 4, TOP_H + 4, 146, LCD_H - TOP_H - BOT_H - 6)
        body = clip_text_for_sign(items[sel]["text"])
        tn = self.sign_thumb(("clipv", items[sel]["id"]), lambda: SR.indices_to_rgb(
            SR.exact_indices(SR.render("text", {"body": body}))))
        self.paste_thumb(img, tn, 164, TOP_H + 6)
        d.text((162, TOP_H + 122), "→ フリーテキストで編集", font=ui_font(10), fill=SUB)
        return img

    def draw_hist(self):
        items = self.lists.get("hist") or []
        img, d = self.frame(f"履歴 ({len(items)})", "↑↓ 選ぶ  ENTER もう一度送る  D 削除  ESC 戻る")
        if not items:
            d.text((10, 50), "まだ送っていません", font=ui_font(13, True), fill=FG)
            return img
        sel = self.sel["hist"]
        rows = [f"{it['time']} {it['title']}" for it in items]
        self.list_rows(d, rows, sel, 4, TOP_H + 4, 146, LCD_H - TOP_H - BOT_H - 6)
        p = items[sel]["path"]
        self.paste_thumb(img, self.sign_thumb(("hist", p), lambda: SR.indices_to_rgb(
            np.array(Image.open(p)))), 164, TOP_H + 6)
        return img

    def preview_rows(self):
        """画像の変換設定の行: (キー, 名前, 表示する値, 色インデックス or None)
        色足しの行は、変換結果に出てきた色だけ (調整中に0%になっても消さない)"""
        o, s = self.spec.opts, self.spec
        rows = [("fit", "配置", SR.FITS[o["fit"]], None)]
        if o["fit"] == "fit":                     # 全体表示のときだけ余白の色
            pad = o.get("pad", 1) if o.get("pad", 1) in SR.COLOR_MODES[o["colors"]][1] else 1
            rows.append(("pad", "余白", SR.COLOR_NAMES[pad], pad))
        rows += [("colors", "色", SR.COLOR_MODES[o["colors"]][0], None),
                ("dither", "ディザ", {"none": "なし", "floyd-steinberg": "Floyd-S",
                                     "atkinson": "Atkinson"}[o["dither"]], None)]
        if o["dither"] != "none":
            rows.append(("strength", "強さ", f"{o['strength']}%", None))
        rows += [("tune", "補正", "実物寄せ" if o["tune"] else "なし", None),
                 ("rotate", "回転", f"{s.rotate * 90}°", None)]
        counts = np.bincount(self.preview_idx.ravel(), minlength=4)
        usable = SR.COLOR_MODES[o["colors"]][1]
        for i in usable:
            if counts[i] > 0 or o["boost"][i]:
                self.seen_colors.add(i)
        for i in usable:
            if i in self.seen_colors:
                b = o["boost"][i]
                rows.append((f"boost{i}", SR.COLOR_NAMES[i] + "足し", f"{b:+d}" if b else "±0", i))
        return rows

    def draw_preview(self):
        s = self.spec
        if s.kind == "image":
            hints = ("V 縮小に戻す  方向キー 移動" if self.zoom else
                     "↑↓ 項目 ←→ 変更  V 等倍  ENTER 送信  ESC 戻る")
        else:
            hints = "ENTER 送信  ESC 戻る"
        img, d = self.frame("送る前の確認", hints)
        rgb = SR.indices_to_rgb(self.preview_idx)
        if self.zoom:                                # 等倍 (ディザの粒の見え方を確認)
            zx, zy = self.zoom_pos
            pv = rgb.crop((zx, zy, zx + 176, zy + 132))
        else:
            pv = rgb.resize((176, 132), Image.LANCZOS)
        d.rectangle((3, TOP_H + 2, 3 + 176 + 3, TOP_H + 2 + 132 + 3), fill=(60, 64, 72))
        img.paste(pv, (5, TOP_H + 4))
        if self.zoom:
            # 全体のどこを見ているか
            d.rectangle((150, TOP_H + 104, 178, TOP_H + 125), outline=FG, fill=(30, 34, 46))
            fx, fy = self.zoom_pos[0] * 28 // 400, self.zoom_pos[1] * 21 // 300
            d.rectangle((150 + fx, TOP_H + 104 + fy, 150 + fx + 12, TOP_H + 104 + fy + 9), fill=ACC)
        if self.converting:
            d.rounded_rectangle((52, TOP_H + 56, 132, TOP_H + 78), 5, fill=(30, 34, 46), outline=WARN)
            d.text((62, TOP_H + 59), "変換中…", font=ui_font(12, True), fill=WARN)
        x = 188
        y = TOP_H + 4
        f = ui_font(11)
        lines = SR.wrap(s.title, f, LCD_W - x - 4)[0]
        for line in lines[:1 if s.kind == "image" else 3]:
            d.text((x, y), line, font=f, fill=FG)
            y += 15
        if s.kind == "image":
            rows = self.preview_rows()
            counts = np.bincount(self.preview_idx.ravel(), minlength=4)
            tot = counts.sum()
            sel = min(self.sel["opt"], len(rows) - 1)
            n = 7                                         # 一度に見える行数 (多いときは動かす)
            top = min(max(0, sel - n // 2), max(0, len(rows) - n))
            y += 1
            for i in range(top, min(len(rows), top + n)):
                key, name, val, ci = rows[i]
                on = i == sel and not self.zoom
                if on:
                    d.rounded_rectangle((x - 3, y - 1, LCD_W - 3, y + 14), 3, fill=SEL_BG, outline=ACC)
                if key == "pad":                          # 余白の色: 値の前に色見本
                    d.text((x, y), name, font=ui_font(10), fill=SUB)
                    d.rectangle((x + 42, y + 3, x + 49, y + 10), fill=SR.PREVIEW_RGB[ci], outline=(110, 110, 110))
                    v = ("‹ " + val + " ›") if on else val
                    d.text((x + 53, y), v, font=ui_font(10, on), fill=FG if on else SUB)
                    y += 15
                    continue
                if ci is not None:                        # 色足しの行: 色見本 + 割合
                    d.rectangle((x, y + 3, x + 7, y + 10), fill=SR.PREVIEW_RGB[ci], outline=(110, 110, 110))
                    d.text((x + 10, y), name, font=ui_font(10), fill=SUB)
                    pc = f"{counts[ci] * 100 / tot:.0f}%"
                    d.text((LCD_W - 6 - ui_font(9).getlength(pc), y + 1), pc, font=ui_font(9), fill=SUB)
                else:
                    d.text((x, y), name, font=ui_font(10), fill=SUB)
                v = ("‹ " + val + " ›") if on else val
                d.text((x + 42, y), v, font=ui_font(10, on), fill=FG if on else SUB)
                y += 15
            if top > 0:
                d.polygon([(LCD_W - 10, TOP_H + 20), (LCD_W - 6, TOP_H + 16), (LCD_W - 2, TOP_H + 20)], fill=SUB)
            if top + n < len(rows):
                d.polygon([(LCD_W - 10, y - 1), (LCD_W - 6, y + 3), (LCD_W - 2, y - 1)], fill=SUB)
        else:
            y += 4
            counts = np.bincount(self.preview_idx.ravel(), minlength=4)
            tot = counts.sum()
            for name, c, col in zip(["黒", "白", "黄", "赤"], counts, SR.PREVIEW_RGB):
                if c * 100 // tot:
                    d.rectangle((x, y + 3, x + 8, y + 11), fill=col, outline=(90, 90, 90))
                    d.text((x + 12, y), f"{name} {c * 100 // tot}%", font=ui_font(10), fill=SUB)
                    y += 13
        return img

    def draw_send(self):
        j = self.job
        st = j.state
        right = f"{j.serial}" if j.serial else ""
        hint = {"wait": "ESC 中止", "open": "ESC 中止", "done": "ENTER 次の札へ同じ絵  ESC 戻る",
                "error": "ENTER もう一度  ESC 戻る", "cancel": "ENTER もう一度  ESC 戻る"}.get(
            st, "送信中は EZ Sign を動かさないでください")
        img, d = self.frame("EZ Sign に送信", hint, right)
        pv = SR.indices_to_rgb(self.preview_idx).resize((120, 90), Image.LANCZOS)
        d.rectangle((6, TOP_H + 8, 6 + 124, TOP_H + 8 + 94), fill=(60, 64, 72))
        img.paste(pv, (8, TOP_H + 10))
        x, y = 142, TOP_H + 10
        col = {"done": ACC, "error": ERR, "cancel": WARN, "refresh": WARN}.get(st, FG)
        for line in SR.wrap(j.message, ui_font(14, True), LCD_W - x - 6)[0][:3]:
            d.text((x, y), line, font=ui_font(14, True), fill=col)
            y += 19
        t = time.monotonic()
        if st == "wait":                          # かざす合図 (電波マーク)
            cx, cy = 225, TOP_H + 92
            ph = int(t * 3) % 4
            for i in range(3):
                r = 10 + i * 10
                c = ACC if i < ph else (50, 60, 70)
                d.arc((cx - r, cy - r, cx + r, cy + r), 210, 330, fill=c, width=3)
            d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=ACC)
            d.text((x, TOP_H + 112), f"あと {max(0, 60 - int(j.elapsed))} 秒", font=ui_font(10), fill=SUB)
        elif st in ("send", "refresh", "auth"):
            done, total = j.blocks
            frac = done / total if st != "refresh" else 1.0
            bx0, by0, bx1 = x, TOP_H + 66, LCD_W - 8
            d.rounded_rectangle((bx0, by0, bx1, by0 + 12), 4, outline=(80, 90, 105))
            if frac > 0:
                d.rounded_rectangle((bx0 + 2, by0 + 2, bx0 + 2 + (bx1 - bx0 - 4) * frac, by0 + 10), 3, fill=ACC)
            d.text((x, by0 + 16), f"ブロック {done}/{total}", font=ui_font(10), fill=SUB)
            if st == "refresh":
                n = int(t * 8) % 6                # 回る六角形
                for i in range(6):
                    a = math.radians(60 * i - 90)
                    px, py = 288 + 12 * math.cos(a), TOP_H + 112 + 12 * math.sin(a)
                    c = WARN if (i - n) % 6 < 2 else (60, 64, 72)
                    d.ellipse((px - 3, py - 3, px + 3, py + 3), fill=c)
                d.text((x, TOP_H + 104), f"{int(j.elapsed)} 秒経過", font=ui_font(11), fill=SUB)
        elif st == "done":
            if self.mascot is not None:
                img.paste(self.mascot, (LCD_W - self.mascot.width - 4, LCD_H - BOT_H - self.mascot.height),
                          self.mascot)
            d.text((x, TOP_H + 70), "履歴に保存しました", font=ui_font(11), fill=SUB)
            if self.sent_count > 1:
                d.text((x, TOP_H + 88), f"この絵で {self.sent_count} 枚目", font=ui_font(11), fill=SUB)
        return img

    def render(self):
        img = getattr(self, "draw_" + self.screen)()
        self.draw_toast(img)
        self.fb.show(img)

    # ---- 操作
    def open_preview(self, spec):
        if spec is not self.spec:
            self.seen_colors = set()
        self.spec = spec
        self.zoom = False
        self.fb.show(self.frame("変換中…", "しばらくお待ちください")[0])
        try:
            self.preview_idx = spec.indices()
        except Exception as e:                    # noqa: BLE001
            self.say(f"変換できません: {type(e).__name__}", 3)
            return
        if self.screen != "preview":
            self.go("preview")
        self.dirty = True

    def reconvert(self):
        """設定を変えたら、少し待ってから裏で変換し直す (続けて押しても1回で済む)"""
        self.conv_due = time.time() + 0.35
        self.dirty = True

    def conv_tick(self):
        if self.conv_due and time.time() >= self.conv_due and not self.converting:
            self.conv_due = 0
            self.converting = True
            spec = self.spec

            def work():
                try:
                    idx = spec.indices()
                    if spec is self.spec:
                        self.preview_idx = idx
                except Exception as e:            # noqa: BLE001
                    print("convert failed:", e, flush=True)
                    self.say(f"変換できません: {type(e).__name__}", 3)
                finally:
                    self.converting = False
                    self.dirty = True
            threading.Thread(target=work, daemon=True).start()

    def start_send(self):
        self.job = SendJob(self.spec.make_pixels, reader=self.args.reader, fake=self.args.fake)
        self.job_saved = False
        self.job.start()
        if self.screen != "send":
            self.go("send")
            self.sent_count = 0

    def on_key(self, kind, val):
        scr = self.screen
        if scr != "edit":
            kind, val = nav([(kind, val)])[0]
        handler = getattr(self, "key_" + scr)
        handler(kind, val)
        self.dirty = True

    def key_home(self, kind, val):
        if self.confirm_quit:
            if val == "ENTER":
                raise _Quit()
            self.confirm_quit = False
            return
        n = len(HOME_ITEMS)
        s = self.sel["home"]
        if val in ("RIGHT", "TAB"):
            self.sel["home"] = (s + 1) % n
        elif val == "LEFT":
            self.sel["home"] = (s - 1) % n
        elif val in ("DOWN", "UP"):
            self.sel["home"] = (s + 2) % n
        elif val == "ESC":
            self.confirm_quit = True
        elif kind == "CHAR" and val in "1234":
            self.sel["home"] = int(val) - 1
            self.open_home(self.sel["home"])
        elif val == "ENTER":
            self.open_home(self.sel["home"])

    def open_home(self, i):
        kind = HOME_ITEMS[i][0]
        if kind == "tpl":
            self.go("tpl")
        elif kind == "img":
            self.lists["img"] = scan_images()
            self.sel["img"] = 0
            self.go("img")
        elif kind == "clip":
            self.lists["clip"] = clip_items()
            self.sel["clip"] = 0
            self.go("clip")
        else:
            self.lists["hist"] = history_list()
            self.sel["hist"] = 0
            self.go("hist")

    def move(self, name, val, n):
        if n and val == "UP":
            self.sel[name] = (self.sel[name] - 1) % n
        elif n and val == "DOWN":
            self.sel[name] = (self.sel[name] + 1) % n

    def key_tpl(self, kind, val):
        self.move("tpl", val, len(SR.TEMPLATES))
        if val == "ENTER":
            self.tpl = SR.TEMPLATES[self.sel["tpl"]]
            self.values = {}
            self.sel["fields"] = 0
            self.go("fields")
        elif val == "ESC":
            self.back()

    def key_fields(self, kind, val):
        t = self.tpl
        nf = len(t["fields"])
        self.move("fields", val, nf + 1)
        sel = self.sel["fields"]
        v = self.cur_values()
        if val == "ESC":
            self.back()
            return
        if (kind == "CHAR" and val.lower() == "s") or (val == "ENTER" and sel == nf):
            self.open_preview(Spec("tpl", tid=t["id"], values=v))
            return
        if sel >= nf:
            return
        key = t["fields"][sel][0]
        if val in ("LEFT", "RIGHT"):
            ch = field_choices(t, key, self.presets)
            i = ch.index(v[key]) if v[key] in ch else -1
            i = (i + (1 if val == "RIGHT" else -1)) % len(ch)
            self.values[key] = ch[i]
        elif val == "ENTER":
            self.edit = (key, v[key], len(v[key]))
            self.go("edit")
        elif kind == "CHAR" and val.lower() == "p":
            items = clip_items(1)
            if not items:
                self.say("HEXA CLIP に文章がありません")
                return
            self.values[key] = clip_text_for_sign(clip_full(items[0]))
            self.say("クリップを貼りました")

    def key_edit(self, kind, val):
        key, text, cur = self.edit
        if kind == "CHAR":
            text = text[:cur] + val + text[cur:]
            cur += len(val)
        elif val == "BS" and cur > 0:
            text = text[:cur - 1] + text[cur:]
            cur -= 1
        elif val == "LEFT":
            cur = max(0, cur - 1)
        elif val == "RIGHT":
            cur = min(len(text), cur + 1)
        elif val == "UP":
            cur = 0
        elif val == "DOWN":
            cur = len(text)
        elif val == "ENTER":
            self.values[key] = text
            self.back()
            return
        elif val == "ESC":
            self.back()
            return
        self.edit = (key, text, cur)

    def key_img(self, kind, val):
        items = self.lists.get("img") or []
        self.move("img", val, len(items))
        if val == "ESC":
            self.back()
        elif val == "ENTER" and items:
            p = items[self.sel["img"]]
            mode = "photo" if p.lower().endswith((".jpg", ".jpeg", ".webp")) else "illust"
            self.sel["opt"] = 0                     # 配置の行から
            self.open_preview(Spec("image", path=p, opts=self.last_opts.get(mode) or SR.PRESETS[mode]))

    def key_clip(self, kind, val):
        items = self.lists.get("clip") or []
        self.move("clip", val, len(items))
        if val == "ESC":
            self.back()
        elif val == "ENTER" and items:
            self.tpl = SR.get_template("text")
            self.values = {"body": clip_text_for_sign(clip_full(items[self.sel["clip"]]))}
            self.sel["fields"] = 0
            self.go("fields")

    def key_hist(self, kind, val):
        items = self.lists.get("hist") or []
        self.move("hist", val, len(items))
        if val == "ESC":
            self.back()
        elif val == "ENTER" and items:
            it = items[self.sel["hist"]]
            self.open_preview(Spec("hist", path=it["path"], title=it["title"]))
        elif kind == "CHAR" and val.lower() == "d" and items:
            it = items[self.sel["hist"]]
            for ext in (".png", ".json"):
                try:
                    os.remove(it["path"][:-4] + ext)
                except OSError:
                    pass
            self.lists["hist"] = history_list()
            self.lists.pop("hist_home", None)
            self.sel["hist"] = min(self.sel["hist"], max(0, len(self.lists["hist"]) - 1))
            self.say("削除しました")

    def key_preview(self, kind, val):
        s = self.spec
        if val == "ESC":
            if self.zoom:
                self.zoom = False
            else:
                self.conv_due = 0
                self.back()
            return
        if val == "ENTER":
            if self.converting or self.conv_due:
                self.say("変換が終わるまでお待ちください")
                return
            self.start_send()
            return
        if kind == "CHAR" and val.lower() == "v":
            self.zoom = not self.zoom
            if self.zoom:
                self.zoom_pos = [(SR.W - 176) // 2, (SR.H - 132) // 2]
            return
        if self.zoom:                                # 等倍: 方向キーで見る場所を動かす
            dx = {"LEFT": -40, "RIGHT": 40}.get(val, 0)
            dy = {"UP": -30, "DOWN": 30}.get(val, 0)
            self.zoom_pos = [min(max(0, self.zoom_pos[0] + dx), SR.W - 176),
                             min(max(0, self.zoom_pos[1] + dy), SR.H - 132)]
            return
        if s.kind != "image":
            return
        rows = self.preview_rows()
        n = len(rows)
        if val == "UP":
            self.sel["opt"] = (self.sel["opt"] - 1) % n
        elif val == "DOWN":
            self.sel["opt"] = (self.sel["opt"] + 1) % n
        elif val in ("LEFT", "RIGHT"):
            step = 1 if val == "RIGHT" else -1
            key = rows[min(self.sel["opt"], n - 1)][0]
            o = s.opts

            def cycle(lst, cur):
                return lst[(lst.index(cur) + step) % len(lst)]
            if key == "fit":
                o["fit"] = cycle(list(SR.FITS), o["fit"])
            elif key == "pad":
                usable = SR.COLOR_MODES[o["colors"]][1]
                cur = o.get("pad", 1) if o.get("pad", 1) in usable else 1
                o["pad"] = cycle([1, 0, 2, 3] if len(usable) == 4 else [1, 0], cur)
            elif key == "colors":
                o["colors"] = cycle(list(SR.COLOR_MODES), o["colors"])
            elif key == "dither":
                o["dither"] = cycle(list(SR.DITHERS), o["dither"])
            elif key == "strength":
                o["strength"] = min(100, max(10, o["strength"] + 10 * step))
            elif key == "tune":
                o["tune"] = not o["tune"]
            elif key == "rotate":
                s.rotate = (s.rotate + step) % 4
            elif key.startswith("boost"):
                i = int(key[5:])
                o["boost"][i] = max(-SR.BOOST_MAX, min(SR.BOOST_MAX, o["boost"][i] + step))
            # 行の数が変わった時 (ディザなし↔あり) も同じ項目にカーソルを残す
            keys = [r[0] for r in self.preview_rows()]
            if key in keys:
                self.sel["opt"] = keys.index(key)
            self.reconvert()

    def key_send(self, kind, val):
        j = self.job
        if val == "ESC":
            if j.cancelable:
                j.cancel.set()
            elif j.finished:
                self.back()
            else:
                self.say("書き換えが終わるまで待ってください")
        elif val == "ENTER" and j.finished:
            self.start_send()                     # 失敗 → やり直し / 完了 → 次の札へ同じ絵

    # ---- メインループ
    def tick(self):
        """送信中の画面更新と、完了時の履歴保存"""
        j = self.job
        if self.screen == "send" and j is not None:
            self.dirty = True
            if j.state == "done" and not self.job_saved:
                self.job_saved = True
                self.sent_count += 1
                title = self.spec.title
                if self.spec.kind == "image":
                    mode = "photo" if self.spec.path.lower().endswith((".jpg", ".jpeg", ".webp")) else "illust"
                    self.last_opts[mode] = dict(self.spec.opts, boost=[0, 0, 0, 0])  # 色足しは画像ごと
                if self.sent_count == 1:
                    try:
                        history_save(self.preview_idx, title, self.spec.to_json())
                    except OSError as e:
                        print("history save failed:", e, flush=True)
                    self.lists.pop("hist_home", None)

    def run(self):
        self.keys.flush()
        last = 0
        while True:
            self.conv_tick()
            busy = (self.screen == "send" or time.time() < self.toast[1] + 0.1
                    or self.converting or bool(self.conv_due))
            for kind, val in self.keys.poll(0.05 if busy else 0.2):
                if val == "QUIT":
                    return "script end"
                try:
                    self.on_key(kind, val)
                except _Quit:
                    return "quit"
            self.tick()
            now = time.time()
            if self.dirty and (now - last > 0.12 or self.screen != "send"):
                self.render()
                self.dirty = busy
                last = now


class _Quit(Exception):
    pass


# ---------------------------------------------------------------- ランチャー
def launcher_pids():
    pids = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            cmd = open(f"/proc/{d}/cmdline", "rb").read()
        except OSError:
            continue
        if b"M5CardputerZero-APPLaunch" in cmd:
            pids.append(int(d))
    return pids


def signal_launcher(sig):
    for pid in launcher_pids():
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


# ---------------------------------------------------------------- 画面なしで使う (SSH / cron)
def parse_kv(pairs):
    out = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"項目は key=value で指定してください: {p}")
        k, v = p.split("=", 1)
        out[k] = v
    return out


def parse_boost(text):
    names = {"黒": 0, "k": 0, "black": 0, "白": 1, "w": 1, "white": 1,
             "黄": 2, "y": 2, "yellow": 2, "赤": 3, "r": 3, "red": 3}
    out = [0, 0, 0, 0]
    for part in filter(None, (t.strip() for t in text.split(","))):
        k, _, v = part.partition("=")
        if k.strip().lower() not in names or not v:
            raise SystemExit(f"--boost の書き方: 黄=2,赤=-1  ({part})")
        out[names[k.strip().lower()]] = max(-SR.BOOST_MAX, min(SR.BOOST_MAX, int(v)))
    return out


def cli_send(spec, args):
    idx = spec.indices()
    job = SendJob(spec.make_pixels, reader=args.reader, fake=args.fake)
    job.start()
    last = None
    try:
        while not job.finished:
            msg = job.message + (f" {job.blocks[0]}/{job.blocks[1]}" if job.state == "send" else "")
            if msg != last:
                print(msg, flush=True)
                last = msg
            time.sleep(0.1)
    except KeyboardInterrupt:
        job.cancel.set()
        job.join(5)
    print(job.message, flush=True)
    if job.state == "done":
        history_save(idx, spec.title, spec.to_json())
        return 0
    return 1


def cli(args):
    if args.cmd == "list":
        for t in SR.TEMPLATES:
            print(f"{t['id']:8s} {t['name']} — {t['desc']}")
            for k, label, dflt, cands, kind in t["fields"]:
                print(f"    {k}={dflt!r:28s} {label}{' (| で改行)' if kind == 'multi' else ''}")
        return 0
    if args.cmd in ("tpl", "render"):
        try:
            SR.get_template(args.target)
        except KeyError:
            raise SystemExit(f"テンプレートがありません: {args.target} (hexasign list で一覧)")
        spec = Spec("tpl", tid=args.target, values=parse_kv(args.kv))
    else:
        opts = dict(SR.PRESETS[args.mode])
        for k, v in (("colors", args.colors), ("dither", args.dither), ("strength", args.strength),
                     ("fit", args.fit)):
            if v is not None:
                opts[k] = v
        if args.tune is not None:
            opts["tune"] = args.tune
        opts["boost"] = parse_boost(args.boost)
        if args.pad:
            opts["pad"] = {"白": 1, "w": 1, "white": 1, "黒": 0, "k": 0, "black": 0,
                           "黄": 2, "y": 2, "yellow": 2, "赤": 3, "r": 3, "red": 3}[args.pad.lower()]
        spec = Spec("image", path=args.target, opts=opts, rotate=args.rotate)
    if args.cmd == "render":
        out = args.output or "/tmp/hexasign.png"
        SR.indices_to_rgb(spec.indices(), SR.PALETTE).save(out)
        print(out)
        return 0
    return cli_send(spec, args)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="HEXA SIGN — EZ Sign writer for CardputerZero")
    ap.add_argument("--fake", action="store_true", help="リーダーなしで流れだけ試す")
    ap.add_argument("--reader", default="usb", help="nfcpy のリーダー指定 (既定 usb)")
    ap.add_argument("--no-pause", action="store_true", help="ランチャーを一時停止しない")
    ap.add_argument("--sim", help="PC確認用: 画面をこのフォルダにPNGで保存")
    ap.add_argument("--script", default="", help="--sim のキー台本")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("tpl", help="テンプレートで送る")
    p.add_argument("target")
    p.add_argument("kv", nargs="*")
    p = sub.add_parser("render", help="送らずに絵だけ作る")
    p.add_argument("target")
    p.add_argument("kv", nargs="*")
    p.add_argument("-o", "--output")
    p = sub.add_parser("send", help="画像ファイルを送る")
    p.add_argument("target")
    p.add_argument("--mode", default="photo", choices=list(SR.PRESETS),
                   help="まとめ設定 (下の個別指定で上書き)")
    p.add_argument("--colors", choices=list(SR.COLOR_MODES), help="4 / mono(単色)")
    p.add_argument("--dither", choices=list(SR.DITHERS))
    p.add_argument("--strength", type=int, help="ディザの強さ 10〜100")
    p.add_argument("--fit", choices=list(SR.FITS),
                   help="fit(全体・余白は白) / cover(枠いっぱい・はみ出しを切る) / stretch(引き伸ばし)")
    p.add_argument("--tune", action=argparse.BooleanOptionalAction, default=None,
                   help="実物の発色に寄せる補正")
    p.add_argument("--rotate", type=int, default=0, help="90°単位 (1=右に90°)")
    p.add_argument("--pad", choices=["白", "黒", "黄", "赤", "w", "k", "y", "r", "white", "black",
                                     "yellow", "red"], help="--fit fit の余白の色 (既定 白)")
    p.add_argument("--boost", default="", help="色足し -5〜+5 例: 黄=2,赤=-1 (k/w/y/r でも可)")
    sub.add_parser("list", help="テンプレート一覧")
    a = ap.parse_args()
    if a.cmd:
        raise SystemExit(cli(a))

    if a.sim:
        fb = SimFB(a.sim)
        keys = SimKeys(a.script, fb)
    else:
        fb = FrameBuffer()
        keys = Keys(find_keyboard())
    paused = False
    if not a.sim and not a.no_pause and launcher_pids():
        signal_launcher(signal.SIGSTOP)
        paused = True
        for sg in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sg, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    reason = "unknown"
    app = App(fb, keys, a)
    try:
        reason = app.run()
    except KeyboardInterrupt:
        reason = "Ctrl+C"
    except Exception:
        import traceback
        traceback.print_exc()
        reason = "error"
    finally:
        print(f"exit: {reason}", flush=True)
        if app.job is not None and app.job.cancelable:
            app.job.cancel.set()
        keys.close()
        fb.clear()
        if paused:
            signal_launcher(signal.SIGCONT)


if __name__ == "__main__":
    main()
