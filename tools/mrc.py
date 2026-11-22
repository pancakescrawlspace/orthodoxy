"""Flatten archive.org-style layered (MRC) PDFs to black text on white.

Each page is: background image + foreground colour image stencilled through a
1-bit /Mask or /SMask (the sharp text). This replaces the foreground colour image with a
1x1 black pixel (the mask still defines the glyphs) and replaces plain,
non-colourful background images with a 1x1 white pixel. A background counts as plain
when fewer than --content of its interior pixels (ignoring a 10%/6% edge band, where
gutter shadows live) are darker than paper (< 230); otherwise it is kept, since it
holds an illustration, map shading, etc. Colourful pages
(covers, plates) are left alone entirely. Text layers and content streams are untouched.

Only layered pages (those with a masked foreground) are touched; pages stored as a
single full-page image are left for binarize.py.

On pages whose background is kept, the foreground is part of the illustration too
(mid-tones, even near-white areas sit under the mask), so it is not forced to black.
With --stretch, both layers of such a page are converted to greyscale and given the
same level curve: paper (90th percentile of the background) -> white, and the
background's 1st percentile -> black, but never a black point above 100, so light
map shading stays light while grey caption text becomes dark. The foreground is
downsampled to half the background's resolution (its sharp edges come from the mask), and its
pixels that the mask never paints are flattened so the JPEG stays small.
Without --stretch those pages are left alone.

Requires: pikepdf, pymupdf, pillow, numpy.

usage: mrc.py IN OUT [--color-frac 0.02] [--chroma 80] [--content 0.001] [--stretch]
"""
import argparse
import io

import numpy as np
import pikepdf
import pymupdf as fitz
from PIL import Image, ImageFilter
from pikepdf import Name, PdfImage


def analyse(obj, chroma):
    """(PIL image, colourful fraction, interior non-paper fraction), or None if undecodable."""
    try:
        im = PdfImage(obj).as_pil_image().convert("RGB")
    except Exception:
        return None
    t = im.copy()
    t.thumbnail((400, 400))
    a = np.asarray(t, dtype=np.int16)
    h, w = a.shape[:2]
    inner = a[int(h * 0.06):int(h * 0.94), int(w * 0.1):int(w * 0.9)].mean(axis=2)
    return im, float(((a.max(axis=2) - a.min(axis=2)) > chroma).mean()), float((inner < 230).mean())


def set_pixel(obj, value):
    obj.write(bytes([value]))  # uncompressed, 1 byte
    obj.Width, obj.Height = 1, 1
    obj.ColorSpace = Name.DeviceGray
    obj.BitsPerComponent = 8
    for key in ("/Filter", "/DecodeParms", "/Decode", "/SMaskInData", "/ColorTransform"):
        if key in obj:
            del obj[key]


def levels(im):
    """(black point, white point) for a kept background."""
    lo, hi = np.percentile(np.asarray(im.convert("L")), [1, 90])
    return min(lo, 100), hi


def painted(doc, obj):
    """The image's stencil as an 'L' image (255 = painted), or None."""
    for key in ("/SMask", "/Mask"):
        m = obj.get(key)
        if isinstance(m, pikepdf.Stream):
            pix = fitz.Pixmap(doc, m.objgen[0])  # MuPDF gives coverage: 255 = paint
            a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
            return Image.fromarray(np.ascontiguousarray(a[:, :, 0]))
    return None


def stretch(obj, im, lo, hi, size=None, paint=None, quality=75):
    g = im.convert("L")
    if size and size != g.size:
        g = g.resize(size, Image.LANCZOS)
        obj.Width, obj.Height = g.size
    if paint is not None:  # flatten what the mask never shows
        cov = np.asarray(paint.resize(g.size, Image.BOX).filter(ImageFilter.MaxFilter(5))) > 0
        if cov.any():
            arr = np.array(g)
            arr[~cov] = int(np.median(arr[cov]))
            g = Image.fromarray(arr)
    if hi > lo:
        g = g.point(lambda v: int(np.clip((v - lo) * 255 / (hi - lo), 0, 255)))
    buf = io.BytesIO()
    g.save(buf, "JPEG", quality=quality, optimize=True)
    obj.write(buf.getvalue(), filter=Name.DCTDecode)
    obj.ColorSpace = Name.DeviceGray
    obj.BitsPerComponent = 8
    for key in ("/DecodeParms", "/Decode", "/ColorTransform"):
        if key in obj:
            del obj[key]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--color-frac", type=float, default=0.02)
    ap.add_argument("--chroma", type=int, default=80,
                    help="max-min channel spread that counts as colour (lower for muted colours)")
    ap.add_argument("--content", type=float, default=0.001,
                    help="interior non-paper fraction above which a background is kept")
    ap.add_argument("--stretch", action="store_true", help="greyscale + level-stretch kept illustrations")
    a = ap.parse_args()

    pdf = pikepdf.open(a.src)
    doc = fitz.open(a.src)
    seen = set()
    fg = bg = stretched = 0
    kept = []
    for pno, page in enumerate(pdf.pages, 1):
        fgs, bgs = [], []
        for obj in page.images.values():
            if obj.get("/ImageMask", False) or int(obj.get("/BitsPerComponent", 8)) == 1:
                continue
            if any(isinstance(obj.get(k), pikepdf.Stream) for k in ("/Mask", "/SMask")):
                fgs.append(obj)
            elif int(obj.Width) >= 300 and int(obj.Height) >= 300:
                bgs.append(obj)
        if not fgs:
            continue  # not a layered page

        bgs = [(o, analyse(o, a.chroma)) for o in bgs if o.objgen not in seen]
        colour_page = any(r is None or r[1] > a.color_frac for _, r in bgs)
        if colour_page:
            kept.append(pno)
        illus = None  # (black point, white point, size) of a kept background
        for obj, r in bgs:
            seen.add(obj.objgen)
            if colour_page:
                continue
            if r[2] > a.content:  # illustration, map shading, ...
                kept.append(pno)
                if illus is None:
                    illus = (*levels(r[0]), r[0].size)
                if a.stretch:
                    stretch(obj, r[0], *illus[:2])
                    stretched += 1
            else:
                set_pixel(obj, 255)
                bg += 1
        for obj in fgs:
            if obj.objgen in seen:
                continue
            seen.add(obj.objgen)
            if colour_page:
                continue
            r = analyse(obj, a.chroma)
            if r is None or r[1] > a.color_frac:
                kept.append(pno)
                continue
            if illus:  # foreground is part of the illustration: keep its tones
                if a.stretch:
                    w, h = illus[2]
                    stretch(obj, r[0], *illus[:2], size=(w // 2, h // 2), paint=painted(doc, obj), quality=60)
                    stretched += 1
                continue
            set_pixel(obj, 0)
            fg += 1
    print(f"foreground->black: {fg}, background->white: {bg}, stretched: {stretched}, "
          f"kept (colour/illustration) on pages {sorted(set(kept))}")
    pdf.remove_unreferenced_resources()
    pdf.save(a.dst, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)


if __name__ == "__main__":
    main()
