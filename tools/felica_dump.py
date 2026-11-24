#!/usr/bin/env python3
"""FeliCa Lite / Lite-S のメモリを読んで、16進と文字で表示する (RC-S380 + nfcpy)

自分のカードの中身を覗くための道具。読むだけで、書き込みはしない。
usage: ~/nfc/bin/python tools/felica_dump.py

ブロック: 0x00〜0x0D がユーザーデータ (14ブロック × 16バイト)。
0x0E=REG、0x80=ID、0x82=SER_C、0x83=SYS_C、0x84=CKV、0x86=MC、0x88=状態 など。
"""
import nfc

USER = list(range(0x00, 0x0E))
SYS = {0x0E: "REG 読み書き可の領域", 0x80: "ID 製造ID", 0x81: "D_ID",
       0x82: "SER_C サービスコード構成", 0x83: "SYS_C システムコード",
       0x84: "CKV 鍵バージョン", 0x86: "MC メモリ構成(ロック状態)",
       0x87: "WCNT 書込回数", 0x88: "MAC_A", 0x90: "STATE_1", 0x91: "STATE_2"}


def ascii_of(b):
    return "".join(chr(c) if 0x20 <= c < 0x7F else "." for c in b)


def read_block(tag, bc):
    """1ブロック読む。読めなければ None (鍵がかかっている等)"""
    try:
        data = tag.read_without_encryption([nfc.tag.tt3.ServiceCode(0, 0b001011)],
                                           [nfc.tag.tt3.BlockCode(bc)])
        return bytes(data)
    except Exception:                               # noqa: BLE001
        return None


def dump(tag):
    print("IDm:", tag.idm.hex().upper(), " 種類:", getattr(tag, "product", type(tag).__name__))
    print("Blk              16進                        文字")
    print("-" * 60)
    for bc in USER:
        b = read_block(tag, bc)
        if b is None:
            print(f"{bc:02X}  (読めない)")
            continue
        used = "" if any(b) else "  (空)"
        print(f"{bc:02X}  {b.hex(' ').upper()}  |{ascii_of(b)}|{used}")
    print("-" * 60)
    for bc, name in SYS.items():
        b = read_block(tag, bc)
        if b is not None:
            print(f"{bc:02X}  {b.hex(' ').upper()}  {name}")
    print("-" * 60)
    mc = read_block(tag, 0x86)
    if mc:
        print("MC[0] =", f"{mc[0]:02X}", "(00=未設定/ロックなし の傾向)")
        print("書き込みを試すなら、まず空きブロックで。既存データのあるブロックは触らない。")


def main():
    try:
        clf = nfc.ContactlessFrontend("usb")
    except IOError as e:
        raise SystemExit(f"リーダーを開けません: {e}")
    print("リーダー:", clf, "/ カードをかざしてください")
    try:
        tag = clf.connect(rdwr={"targets": ["212F", "424F"], "on-connect": lambda t: False})
        if not tag:
            return
        if getattr(tag, "type", "") != "Type3Tag":
            print("FeliCa ではありません:", type(tag).__name__)
            return
        dump(tag)
    finally:
        clf.close()


if __name__ == "__main__":
    main()
