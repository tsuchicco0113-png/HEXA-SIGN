#!/usr/bin/env python3
"""かざしたカードの IDm で本人を判定して挨拶し、時刻を記録する (RC-S380 + nfcpy)

自分専用タッチ。登録したカードをかざすと、名前入りで時間帯に合わせた挨拶を出し、
かざした時刻を記録する (出入りのタイムライン)。勤怠機には一切触れない。

usage:
  ~/nfc/bin/python tools/tap_greet.py           # 待ち受け。知らないカードはその場で登録
  ~/nfc/bin/python tools/tap_greet.py --list     # 登録済みカード
  ~/nfc/bin/python tools/tap_greet.py --log [N]  # 記録を新しい順に N 件 (既定 20)

データ: ~/.config/hexasign/cards.json (IDm→名前)、~/.local/share/hexasign/taps.csv (記録)
IDm は個人を特定する番号なので、ファイルは自分の端末の中だけに置く。
"""
import csv
import datetime
import json
import os
import sys

import nfc
import nfc.tag.tt3

HOME = os.path.expanduser("~" + os.environ.get("SUDO_USER", ""))
CARDS = os.path.join(HOME, ".config", "hexasign", "cards.json")
TAPS = os.path.join(HOME, ".local", "share", "hexasign", "taps.csv")


def chown_user(*paths):
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid:
        for p in paths:
            try:
                os.chown(p, int(uid), int(gid))
            except OSError:
                pass


def load_cards():
    try:
        return json.load(open(CARDS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cards(cards):
    os.makedirs(os.path.dirname(CARDS), exist_ok=True)
    json.dump(cards, open(CARDS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    chown_user(os.path.dirname(CARDS), CARDS)


def greeting(now=None):
    h = (now or datetime.datetime.now()).hour
    if h < 5:
        return "おつかれさま"
    if h < 11:
        return "おはよう"
    if h < 18:
        return "こんにちは"
    return "おつかれさま"


def log_tap(idm, name):
    os.makedirs(os.path.dirname(TAPS), exist_ok=True)
    new = not os.path.exists(TAPS)
    with open(TAPS, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "name", "idm"])
        w.writerow([datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), name, idm])
    chown_user(os.path.dirname(TAPS), TAPS)


def show_log(n):
    try:
        rows = list(csv.reader(open(TAPS, encoding="utf-8")))[1:]
    except OSError:
        print("まだ記録がありません")
        return
    for r in rows[-n:][::-1]:
        print(f"  {r[0]}  {r[1]}")


def on_tap(tag, cards, register=True):
    idm = tag.idm.hex().upper()
    name = cards.get(idm)
    if name is None:
        print(f"\n知らないカードです (IDm {idm[:4]}…)")
        if not register or not sys.stdin.isatty():
            return
        try:
            name = input("  名前を登録 (空でスキップ): ").strip()
        except EOFError:
            return
        if not name:
            return
        cards[idm] = name
        save_cards(cards)
        print(f"  登録しました: {name}")
    print(f"\n  ★ {greeting()}、{name} さん！  ({datetime.datetime.now():%H:%M})")
    log_tap(idm, name)


def main():
    if "--list" in sys.argv:
        cards = load_cards()
        if not cards:
            print("登録なし")
        for idm, name in cards.items():
            print(f"  {name:12s} {idm}")
        return
    if "--log" in sys.argv:
        i = sys.argv.index("--log")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 20
        show_log(n)
        return

    cards = load_cards()
    try:
        clf = nfc.ContactlessFrontend("usb")
    except IOError as e:
        sys.exit(f"リーダーを開けません: {e}")
    print(f"待ち受け中… カードをかざしてください (Ctrl+C で終了)  登録済み {len(cards)} 件")
    try:
        while True:
            tag = clf.connect(rdwr={"targets": ["212F", "424F", "106A", "106B"],
                                    "on-connect": lambda t: False})
            if tag is None:
                break
            on_tap(tag, cards)
            # 離すまで待つ (同じタッチを二重に数えない)
            while clf.connect(rdwr={"on-connect": lambda t: False, "iterations": 1},
                              terminate=lambda: False) is not None:
                pass
            print("\n待ち受け中…")
    except KeyboardInterrupt:
        print()
    finally:
        clf.close()


if __name__ == "__main__":
    main()
