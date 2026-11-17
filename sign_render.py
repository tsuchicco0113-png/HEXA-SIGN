"""HEXA SIGN — EZ Sign に送る画像を作る (テンプレート描画 / 画像変換)

NFC には触らない。PIL と numpy だけで動くので PC でもテストできる。
EZ Sign 4.2 インチ 4色版: 400x300、色インデックス 0=黒 1=白 2=黄 3=赤
"""
import glob
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 400, 300
K, WH, Y, R = (0, 0, 0), (255, 255, 255), (255, 255, 0), (255, 0, 0)
PALETTE = [K, WH, Y, R]                      # 色インデックス順
# 本体の画面でプレビューするときの色 (実物の発色に寄せる: 白は紙っぽく、黄は山吹)
PREVIEW_RGB = [(20, 20, 20), (236, 232, 222), (232, 188, 0), (200, 30, 30)]


# ---------------------------------------------------------------- フォント
JP_TEST = "本日の豆営業中準備中休憩中名札お知らせ売切浅煎深煎産地価格"
_FONT_FILE = {}


def _font_ok(font):
    try:
        nd = font.getmask("\U0010FFFD")
        nd_key = (nd.size, bytes(nd))
        for ch in JP_TEST:
            m = font.getmask(ch)
            if m.getbbox() is None or (m.size, bytes(m)) == nd_key:
                return False
        return True
    except Exception:
        return False


def font_path(bold=True):
    """日本語の字がそろったフォント (本体: AlibabaPuHuiTi / 無ければ Noto CJK)"""
    if bold in _FONT_FILE:
        return _FONT_FILE[bold]
    cands = []
    for p in ["/usr/share/APPLaunch/**/*.tt[fc]", "/usr/share/APPLaunch/**/*.otf",
              "/usr/share/fonts/**/*CJK*", "/usr/share/fonts/**/*[Jj][Pp]*",
              "/usr/share/fonts/**/*[Ii][Pp][Aa]*"]:
        cands += glob.glob(p, recursive=True)

    def rank(f):
        n = os.path.basename(f).lower()
        if "serif" in n or "mono" in n:
            return 50
        order = (["bold", "85", "heavy", "black", "75", "medium", "65", "regular", "55", "light"]
                 if bold else ["medium", "65", "regular", "55", "bold", "75", "light"])
        for i, w in enumerate(order):
            if w in n:
                return i
        return 8
    for f in sorted(set(cands), key=rank):
        try:
            if _font_ok(ImageFont.truetype(f, 20)):
                _FONT_FILE[bold] = f
                return f
        except Exception:
            continue
    _FONT_FILE[bold] = None
    return None


def font(size, bold=True):
    p = font_path(bold)
    if p:
        return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# ---------------------------------------------------------------- 文字の配置
BREAK_AFTER = set(" ・、。,/-")              # ここの後ろなら改行してよい
NO_LINE_HEAD = set("、。ー・）」』!?！？")     # 行の頭に来ないように前の行へ


def _words(text):
    """改行してよい位置で区切る (空白・中黒・読点の後ろ)"""
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in BREAK_AFTER:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def wrap(text, f, max_w):
    """→ (lines, broke)  broke=語の途中で切った (区切りのない長い文など)"""
    lines, broke = [], False
    for para in text.split("\n"):
        line = ""
        for word in _words(para):
            if f.getlength((line + word).rstrip()) <= max_w:
                line += word
                continue
            if line:
                lines.append(line.rstrip())
                line = ""
            word = word.lstrip()
            while f.getlength(word.rstrip()) > max_w and len(word) > 1:   # 1語が長すぎる → 字で切る
                cut = len(word) - 1
                while cut > 1 and f.getlength(word[:cut]) > max_w:
                    cut -= 1
                while cut > 1 and word[cut] in NO_LINE_HEAD:                # 簡単な禁則
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
                broke = True
            line = word
        lines.append(line.rstrip())
    return lines, broke


