"""Flatten archive.org-style layered (MRC) PDFs to black text on white.

Each page is: background image + foreground colour image stencilled through a
1-bit /Mask or /SMask (the sharp text). This replaces the foreground colour image with a
1x1 black pixel (the mask still defines the glyphs) and replaces plain,
non-colourful background images with a 1x1 white pixel. Colourful images
(covers, plates) are left alone. Text layers and content streams are untouched.

Requires: pikepdf, pillow, numpy.

usage: mrc.py IN OUT [--color-frac 0.02]
"""
import argparse

import numpy as np
import pikepdf
from pikepdf import Name, PdfImage


def keep(obj, frac, background):
    """True if the image is colourful, or (for backgrounds) has real dark content."""
    try:
        im = PdfImage(obj).as_pil_image().convert("RGB")
    except Exception:
        return True  # can't decode: leave it alone
    im.thumbnail((400, 400))
    a = np.asarray(im, dtype=np.int16)
    if float(((a.max(axis=2) - a.min(axis=2)) > 80).mean()) > frac:
        return True
    return background and float((a.mean(axis=2) < 110).mean()) > 0.01


def set_pixel(obj, value):
    obj.write(bytes([value]))  # uncompressed, 1 byte
    obj.Width, obj.Height = 1, 1
    obj.ColorSpace = Name.DeviceGray
    obj.BitsPerComponent = 8
    for key in ("/Filter", "/DecodeParms", "/Decode", "/SMaskInData", "/ColorTransform"):
        if key in obj:
            del obj[key]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--color-frac", type=float, default=0.02)
    a = ap.parse_args()

    pdf = pikepdf.open(a.src)
    seen = set()
    fg = bg = 0
    kept = []
    for pno, page in enumerate(pdf.pages, 1):
        for obj in page.images.values():
            if obj.objgen in seen:
                continue
            seen.add(obj.objgen)
            if obj.get("/ImageMask", False) or int(obj.get("/BitsPerComponent", 8)) == 1:
                continue
            masked = any(isinstance(obj.get(k), pikepdf.Stream) for k in ("/Mask", "/SMask"))
            if not masked and (int(obj.Width) < 300 or int(obj.Height) < 300):
                continue
            if keep(obj, a.color_frac, background=not masked):
                kept.append(pno)
                continue
            if masked:
                set_pixel(obj, 0)
                fg += 1
            else:
                set_pixel(obj, 255)
                bg += 1
    print(f"foreground->black: {fg}, background->white: {bg}, kept (colour/illustration) on pages {kept}")
    pdf.remove_unreferenced_resources()
    pdf.save(a.dst, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)


if __name__ == "__main__":
    main()
