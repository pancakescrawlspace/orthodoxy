"""Replace scanned page images in a PDF with 1-bit (black/white) versions.

Keeps the page content streams (and thus any invisible OCR text layer) intact;
only the image XObjects are rewritten. Images that are genuinely colourful
(e.g. a cover) are left untouched. Output images are Flate-compressed 1-bit;
run `ocrmypdf -O3` afterwards to recompress them as JBIG2.

Requires: pikepdf, pillow, numpy. Follow with `ocrmypdf -O3 --skip-text` for JBIG2 (+ OCR).

usage: binarize.py IN OUT [--color-frac 0.02] [--k 0.2] [--window 0]
                   [--upscale N] [--gray Q] [--dry-run]
"""
import argparse
import zlib

import numpy as np
import pikepdf
from pikepdf import Name, PdfImage


def chroma_fraction(rgb):
    """Fraction of pixels with strong chroma (max-min channel > 80)."""
    a = np.asarray(rgb, dtype=np.int16)
    ch = a.max(axis=2) - a.min(axis=2)
    return float((ch > 80).mean())


def box_mean(x, r):
    """Mean over a (2r+1)^2 window using an integral image (edge-clamped)."""
    p = np.pad(x, r + 1, mode="edge")
    c = p.cumsum(0).cumsum(1)
    n = 2 * r + 1
    s = c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]
    return s[: x.shape[0], : x.shape[1]] / (n * n)


def sauvola(gray, window, k):
    g = np.asarray(gray, dtype=np.float64)
    r = window // 2
    m = box_mean(g, r)
    s = np.sqrt(np.maximum(box_mean(g * g, r) - m * m, 0))
    t = m * (1 + k * (s / 128.0 - 1))
    return g > t  # True = white


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--color-frac", type=float, default=0.02)
    ap.add_argument("--k", type=float, default=0.2)
    ap.add_argument("--window", type=int, default=0, help="0 = ~1/60 of image width")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upscale", type=int, default=1, help="upscale factor before binarizing (low-res scans)")
    ap.add_argument("--gray", type=int, default=0, metavar="Q",
                    help="write greyscale JPEG at quality Q instead of 1-bit")
    a = ap.parse_args()

    pdf = pikepdf.open(a.src)
    seen = set()
    kept, converted = [], 0
    for pno, page in enumerate(pdf.pages, 1):
        for obj in page.images.values():
            if obj.objgen in seen:
                continue
            seen.add(obj.objgen)
            if obj.get("/ImageMask", False) or int(obj.get("/BitsPerComponent", 8)) == 1:
                continue
            if int(obj.Width) < 300 or int(obj.Height) < 300:
                continue  # ornaments, rules, etc.
            im = PdfImage(obj).as_pil_image()
            rgb = im.convert("RGB")
            cf = chroma_fraction(rgb.reduce(4))
            if cf > a.color_frac:
                kept.append((pno, round(cf, 3)))
                continue
            converted += 1
            if a.dry_run:
                continue
            if a.gray:
                import io
                buf = io.BytesIO()
                rgb.convert("L").save(buf, "JPEG", quality=a.gray, optimize=True)
                obj.write(buf.getvalue(), filter=Name.DCTDecode)
                obj.ColorSpace = Name.DeviceGray
                obj.BitsPerComponent = 8
                for key in ("/DecodeParms", "/Decode", "/ColorTransform"):
                    if key in obj:
                        del obj[key]
                continue
            g = rgb.convert("L")
            if a.upscale > 1:
                from PIL import Image
                g = g.resize((g.width * a.upscale, g.height * a.upscale), Image.LANCZOS)
                obj.Width, obj.Height = g.width, g.height
            window = a.window or max(15, (g.width // 60) | 1)
            white = sauvola(g, window, a.k)
            data = np.packbits(white, axis=1).tobytes()
            obj.write(zlib.compress(data, 9), filter=Name.FlateDecode)
            obj.ColorSpace = Name.DeviceGray
            obj.BitsPerComponent = 1
            for key in ("/DecodeParms", "/Decode", "/SMask", "/SMaskInData", "/ColorTransform"):
                if key in obj:
                    del obj[key]
    print(f"converted {converted} images; kept colour on pages {kept}")
    if not a.dry_run:
        pdf.save(a.dst)


if __name__ == "__main__":
    main()
