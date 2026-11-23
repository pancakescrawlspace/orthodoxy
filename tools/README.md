# PDF optimization tools

Scanned books are large mainly because every page is stored as a full-colour
photo of paper. Most of that is wasted: the text is really just black and
white, and the paper colour carries no information. These scripts shrink scans
by storing each image the way its content needs. Text becomes 1-bit, photos
become greyscale, and colour is kept only where colour actually appears.

All three scripts rewrite **image objects only**. Page content streams, and so
any invisible OCR text layer, bookmarks and links, stay as they were. Each one
takes `IN OUT` and never overwrites its input. Originals are kept locally in
the git-ignored `originals/` folder.

Requires `pikepdf`, `pillow`, `numpy`, and (for `mrc.py` and `bilevel.py`)
`pymupdf`, plus jbig2enc's `jbig2` for `bilevel.py`.

## Shared techniques

- **Colour detection.** A pixel counts as colourful when the spread between its
  largest and smallest RGB channel is greater than a set value (`--chroma`,
  default 80). An image is colourful when more than `--color-frac` (default 2%)
  of its pixels are. Colourful images, such as covers, plates and maps, are
  left untouched. Yellowed paper and grey ink have a low spread, so those pages
  still get converted. For muted colours, lower `--chroma` (e.g. 40).
- **Small images are skipped.** Images under 300×300 px (ornaments, rules) are
  not worth processing.
- **Shared images are processed once.** An image used on several pages is only
  handled the first time.

## `binarize.py`: scans to black and white

For books stored as one full-page greyscale or colour image per page.

- **Sauvola adaptive thresholding.** Each pixel is compared with a threshold
  computed from the mean *m* and standard deviation *s* of the pixels around it:
  `t = m · (1 + k · (s/128 − 1))`. Using a local threshold instead of a single
  global one copes with gutter shadows, uneven lighting and show-through. The
  window is about 1/60 of the page width; integral images keep this fast.
  `--k` (default 0.2) sets how aggressively faint marks drop out.
- **1-bit output.** Each pixel becomes one bit, stored with Flate. After that,
  run `ocrmypdf -O3 --skip-text` (or `bilevel.py`) to re-encode as JBIG2, which
  is often an order of magnitude smaller than a colour JPEG of the same page.
- **`--upscale N`** enlarges low-resolution scans (Lanczos) before
  thresholding, so letter edges come out smooth instead of jagged.
- **`--gray-pages`** keeps photos as greyscale JPEG instead of 1-bit, and
  stretches their levels: the darkest 1% of pixels become black and the
  brightest 10% (the paper) become white. This raises contrast without crushing
  the mid-tones.
- **`--pages`** limits processing to a list of pages, for example to fix only
  the pages another tool skipped.

## `mrc.py`: flatten archive.org layered (MRC) scans

archive.org PDFs use *mixed raster content*. Each page has three layers:

1. a low-resolution background JPEG (the paper, ~100 ppi)
2. a high-resolution foreground colour JPEG (the ink colour, ~300 ppi)
3. a sharp 1-bit JBIG2 mask that stencils the foreground onto the background
   (the glyph shapes)

The text shape is defined entirely by the mask, so for a text page:

- **Foreground → 1×1 black pixel.** The mask still paints the glyphs, but now
  in pure black instead of mid-grey.
- **Background → 1×1 white pixel**, but only when it is plain paper. A
  background counts as plain when fewer than `--content` (0.1%) of its interior
  pixels are darker than paper (< 230). A 10% band at the left and right edges
  and a 6% band at the top and bottom are ignored, because that is where gutter
  shadows sit.

Together these remove both JPEGs from most pages and turn grey-on-cream text
into black-on-white. Pages whose background holds real content
(illustrations, map shading, heavy underlining) are kept. With `--stretch`,
those pages get the following:

- **Greyscale + one shared level curve.** Both layers are converted to
  greyscale and given the same curve. The white point is the background's 90th
  percentile (the paper); the black point is its 1st percentile, capped at 100
  so that light shading stays light. Because both layers use the same curve,
  the illustration's tones stay consistent across the two layers.
- **Half-resolution foreground.** The sharp edges come from the mask, so the
  foreground colour layer can be downsampled without visible loss.
- **Flattening hidden pixels.** Foreground pixels that the mask never paints
  (with a 5-pixel safety margin) are invisible. They are set to one flat value
  so the JPEG spends no bytes on them.

The script only touches layered pages. Pages stored as a single image are left
for `binarize.py`. If any part of a page is colourful, the whole page is left
alone.

## `bilevel.py`: JBIG2 re-encoding and downsampling

- **Lossless JBIG2 generic coding** (`jbig2 -p`) for every large 1-bit image,
  including `/Mask` and `/SMask` stencils. JBIG2's *symbol* mode is avoided: it
  merges similar-looking glyphs, which can silently replace one letter or digit
  with another.
- **Downsampling over-resolved masks.** Any mask above `--threshold` (330 ppi)
  is reduced to `--max-ppi` (300 ppi). Box filtering is followed by a threshold
  at 50%, which keeps stroke weight.
- **`--keep-jbig2`** leaves existing JBIG2 images alone. archive.org's
  symbol-coded masks are about 3× smaller than a lossless re-encode, so
  re-encoding them would make the file bigger.
- **Built-in check.** Every page of the input and the output is rendered at
  30 dpi and compared. Pages with a mean difference above 8 are reported.

## Other tools used in the pipeline

- **`ocrmypdf -O3`**: JBIG2 re-encoding plus OCR in one step.
- **`ocrmypdf -l rus --output-type pdf --optimize 0`**: adds *only* an invisible
  text layer. `--output-type pdf` skips the PDF/A conversion, and
  `--optimize 0` leaves the images byte-for-byte alone. OCR adds some size
  (Брянчанинов: 5.4 → 7.0 MB).
- **`qpdf --recompress-flate --compression-level=9 --object-streams=generate`**
  as the last step. It re-compresses every Flate stream at maximum level and
  packs the many small PDF objects (OCR text, fonts, page dictionaries) into
  compressed object streams.
- **Cover downsampling.** 600 ppi colour covers were re-saved at 200 ppi
  (JPEG q80). This took Брянчанинов from 9.7 MB to 5.4 MB.

## Example: `dvornik_origins_of_intelligence_services_1974.pdf` (46.0 → 11.3 MB)

```sh
python3 tools/mrc.py --stretch --chroma 40 --color-frac 0.003 originals/dvornik….pdf d1.pdf
python3 tools/binarize.py d1.pdf d2.pdf --pages 4-10,14,16-18,21,22,57,115,138,205,250,334,352-355 --gray-pages 115
python3 tools/bilevel.py --keep-jbig2 d2.pdf d3.pdf
qpdf --recompress-flate --compression-level=9 --object-streams=generate d3.pdf out.pdf
```

`mrc.py` flattens the archive.org layers; `binarize.py` handles the plain
single-image pages that `mrc.py` skips, keeping the coin photo on page 115 as
greyscale; `bilevel.py` checks the result without touching archive.org's
compact masks.

Always check the output visually. A contact sheet of every page
(`mutool draw -r 20` + `magick montage`) quickly shows wiped illustrations or
blackened covers.
