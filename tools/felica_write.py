#!/usr/bin/env python3
"""FeliCa Lite / Lite-S のユーザーブロックに文字列を書いて、読み返す (RC-S380 + nfcpy)

自分のカードで遊ぶための道具。書けるのはユーザーブロック 0x00〜0x0D だけ。
システム領域 (鍵 CK=0x85 / メモリ構成 MC=0x86 など) には一切触れない
 → カードが永久ロックされる事故を防ぐ。勤怠機は IDm だけを見るので、ここに何を書いても打刻には影響しない。

usage:
  ~/nfc/bin/python tools/felica_write.py 0 "HELLO HEXA"   # ブロック0に書く
  ~/nfc/bin/python tools/felica_write.py 0 --clear        # ブロック0を 00 で消す
  ~/nfc/bin/python tools/felica_write.py --dump           # 全ユーザーブロックを読むだけ
"""
import argparse
import sys

import nfc
import nfc.tag.tt3

USER_MAX = 0x0D
RW = nfc.tag.tt3.ServiceCode(0, 0b001001)   # 0009h 書き込み(鍵なし)
RO = nfc.tag.tt3.ServiceCode(0, 0b001011)   # 000Bh 読み取り(鍵なし)


def ascii_of(b):
    return "".join(chr(c) if 0x20 <= c < 0x7F else "." for c in b)


def read_block(tag, bc):
    try:
        return bytes(tag.read_without_encryption([RO], [nfc.tag.tt3.BlockCode(bc)]))
    except Exception:                               # noqa: BLE001
        return None


def write_block(tag, bc, data16):
    tag.write_without_encryption([RW], [nfc.tag.tt3.BlockCode(bc)], data16)


def show(tag):
    for bc in range(USER_MAX + 1):
        b = read_block(tag, bc)
        line = "(読めない)" if b is None else f"{b.hex(' ').upper()}  |{ascii_of(b)}|"
        print(f"  {bc:02X}  {line}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("block", nargs="?", type=lambda x: int(x, 0), help="書くブロック 0〜13")
    ap.add_argument("text", nargs="?", default=None, help="書く文字列 (16バイトまで)")
    ap.add_argument("--clear", action="store_true", help="そのブロックを 00 で消す")
    ap.add_argument("--dump", action="store_true", help="読むだけ")
    a = ap.parse_args()

    if a.block is not None and not (0 <= a.block <= USER_MAX):
        sys.exit(f"ブロックは 0〜{USER_MAX} (ユーザー領域) だけ。システム領域には書けません。")

    data = None
    if not a.dump and a.block is not None:
        if a.clear:
            data = bytes(16)
        elif a.text is not None:
            raw = a.text.encode("utf-8")
            if len(raw) > 16:
                sys.exit(f"16バイトまで (今 {len(raw)} バイト)。日本語は1字3バイトになりがち。")
            data = raw.ljust(16, b"\x00")
        else:
            sys.exit("書く文字列か --clear を指定してください (読むだけなら --dump)")

    try:
        clf = nfc.ContactlessFrontend("usb")
    except IOError as e:
        sys.exit(f"リーダーを開けません: {e}")
    print("カードをかざしてください")
    try:
        tag = clf.connect(rdwr={"targets": ["212F", "424F"], "on-connect": lambda t: False})
        if not tag or getattr(tag, "type", "") != "Type3Tag":
            sys.exit("FeliCa を読めませんでした")
        print("IDm:", tag.idm.hex().upper())
        if data is not None:
            before = read_block(tag, a.block)
            write_block(tag, a.block, data)
            after = read_block(tag, a.block)
            print(f"ブロック {a.block:02X} に書き込みました")
            print("  前 :", before.hex(' ').upper() if before else "?")
            print("  後 :", after.hex(' ').upper() if after else "?", f"|{ascii_of(after)}|" if after else "")
            if after == data:
                print("  → 一致。書き込み成功")
            else:
                print("  → 一致しません (鍵がかかっている? MC を確認)")
        else:
            print("ユーザーブロック:")
            show(tag)
    finally:
        clf.close()


if __name__ == "__main__":
    main()
