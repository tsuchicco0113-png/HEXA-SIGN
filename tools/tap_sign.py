#!/usr/bin/env python3
"""カードをかざす → EZ Sign をかざす で、Sign に名前入りの挨拶を表示する

リーダーは RC-S380 の1台。流れ:
  1. 社員証などをかざす → IDm で本人を判定 (tap_greet の cards.json を共用)
  2. リーダーを離す → 「EZ Sign をかざしてください」
  3. EZ Sign をかざす → 挨拶を書き込む (HEXA SIGN の仕組みを流用)
勤怠機には一切触れない。かざした時刻は taps.csv に記録する。

usage:
  ~/nfc/bin/python tools/tap_sign.py            # 1回ぶん (カード→Sign)
  ~/nfc/bin/python tools/tap_sign.py --loop     # 続けて何人も
  ~/nfc/bin/python tools/tap_sign.py --fake     # リーダーなしで流れだけ
"""
import datetime
import os
import sys
import time

import nfc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sign_render as SR          # noqa: E402
from sign_nfc import SendJob      # noqa: E402
import tap_greet as TG            # noqa: E402


def greeting_image_values(name):
    """時間帯で見出しと帯の色を変える"""
    g = TG.greeting()
    color = {"おはよう": "黄", "こんにちは": "赤", "おつかれさま": "黒"}.get(g, "赤")
    now = datetime.datetime.now()
    body = f"{name} さん|{now:%-H:%M} {'出勤' if now.hour < 12 else '退勤' if now.hour >= 17 else ''}".strip()
    return {"title": g, "body": body, "color": color}


def make_pixels_for(name):
    def mk(info):
        img = SR.render("notice", greeting_image_values(name))
        return SR.to_indices(img, None, info.width, info.height, info.num_colors)
    return mk


def read_one_card(reader="usb", timeout=30):
    """社員証を1枚読んで IDm を返す。読めなければ None"""
    clf = nfc.ContactlessFrontend(reader)
    try:
        end = time.monotonic() + timeout
        tag = clf.connect(rdwr={"targets": ["212F", "424F", "106A", "106B"],
                                "on-connect": lambda t: False},
                          terminate=lambda: time.monotonic() > end)
        return tag.idm.hex().upper() if tag and hasattr(tag, "idm") else (
            tag.identifier.hex().upper() if tag else None)
    finally:
        clf.close()


def send_to_sign(name, fake):
    """EZ Sign をかざしてもらって挨拶を書き込む"""
    print("  → EZ Sign をかざしてください")
    job = SendJob(make_pixels_for(name), fake=fake)
    job.start()
    last = None
    while not job.finished:
        msg = job.message + (f" {job.blocks[0]}/{job.blocks[1]}" if job.state == "send" else "")
        if msg != last:
            print("   ", msg)
            last = msg
        time.sleep(0.1)
    print("   ", job.message)
    return job.state == "done"


def one_round(cards, fake):
    if fake:
        idm = "012E64A87A4974AB"
    else:
        print("社員証をかざしてください")
        idm = read_one_card()
    if not idm:
        print("カードを読めませんでした")
        return
    name = cards.get(idm)
    if not name:
        print(f"未登録のカードです (IDm {idm[:4]}…)。先に tap_greet.py で登録してください")
        return
    print(f"  {TG.greeting()}、{name} さん")
    TG.log_tap(idm, name)
    if not fake:
        time.sleep(0.6)       # リーダーを離す間
    send_to_sign(name, fake)


def main():
    fake = "--fake" in sys.argv
    loop = "--loop" in sys.argv
    cards = TG.load_cards()
    if not cards and not fake:
        print("登録カードがありません。先に tools/tap_greet.py で自分のカードを登録してください")
    try:
        while True:
            one_round(cards, fake)
            if not loop:
                break
            cards = TG.load_cards()
            print("\n次の人どうぞ (Ctrl+C で終了)\n")
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
