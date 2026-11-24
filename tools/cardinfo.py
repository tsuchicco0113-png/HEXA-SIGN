#!/usr/bin/env python3
"""かざしたカードの種類と番号を表示する (RC-S380 + nfcpy)

usage: ~/nfc/bin/python tools/cardinfo.py        # 1枚読んで終わり
       ~/nfc/bin/python tools/cardinfo.py -l     # 続けて何枚も (Ctrl+C で終了)
読むのは種類・番号 (UID / IDm)・システムコードだけ。カードの中身は読まない、書かない。
"""
import sys
import time

import nfc

SYSTEMS = {  # FeliCa のシステムコード (よく見るもの)
    0x0003: "交通系IC (Suica/ICOCA など)",
    0x8008: "楽天Edy / 共通領域",
    0xFE00: "共通領域 (社員証・学生証・nanaco・WAON などで使われる)",
    0x12FC: "NDEF (FeliCa Lite-S など)",
    0x88B4: "FeliCa Lite / Lite-S",
    0x957A: "FeliCa Lite-S (DFC)",
}


def describe(tag):
    print("-" * 40)
    print("nfcpy の表示 :", tag)
    kind = type(tag).__name__
    if kind.startswith("Type3"):
        print("種類         : FeliCa (Type3)")
        print("IDm          :", tag.idm.hex().upper())
        print("PMm          :", tag.pmm.hex().upper())
        try:
            codes = tag.request_system_code()
        except Exception as e:                  # noqa: BLE001
            codes = []
            print("システムコード: 取得できず", e)
        for c in codes:
            print(f"システムコード: {c:04X}  {SYSTEMS.get(c, '')}")
    else:
        names = {"Type1Tag": "Type1 (Topaz)", "Type2Tag": "Type A (MIFARE Ultralight / NTAG 系)",
                 "Type4ATag": "Type A ISO14443-4 (MIFARE DESFire / 各種ICカード)",
                 "Type4BTag": "Type B ISO14443-4 (マイナンバーカード・免許証など)"}
        print("種類         :", names.get(kind, kind))
        print("UID          :", tag.identifier.hex().upper(), f"({len(tag.identifier)} バイト)")
        t = getattr(tag, "target", None)
        for attr in ("sens_res", "sel_res"):
            v = getattr(t, attr, None)
            if v:
                print(f"{attr:13s}:", v.hex().upper())
        sel = getattr(t, "sel_res", None)
        if sel and sel[0] in (0x08, 0x18, 0x09):
            print("※ SAK から MIFARE Classic の可能性 (nfcpy+RC-S380 では中身は扱えない)")
    print("-" * 40, flush=True)
    return True


def main():
    loop = "-l" in sys.argv
    try:
        clf = nfc.ContactlessFrontend("usb")
    except IOError as e:
        sys.exit(f"リーダーを開けません: {e}\n(USB の挿し直し / sudo で試す / port100 ドライバ)")
    print("リーダー:", clf)
    try:
        while True:
            print("カードをかざしてください (Ctrl+C で終了)", flush=True)
            # 106A (MIFARE等) / 106B / 212F・424F (FeliCa) を順に探す
            tag = clf.connect(rdwr={"targets": ["106A", "106B", "212F", "424F"],
                                    "on-connect": lambda t: False})
            if tag:
                describe(tag)
            if not loop:
                break
            while clf.connect(rdwr={"on-connect": lambda t: False, "iterations": 1},
                              terminate=lambda: False) is not None:
                time.sleep(0.3)              # 離すまで待つ (同じカードを何度も出さない)
            if not tag:
                break
    except KeyboardInterrupt:
        pass
    finally:
        clf.close()


if __name__ == "__main__":
    main()
