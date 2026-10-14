"""Re-encode 1-bit images (including /Mask and /SMask stencils) as lossless JBIG2,
downsampling any that exceed --max-ppi.

Text layers and content streams are untouched. Every page of the result is
rendered and compared with the input; pages that differ noticeably are listed.

Requires: pikepdf, pymupdf, pillow, numpy, and jbig2enc's `jbig2` on PATH.

usage: bilevel.py IN OUT [--max-ppi 300] [--threshold 330]
"""
import argparse
import os
import subprocess
import tempfile

import pymupdf as fitz
import numpy as np
import pikepdf
from PIL import Image
from pikepdf import Name


def encode_jbig2(img):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "i.png")
        img.save(path)
        return subprocess.run(["jbig2", "-p", path], check=True, capture_output=True).stdout


def decode(doc, xref):
    """Decoded samples as an 'L' image (0 = black), /Decode already applied."""
    pix = fitz.Pixmap(doc, xref)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return Image.fromarray(np.ascontiguousarray(a[:, :, 0]))


def bilevel_images(pdf):
    """Yield (stream, effective ppi) for each distinct large 1-bit image."""
    seen = set()
    for page in pdf.pages:
        mb = [float(v) for v in page.MediaBox]
        pw, ph = (mb[2] - mb[0]) / 72, (mb[3] - mb[1]) / 72
        for img in page.images.values():
            for o in [img] + [img[k] for k in ("/Mask", "/SMask") if isinstance(img.get(k), pikepdf.Stream)]:
                if o.objgen in seen:
                    continue
                seen.add(o.objgen)
                if not (o.get("/ImageMask", False) or int(o.get("/BitsPerComponent", 8)) == 1):
                    continue
                if int(o.Width) < 300 or int(o.Height) < 300:
                    continue
                yield o, min(int(o.Width) / pw, int(o.Height) / ph)


def page_diffs(a, b, dpi=30):
    da, db = fitz.open(a), fitz.open(b)
    out = []
    for i in range(len(da)):
        pa = da[i].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        pb = db[i].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        x = np.frombuffer(pa.samples, np.uint8).astype(np.int16)
        y = np.frombuffer(pb.samples, np.uint8).astype(np.int16)
        out.append(float(np.abs(x - y).mean()) if x.shape == y.shape else 255.0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--max-ppi", type=float, default=300)
    ap.add_argument("--threshold", type=float, default=330, help="only downsample above this ppi")
    a = ap.parse_args()

    pdf = pikepdf.open(a.src)
    doc = fitz.open(a.src)
    n = down = 0
    for o, ppi in list(bilevel_images(pdf)):
        im = decode(doc, o.objgen[0])
        if o.get("/ImageMask", False):
            im = im.point(lambda v: 255 - v)  # MuPDF gives stencils as coverage (255 = paint)
        if ppi > a.threshold:
            scale = a.max_ppi / ppi
            size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
            im = im.resize(size, Image.BOX)
            down += 1
        o.write(encode_jbig2(im.point(lambda v: 255 if v >= 128 else 0).convert("1")),
                filter=Name.JBIG2Decode)
        o.Width, o.Height = im.width, im.height
        o.BitsPerComponent = 1
        for key in ("/DecodeParms", "/Decode"):
            if key in o:
                del o[key]
        n += 1
    pdf.save(a.dst, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.generate)

    diffs = page_diffs(a.src, a.dst)
    bad = [(i + 1, round(d, 1)) for i, d in enumerate(diffs) if d > 8]
    print(f"re-encoded {n} images ({down} downsampled); "
          f"page diff mean {np.mean(diffs):.2f} max {max(diffs):.1f}; suspicious pages: {bad[:20]}")


if __name__ == "__main__":
    main()
