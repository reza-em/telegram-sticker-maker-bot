import io
from PIL import Image, ImageOps

TARGET = 150 * 1024
LIMIT = 512 * 1024

def prepare_image(data: bytes):
    """Decode + resize so one side is exactly 512. Returns a PIL image (RGB/RGBA)."""
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    im.seek(0) if getattr(im, "is_animated", False) else None
    has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
    im = im.convert("RGBA" if has_alpha else "RGB")
    return fit_512(im)

def fit_512(im):
    w, h = im.size
    scale = 512 / max(w, h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    if w >= h:
        nw = 512
    else:
        nh = 512
    if (nw, nh) != (w, h):
        im = im.resize((nw, nh), Image.LANCZOS)
    return im

def encode_webp(im):
    """Encode PIL image to WebP <=512KB. Returns (bytes, (w, h), quality)."""
    im = fit_512(im)
    nw, nh = im.size
    best = None
    for q in list(range(90, 10, -5)) + [10, 5, 1]:
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=q, method=6)
        out = buf.getvalue()
        best = (out, (nw, nh), q)
        # aim for small size while keeping quality decent (>=60); below that accept up to the hard limit
        if len(out) <= TARGET or (q < 60 and len(out) <= LIMIT):
            return best
    return best

def compress_to_sticker(data: bytes):
    """Return (webp_bytes, (w, h), quality). One side exactly 512, other <=512."""
    return encode_webp(prepare_image(data))

if __name__ == "__main__":
    import sys
    from PIL import ImageDraw
    import random
    random.seed(1)
    im = Image.new("RGB", (1600, 1000))
    d = ImageDraw.Draw(im)
    for i in range(400):
        x0,y0=random.randint(0,1500),random.randint(0,900)
        d.ellipse([x0,y0,x0+random.randint(5,300),y0+random.randint(5,300)],
                  fill=tuple(random.randint(0,255) for _ in range(3)))
    b = io.BytesIO(); im.save(b, "JPEG", quality=95)
    for name, src in (("landscape", b.getvalue()),):
        out, size, q = compress_to_sticker(src)
        chk = Image.open(io.BytesIO(out))
        print(name, "orig", len(src), "final", len(out), "q", q, "dims", chk.size, chk.format)
        assert len(out) <= LIMIT and max(chk.size) == 512 and min(chk.size) <= 512
    # portrait PNG with alpha
    im2 = Image.new("RGBA", (300, 900), (255, 0, 0, 0)); ImageDraw.Draw(im2).ellipse([20,20,280,880], fill=(0,128,255,255))
    b2 = io.BytesIO(); im2.save(b2, "PNG")
    out, size, q = compress_to_sticker(b2.getvalue()); chk = Image.open(io.BytesIO(out))
    print("portrait-alpha final", len(out), "dims", chk.size, chk.mode)
    assert max(chk.size) == 512 and len(out) <= LIMIT
    print("OK")