def fit_text(text, box, max_size, min_size=12, bold=True, max_lines=None, spacing=0.18):
    """box (x0,y0,x1,y1) に収まる最大の文字サイズを探す → (font, lines, line_h)
    まず語の途中で切らずに収まる大きさを探し、無ければ途中で切ってよしとする"""
    bw, bh = box[2] - box[0], box[3] - box[1]

    def best(allow_break):
        for size in range(max_size, min_size - 1, -2):
            f = font(size, bold)
            lines, broke = wrap(text, f, bw)
            if broke and not allow_break:
                continue
            if max_lines and len(lines) > max_lines:
                continue
            lh = int(size * (1 + spacing))
            if lh * len(lines) - int(size * spacing) <= bh:
                return f, lines, lh
        return None
    clean_fit, any_fit = best(False), best(True)
    # 語の途中で切らない方が、あまり小さくならないならそちらを選ぶ
    if clean_fit and (not any_fit or clean_fit[0].size >= any_fit[0].size * 0.7):
        return clean_fit
    if any_fit:
        return any_fit
    f = font(min_size, bold)
    lines, _ = wrap(text, f, bw)
    lh = int(min_size * (1 + spacing))
    return f, lines[:max(1, bh // lh)], lh


def draw_text(d, text, box, fill, max_size, min_size=12, bold=True, align="center",
              valign="center", max_lines=None):
    if not text:
        return
    f, lines, lh = fit_text(text, box, max_size, min_size, bold, max_lines)
    total = lh * len(lines) - int(f.size * 0.18)
    y = box[1] if valign == "top" else box[1] + (box[3] - box[1] - total) // 2
    for line in lines:
        w = f.getlength(line)
        if align == "center":
            x = box[0] + (box[2] - box[0] - w) / 2
        elif align == "right":
            x = box[2] - w
        else:
            x = box[0]
        # 字の上の余白(ascender差)を詰めて、見た目の中心に置く
        top = f.getbbox("あA")[1]
        d.text((x, y - top * 0.6), line, font=f, fill=fill)
        y += lh


def new_canvas(bg=WH):
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"                          # アンチエイリアスなし (4色に減色しても字がにじまない)
    return img, d


# ---------------------------------------------------------------- テンプレート
# fields: (キー, 表示名, 既定値, 候補リスト, 種類)  種類: "line" / "multi"(| で改行)
TEMPLATES = []


def template(tid, name, desc, fields):
    def deco(fn):
        TEMPLATES.append({"id": tid, "name": name, "desc": desc, "fields": fields, "draw": fn})
        return fn
    return deco


STATUS_STYLE = {      # 背景色, 文字色
    "営業中": (R, WH), "OPEN": (R, WH), "営業中 OPEN": (R, WH),
    "準備中": (Y, K), "休憩中": (Y, K), "まもなく開店": (Y, K), "BREAK": (Y, K),
    "本日終了": (K, WH), "CLOSED": (K, WH), "定休日": (K, WH), "売り切れ": (K, WH),
}


@template("status", "営業プレート", "営業中/準備中/本日終了 を大きく", [
    ("status", "状態", "営業中", ["営業中", "準備中", "休憩中", "まもなく開店", "本日終了",
                                  "定休日", "OPEN", "CLOSED"], "line"),
    ("sub", "補足", "10:00 - 17:00", ["10:00 - 17:00", "11:00 - 16:00", "14:00 再開",
                                      "またのお越しを", ""], "line"),
    ("shop", "店名", "SHOP NAME", ["SHOP NAME"], "line"),
])
def draw_status(v):
    bg, fg = STATUS_STYLE.get(v["status"].strip(), (R, WH))
    img, d = new_canvas(WH)
    d.rectangle((0, 0, W, 190), fill=bg)
    draw_text(d, v["status"], (20, 20, W - 20, 175), fg, 120, 30, max_lines=2)
    draw_text(d, v["sub"], (20, 200, W - 20, 250), K, 40, 16, max_lines=1)
    d.line((60, 262, W - 60, 262), fill=bg if bg != WH else K, width=2)
    draw_text(d, v["shop"], (20, 268, W - 20, 296), K, 22, 12, max_lines=1)
    return img


ROASTS = ["浅煎り", "中煎り", "中深煎り", "深煎り"]


@template("bean", "本日の豆", "コーヒー豆の名前・焙煎度・味", [
    ("name", "豆の名前", "エチオピア イルガチェフェ",
     ["エチオピア イルガチェフェ", "ケニア AA", "グアテマラ アンティグア",
      "コロンビア スプレモ", "ブラジル サントス", "マンデリン G1", "HEXA ブレンド"], "line"),
    ("roast", "焙煎度", "浅煎り", ROASTS, "line"),
    ("notes", "味わい", "フローラル・レモン・紅茶", [
        "フローラル・レモン・紅茶", "ベリー・明るい酸味", "チョコ・ナッツ・まろやか",
        "キャラメル・しっかりコク", "スパイス・重厚な苦味"], "multi"),
    ("price", "価格", "¥550", ["¥500", "¥550", "¥600", "¥650", ""], "line"),
])
def draw_bean(v):
    img, d = new_canvas(WH)
    d.rectangle((0, 0, W, 46), fill=K)
    draw_text(d, "本日の豆", (16, 6, 200, 42), Y, 30, 14, align="left", max_lines=1)
    draw_text(d, "TODAY'S BEAN", (200, 10, W - 16, 40), WH, 18, 10, align="right", max_lines=1)
    draw_text(d, v["name"], (16, 56, W - 16, 150), K, 50, 18, max_lines=2)
    # 焙煎度メーター (4段)
    try:
        lv = ROASTS.index(v["roast"].strip())
    except ValueError:
        lv = -1
    x0, y0, cw, ch = 24, 164, 72, 16
    for i in range(4):
        x = x0 + i * (cw + 6)
        d.rectangle((x, y0, x + cw, y0 + ch), outline=K, width=2, fill=R if i <= lv else WH)
    draw_text(d, v["roast"], (x0 + 4 * (cw + 6), y0 - 8, W - 16, y0 + ch + 8), K, 24, 12,
              align="right", max_lines=1)
    d.line((16, 196, W - 16, 196), fill=K, width=1)
    has_price = bool(v["price"].strip())
    right = W - 130 if has_price else W - 16
    draw_text(d, v["notes"].replace("|", "\n"), (16, 204, right, 292), K, 30, 12,
              align="left", max_lines=3)
    if has_price:
        d.rounded_rectangle((W - 120, 214, W - 14, 284), 10, fill=R)
        draw_text(d, v["price"], (W - 116, 218, W - 18, 280), WH, 40, 14, max_lines=1)
    return img


@template("name", "名札", "名前を大きく・肩書き・所属", [
    ("name", "名前", "なまえ", ["なまえ"], "line"),
    ("role", "肩書き", "スタッフ", ["スタッフ", "店長", "出展者", "Maker", "受付"], "line"),
    ("org", "所属", "SHOP NAME", ["SHOP NAME"], "line"),
])
def draw_name(v):
    img, d = new_canvas(WH)
    d.rectangle((0, 0, W, 14), fill=R)
    draw_text(d, v["role"], (24, 30, W - 24, 80), K, 32, 12, max_lines=1)
    draw_text(d, v["name"], (16, 84, W - 16, 210), K, 110, 24, max_lines=1)
    d.rectangle((80, 216, W - 80, 222), fill=R)
    d.rectangle((0, 240, W, H), fill=Y)
    draw_text(d, v["org"], (16, 246, W - 16, 294), K, 34, 12, max_lines=1)
    return img


@template("notice", "お知らせ", "タイトル帯 + 本文", [
    ("title", "見出し", "お知らせ", ["お知らせ", "お願い", "売り切れ", "本日のイベント",
                                     "ご自由にどうぞ", "NEW"], "line"),
    ("body", "本文", "本日は15時で|終了します", ["本日は15時で|終了します",
                                               "ただいま品切れ中です", "お一人様2点まで",
                                               "お気軽に|お声がけください"], "multi"),
    ("color", "帯の色", "赤", ["赤", "黄", "黒"], "line"),
])
def draw_notice(v):
    bg, fg = {"赤": (R, WH), "黄": (Y, K), "黒": (K, Y)}.get(v["color"].strip(), (R, WH))
    img, d = new_canvas(WH)
    d.rectangle((0, 0, W, 80), fill=bg)
    draw_text(d, v["title"], (16, 8, W - 16, 72), fg, 56, 16, max_lines=1)
    draw_text(d, v["body"].replace("|", "\n"), (20, 94, W - 20, 286), K, 64, 14)
    d.rectangle((0, H - 6, W, H), fill=bg)
    return img


@template("qr", "QRコード", "URL のQR + ひとこと", [
    ("url", "URL", "https://example.com/", ["https://example.com/"], "line"),
    ("caption", "見出し", "BOOTHで|販売中", ["BOOTHで|販売中", "作品紹介はこちら",
                                           "Instagram", "取扱説明書"], "multi"),
    ("sub", "補足", "SHOP NAME", ["SHOP NAME", "スマホで読み取ってね"], "multi"),
])
def draw_qr(v):
    img, d = new_canvas(WH)
    size = 232
    try:
        import qrcode
        q = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.box_size = 1
        q.add_data(v["url"] or " ")
        q.make(fit=True)
        qi = q.make_image(fill_color="black", back_color="white").convert("RGB")
        n = qi.size[0]                            # セル数 (box_size=1 なので画素数=セル数)
        k = max(1, size // n)                     # 整数倍で拡大 (セルの大きさをそろえる)
        qi = qi.resize((n * k, n * k), Image.NEAREST)
        img.paste(qi, (12 + (size - qi.size[0]) // 2, (H - qi.size[1]) // 2))
    except ImportError:
        d.rectangle((12, 34, 12 + size, 34 + size), outline=K, width=3)
        draw_text(d, "qrcode\n未導入", (16, 38, 8 + size, 30 + size), K, 30)
    x0 = 12 + size + 8
    d.rectangle((x0, 16, x0 + 5, H - 16), fill=R)
    draw_text(d, v["caption"].replace("|", "\n"), (x0 + 12, 20, W - 8, 200), K, 34, 12,
              align="left")
    draw_text(d, v["sub"].replace("|", "\n"), (x0 + 12, 210, W - 8, H - 20), K, 20, 10,
              align="left")
    return img


@template("text", "フリーテキスト", "好きな文字を大きく (| で改行)", [
    ("body", "本文", "HELLO|EZ SIGN", ["HELLO|EZ SIGN"], "multi"),
    ("color", "文字色", "黒", ["黒", "赤"], "line"),
    ("frame", "飾り", "赤枠", ["なし", "赤枠", "黄帯", "黒地"], "line"),
])
def draw_text_tpl(v):
    frame = v["frame"].strip()
    bg = K if frame == "黒地" else WH
    fg = {"黒": K, "赤": R}.get(v["color"].strip(), K)
    if frame == "黒地":
        fg = Y if fg == K else R
    img, d = new_canvas(bg)
    box = (18, 18, W - 18, H - 18)
    if frame == "赤枠":
        d.rectangle((4, 4, W - 5, H - 5), outline=R, width=8)
        box = (24, 24, W - 24, H - 24)
    elif frame == "黄帯":
        d.rectangle((0, 0, W, 22), fill=Y)
        d.rectangle((0, H - 22, W, H), fill=Y)
        box = (16, 30, W - 16, H - 30)
    draw_text(d, v["body"].replace("|", "\n"), box, fg, 140, 14)
    return img


def get_template(tid):
    for t in TEMPLATES:
        if t["id"] == tid:
            return t
    raise KeyError(tid)


def default_values(t):
    return {k: dflt for k, _label, dflt, _p, _kind in t["fields"]}


def render(tid, values=None):
    t = get_template(tid)
    v = default_values(t)
    v.update({k: str(x) for k, x in (values or {}).items() if k in v})
    return t["draw"](v)


# ---------------------------------------------------------------- 4色化
# 画像の変換設定 (公式アプリの「色」「ディザリング」相当)
COLOR_MODES = {"4": ("4色", [0, 1, 2, 3]), "mono": ("単色", [0, 1])}    # 使う色インデックス
DITHERS = {"none": "なし", "floyd-steinberg": "Floyd-Steinberg", "atkinson": "Atkinson"}
FITS = {"fit": "全体", "cover": "枠いっぱい", "stretch": "引き伸ばし"}
# 実物の発色に寄せたパレット (nfc-eink の tuned と同じ)。補正ONで使う
TUNED = [(0, 0, 0), (160, 160, 160), (200, 128, 0), (160, 0, 0)]
DEFAULT_OPTS = {"colors": "4", "dither": "atkinson", "strength": 100, "fit": "cover", "tune": True,
                "boost": [0, 0, 0, 0]}
BOOST_MAX = 5           # 色足し: -5〜+5 (色インデックス順 黒/白/黄/赤)
BOOST_STEP = 0.12       # 1段で「その色までの距離」を12%縮める (+) / 伸ばす (-)
COLOR_NAMES = ["黒", "白", "黄", "赤"]
PRESETS = {            # 旧 --mode の呼び名 (コマンド互換) → 設定
    "photo": {"colors": "4", "dither": "atkinson", "strength": 100, "fit": "cover", "tune": True},
    "illust": {"colors": "4", "dither": "none", "strength": 100, "fit": "fit", "tune": False},
    "dither": {"colors": "4", "dither": "floyd-steinberg", "strength": 100, "fit": "fit", "tune": False},
    "mono": {"colors": "mono", "dither": "atkinson", "strength": 100, "fit": "cover", "tune": False},
}


def opts_label(o):
    s = f"{COLOR_MODES[o['colors']][0]} / {DITHERS[o['dither']]}"
    if o["dither"] != "none":
        s += f" {o['strength']}%"
    return s


def _fit(img, width, height, fit):
    from PIL import ImageOps
    img = img.convert("RGB")
    if fit == "cover":                           # 縦横比そのまま、はみ出た分を切る
        return ImageOps.fit(img, (width, height), Image.LANCZOS)
    if fit == "stretch":                         # 縦横比を無視して枠に合わせる
        return img.resize((width, height), Image.LANCZOS)
    t = img.copy()
    t.thumbnail((width, height), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(t, ((width - t.width) // 2, (height - t.height) // 2))
    return canvas


def dither_image(img, opts=None, width=W, height=H, num_colors=4):
    """写真・イラスト → 色インデックス (numpy uint8)
    opts: colors ("4"/"mono") / dither ("none"/"floyd-steinberg"/"atkinson")
          strength (0〜100: 誤差を広げる強さ) / fit ("cover"/"fit") / tune (実物の発色に合わせる)
    CIELAB 空間で一番近い色を選び、誤差を周りに配る (nfc-eink と同じ方式に強さを足したもの)"""
    from nfc_eink.convert import _compute_l_scale, rgb_to_lab
    o = dict(DEFAULT_OPTS)
    o.update(opts or {})
    use = COLOR_MODES[o["colors"]][1] if num_colors == 4 else [0, 1]
    pal_rgb = [(TUNED if o["tune"] else PALETTE)[i] for i in use]
    if o["colors"] == "mono" and o["tune"]:
        pal_rgb = [(0, 0, 0), (170, 170, 170)]
    fitted = np.asarray(_fit(img, width, height, o["fit"]), dtype=np.uint8)
    lab = rgb_to_lab(fitted)
    if o["tune"]:
        ls = _compute_l_scale(pal_rgb)
        if ls is not None:
            lab[..., 0] *= ls
    pal = [tuple(float(v) for v in c) for c in rgb_to_lab(np.array(pal_rgb, dtype=np.uint8))]
    # 色足し: 近さの判定に重みを掛ける (+ でその色が選ばれやすく、- で選ばれにくく)
    boost = list(o.get("boost") or [0, 0, 0, 0]) + [0, 0, 0, 0]
    wt = [max(0.2, 1.0 - BOOST_STEP * max(-BOOST_MAX, min(BOOST_MAX, int(boost[i])))) for i in use]
    k = max(0.0, min(1.0, o["strength"] / 100.0))
    if o["dither"] == "none" or k == 0:
        d = ((lab[:, :, None, :] - np.array(pal)[None, None]) ** 2).sum(-1) * np.array(wt)
        return np.array(use, dtype=np.uint8)[d.argmin(-1)]
    if o["dither"] == "atkinson":
        offs = [(0, 1, 1 / 8), (0, 2, 1 / 8), (1, -1, 1 / 8), (1, 0, 1 / 8), (1, 1, 1 / 8), (2, 0, 1 / 8)]
    else:
        offs = [(0, 1, 7 / 16), (1, -1, 3 / 16), (1, 0, 5 / 16), (1, 1, 1 / 16)]
    offs = [(dy, dx, f * k) for dy, dx, f in offs]
    h, w = height, width
    work = lab.tolist()
    out = [[0] * w for _ in range(h)]
    n = len(pal)
    for y in range(h):
        row, res = work[y], out[y]
        rev = y % 2 == 1                 # 1行ごとに向きを変える (誤差が片側に流れて筋になるのを防ぐ)
        xs = range(w - 1, -1, -1) if rev else range(w)
        sg = -1 if rev else 1
        for x in xs:
            px = row[x]
            # 積もった誤差で色が振り切れないように (赤や黄のかたまりができるのを防ぐ)
            L = min(100.0, max(0.0, px[0]))
            A = min(80.0, max(-80.0, px[1]))
            B = min(90.0, max(-90.0, px[2]))
            bi, bd = 0, 1e18
            for ci in range(n):
                cl, ca, cb = pal[ci]
                dd = ((L - cl) ** 2 + (A - ca) ** 2 + (B - cb) ** 2) * wt[ci]
                if dd < bd:
                    bd, bi = dd, ci
            res[x] = bi
            cl, ca, cb = pal[bi]
            el, ea, eb = L - cl, A - ca, B - cb
            for dy, dx, f in offs:
                ny, nx = y + dy, x + dx * sg
                if ny < h and 0 <= nx < w:
                    q = work[ny][nx]
                    q[0] += el * f
                    q[1] += ea * f
                    q[2] += eb * f
    return np.array(use, dtype=np.uint8)[np.array(out, dtype=np.uint8)]


def exact_indices(img):
    """テンプレートで描いた絵 (4色だけでできている) を色インデックスに"""
    a = np.asarray(img.convert("RGB"), dtype=np.int32)
    pal = np.array(PALETTE, dtype=np.int32)
    dist = ((a[:, :, None, :] - pal[None, None, :, :]) ** 2).sum(-1)
    return dist.argmin(-1).astype(np.uint8)


def to_indices(img, opts=None, width=W, height=H, num_colors=4):
    """PIL画像 → 色インデックス (numpy uint8, shape=(height, width))
    opts=None: テンプレート (最も近い色に置き換えるだけ)"""
    if opts is None and img.size == (width, height) and num_colors == 4:
        return exact_indices(img)
    return dither_image(img, opts or PRESETS["illust"], width, height, num_colors)


def indices_to_rgb(idx, colors=PREVIEW_RGB):
    pal = np.array(colors, dtype=np.uint8)
    return Image.fromarray(pal[idx])


def load_image(path, rotate=0):
    from PIL import ImageOps
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    if rotate:
        im = im.rotate(-90 * rotate, expand=True)
    return im
