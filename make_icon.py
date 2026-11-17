#!/usr/bin/env python3
"""ランチャー用アイコン (光る六角形 + 看板)。usage: make_icon.py OUT.png [参考アイコン.png | サイズ]"""
import math
import sys

from PIL import Image, ImageDraw, ImageFilter

out = sys.argv[1]
ref = sys.argv[2] if len(sys.argv) > 2 else "64"
try:
    size = Image.open(ref).size
except (OSError, ValueError):
    n = int(ref) if ref.isdigit() else 64
    size = (n, n)
W, H = size
S = 4                                             # 大きく描いて縮める (なめらかに)
img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
cx, cy, r = W * S / 2, H * S / 2, min(W, H) * S * 0.44
pts = [(cx + r * math.cos(math.radians(60 * i + 30)), cy + r * math.sin(math.radians(60 * i + 30)))
       for i in range(6)]
d = ImageDraw.Draw(img)
d.polygon(pts, fill=(14, 30, 24, 255))
glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
ImageDraw.Draw(glow).line(pts + [pts[0]], fill=(47, 214, 123, 255), width=int(6 * S))
glow = glow.filter(ImageFilter.GaussianBlur(3 * S))
img = Image.alpha_composite(img, glow)
d = ImageDraw.Draw(img)
d.line(pts + [pts[0]], fill=(47, 214, 123, 255), width=int(2 * S))
# 看板 (白い札に赤い帯と黄色い点)
s = r * 0.95
x0, y0, x1, y1 = cx - s * .55, cy - s * .42, cx + s * .55, cy + s * .32
d.rectangle((x0, y0, x1, y1), fill=(236, 232, 222, 255))
d.rectangle((x0, y0, x1, y0 + (y1 - y0) * .32), fill=(210, 30, 30, 255))
d.rectangle((x0 + s * .12, y0 + (y1 - y0) * .5, x1 - s * .12, y0 + (y1 - y0) * .62), fill=(20, 20, 20, 255))
d.rectangle((x0 + s * .12, y0 + (y1 - y0) * .72, x0 + s * .5, y0 + (y1 - y0) * .84), fill=(232, 188, 0, 255))
d.line((cx, y1, cx, cy + s * .55), fill=(47, 214, 123, 255), width=int(2 * S))
img.resize(size, Image.LANCZOS).save(out)
print(out, size)
