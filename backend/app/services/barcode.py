"""Code 128 (set B) barcodes for short links, drawn as SVG or PNG. No third-party library.

Phone cameras open QR codes directly; a 1D barcode needs a scanner app, so QR is what we recommend for sharing,
and barcodes are offered for print layouts and scanners that expect them.
"""

from __future__ import annotations

import html
import io

# Bar/space widths for symbol values 0..105 (ISO/IEC 15417).
_W = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 122231 113222 "
      "123122 123221 223211 221132 221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 212123 212321 "
      "232121 111323 131123 131321 112313 132113 132311 211313 231113 231311 112133 112331 132131 113123 113321 133121 "
      "313121 211331 231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 314111 221411 431111 111224 "
      "111422 121124 121421 141122 141221 112214 112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
      "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 "
      "114311 411113 411311 113141 114131 311141 411131 211412 211214 211232").split()
_STOP = "2331112"
START_B = 104


def encode(text: str) -> list[int]:
    """Module widths (bar, space, bar, …) for `text` in code set B, quiet zones not included."""
    if not text or any(not (32 <= ord(ch) <= 126) for ch in text):
        raise ValueError("Code 128 B takes printable ASCII only")
    values = [START_B] + [ord(ch) - 32 for ch in text]
    check = (values[0] + sum(i * v for i, v in enumerate(values[1:], start=1))) % 103
    widths: list[int] = []
    for v in values + [check]:
        widths.extend(int(c) for c in _W[v])
    widths.extend(int(c) for c in _STOP)
    return widths


def modules(text: str) -> int:
    return sum(encode(text))


def svg(text: str, *, module: float = 2, height: float = 90, fg: str = "#000000", bg: str = "#ffffff",
        caption: bool = True, quiet: int = 12) -> str:
    widths = encode(text)
    total = sum(widths) + quiet * 2
    W = total * module
    cap_h = 22 if caption else 0
    H = height + cap_h + 8
    x = quiet * module
    rects = []
    for i, w in enumerate(widths):
        if i % 2 == 0:
            rects.append(f'<rect x="{x:.2f}" y="4" width="{w * module:.2f}" height="{height}"/>')
        x += w * module
    cap = (f'<text x="{W / 2:.1f}" y="{height + 22}" text-anchor="middle" font-family="Inter,Arial,sans-serif" '
           f'font-size="15" fill="{fg}">{html.escape(text)}</text>') if caption else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" '
            f'shape-rendering="crispEdges" role="img" aria-label="Barcode: {html.escape(text)}">'
            f'<rect width="100%" height="100%" fill="{bg}"/><g fill="{fg}">{"".join(rects)}</g>{cap}</svg>')


def png(text: str, *, module: int = 3, height: int = 160, fg: str = "#000000", bg: str = "#ffffff",
        caption: bool = True, quiet: int = 12) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    widths = encode(text)
    W = (sum(widths) + quiet * 2) * module
    cap_h = 40 if caption else 0
    img = Image.new("RGB", (W, height + cap_h + 16), bg)
    d = ImageDraw.Draw(img)
    x = quiet * module
    for i, w in enumerate(widths):
        if i % 2 == 0:
            d.rectangle([x, 8, x + w * module - 1, 8 + height], fill=fg)
        x += w * module
    if caption:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 24)
        except OSError:
            font = ImageFont.load_default()
        tw = d.textlength(text, font=font)
        d.text(((W - tw) / 2, height + 16), text, fill=fg, font=font)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
