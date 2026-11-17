"""実機の代わりに、実機と同じ形の 00D1 応答を返すタグで _run_real を通す"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nfc
import sign_nfc
from hexasign import Spec

D1 = bytes.fromhex(
    "a007f007200258019 0".replace(" ", "")
    + "a10701120030ffffff" + "b10108" + "b20114" + "b30100"
    + "c00a" + b"SEAB000000".hex() + "c10400000000" + "d1070120000000000000")

class Tag:
    def __init__(self): self.log = []; self.polls = 0
    def send_apdu(self, cla, ins, p1, p2, data=None, mrl=0, check_status=True):
        self.log.append((cla, ins, p1, p2, len(data or b"")))
        if ins == 0x20: assert data == b"\x20\x09\x12\x10"; return b""
        if ins == 0xD1: return D1
        if ins == 0xD3: assert len(data) <= 252; return b""
        if ins == 0xD4: assert (p1, p2) == (0x85, 0x80); return b""
        if ins == 0xDE: self.polls += 1; return b"\x01" if self.polls < 3 else b"\x00"
        raise AssertionError(hex(ins))

TAG = Tag()
class CLF:
    def __init__(self, path): pass
    def connect(self, rdwr, terminate): return TAG
    def close(self): pass
nfc.ContactlessFrontend = CLF
sign_nfc.POLL_INTERVAL = 0.01

job = sign_nfc.SendJob(Spec("tpl", tid="status").make_pixels)
job.run()
print("state:", job.state, job.error, "serial:", job.serial, "blocks:", job.blocks)
d3 = [l for l in TAG.log if l[1] == 0xD3]
print("F0D3 APDUs:", len(d3), "final-of-block P2=01:", sum(1 for l in d3 if l[3] == 1), "polls:", TAG.polls)
assert job.state == "done" and job.serial == "SEAB000000" and job.blocks == (15, 15)
assert sum(1 for l in d3 if l[3] == 1) == 15
print("OK")
