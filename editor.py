"""Image editing helpers for stickers: text overlay (Persian/Arabic shaping), rotate, flip."""
import os
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONT_CANDIDATES = [
    os.path.join(FONT_DIR, "Vazirmatn-Bold.ttf"),
    "/usr/share/fonts/truetype/sand-box/google/Noto Sans Arabic/NotoSansArabic-VariableFont_wdth,wght.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
MAX_TEXT = 120
MAX_LINES = 4

_reshaper = arabic_reshaper.ArabicReshaper(configuration={"delete_harakat": False, "support_ligatures": True})

def _font_path():
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None

def _font(size):
    p = _font_path()
    if p:
        # BASIC layout on purpose: we shape (arabic-reshaper) and reorder (python-bidi) ourselves;
        # raqm would shape/reorder a second time and garble the result.
        return ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.BASIC)
    return ImageFont.load_default()

def _is_emoji_char(ch):
    o = ord(ch)
    return (o >= 0x1F000 or 0x2190 <= o <= 0x2BFF or o in (0xFE0F, 0x20E3)
            or 0x1F1E6 <= o <= 0x1F1FF or o == 0x200D)

def clean_text(text):
    text = (text or "").replace("\r", "")
    text = "".join(ch for ch in text if ch == "\n" or not _is_emoji_char(ch))
    text = "\n".join(l.strip() for l in text.split("\n"))
    return text.strip()[:MAX_TEXT]

def shape(line):
    """Logical-order string -> visual-order string ready for left-to-right glyph drawing."""
    return get_display(_reshaper.reshape(line))

def _width(font, line, stroke):
    if not line:
        return 0
    l, t, r, b = font.getbbox(shape(line), stroke_width=stroke)
    return r - l

def _wrap(font, text, max_w, stroke):
    lines = []
    for para in text.split("\n"):
        words = para.split()
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if cur and _width(font, trial, stroke) > max_w:
                lines.append(cur); cur = w
            else:
                cur = trial
        lines.append(cur)
    return [l for l in lines if l] or [""]

def add_text(im, text, pos="bottom"):
    """Draw text (white with black outline) on a copy of `im`. pos: top|center|bottom."""
    text = clean_text(text)
    if not text:
        return im.copy()
    im = im.convert("RGBA")
    W, H = im.size
    margin = int(W * 0.04)
    max_w = W - 2 * margin
    size = max(18, int(min(W, H) * 0.20))
    while True:
        stroke = max(2, size // 9)
        font = _font(size)
        lines = _wrap(font, text, max_w, stroke)
        widest = max(_width(font, l, stroke) for l in lines)
        if (widest <= max_w and len(lines) <= MAX_LINES) or size <= 14:
            break
        size -= 2
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * 1.05)
    total = lh * len(lines)
    if pos == "top":
        y = margin
    elif pos == "center":
        y = (H - total) // 2
    else:
        y = H - margin - total
    y = max(0, y)
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, l in enumerate(lines):
        d.text((W / 2, y + i * lh), shape(l), font=font, fill=(255, 255, 255, 255),
               stroke_width=stroke, stroke_fill=(0, 0, 0, 255), anchor="ma")
    return Image.alpha_composite(im, layer)

def rotate90(im):
    return im.transpose(Image.ROTATE_270)  # 90 degrees clockwise

def flip_h(im):
    return im.transpose(Image.FLIP_LEFT_RIGHT)
