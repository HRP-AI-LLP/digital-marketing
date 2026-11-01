"""A small QR code encoder (byte mode, versions 1-10, error correction L/M/Q/H) that draws SVG.

Used for the authenticator-app setup code on the Security page, so no third-party QR library is needed.
Follows ISO/IEC 18004 (structure after Project Nayuki's reference implementation).
"""

from __future__ import annotations

# Index 0 is unused; versions 1..10.
_ECC_PER_BLOCK = {"L": (0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18), "M": (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26),
                  "Q": (0, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24), "H": (0, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28)}
_NUM_BLOCKS = {"L": (0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4), "M": (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5),
               "Q": (0, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8), "H": (0, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8)}
_FORMAT_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}
MAX_VERSION = 10


def _raw_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        result -= (25 * n - 10) * n - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int, ecl: str) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ecl][ver] * _NUM_BLOCKS[ecl][ver]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _alignment_positions(ver: int) -> list[int]:
    if ver == 1:
        return []
    num = ver // 7 + 2
    step = (ver * 8 + num * 3 + 5) // (num * 4 - 4) * 2
    result = [ver * 4 + 10 - i * step for i in range(num - 1)] + [6]
    return list(reversed(result))


class QrCode:
    def __init__(self, data: bytes, ecl: str = "M"):
        for ver in range(1, MAX_VERSION + 1):
            cap_bits = _data_codewords(ver, ecl) * 8
            used = 4 + (8 if ver < 10 else 16) + len(data) * 8
            if used <= cap_bits:
                break
        else:
            raise ValueError("Text too long for this QR encoder")
        self.version, self.ecl = ver, ecl
        self.size = ver * 4 + 17
        bits: list[int] = []

        def put(val: int, n: int) -> None:
            bits.extend((val >> i) & 1 for i in reversed(range(n)))

        put(0b0100, 4)
        put(len(data), 8 if ver < 10 else 16)
        for b in data:
            put(b, 8)
        put(0, min(4, cap_bits - len(bits)))
        put(0, (-len(bits)) % 8)
        pad = 0xEC
        while len(bits) < cap_bits:
            put(pad, 8)
            pad ^= 0xEC ^ 0x11
        codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
        all_cw = self._add_ecc(codewords)

        self.modules = [[False] * self.size for _ in range(self.size)]
        self.is_function = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()
        self._draw_codewords(all_cw)
        best, best_penalty = 0, None
        for m in range(8):
            self._apply_mask(m)
            self._draw_format_bits(m)
            p = self._penalty()
            if best_penalty is None or p < best_penalty:
                best, best_penalty = m, p
            self._apply_mask(m)  # undo (XOR)
        self.mask = best
        self._apply_mask(best)
        self._draw_format_bits(best)

    # ---- structure ---------------------------------------------------------------------------------
    def _add_ecc(self, data: list[int]) -> list[int]:
        ver, ecl = self.version, self.ecl
        num_blocks = _NUM_BLOCKS[ecl][ver]
        ecc_len = _ECC_PER_BLOCK[ecl][ver]
        raw = _raw_modules(ver) // 8
        num_short = num_blocks - raw % num_blocks
        short_len = raw // num_blocks
        divisor = _rs_divisor(ecc_len)
        blocks = []
        k = 0
        for i in range(num_blocks):
            dat = data[k:k + short_len - ecc_len + (0 if i < num_short else 1)]
            k += len(dat)
            ecc = _rs_remainder(dat, divisor)
            if i < num_short:
                dat = dat + [0]
            blocks.append(dat + ecc)
        result = []
        for i in range(len(blocks[0])):
            for j, blk in enumerate(blocks):
                if i != short_len - ecc_len or j >= num_short:
                    result.append(blk[i])
        return result

    def _set_function(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self.is_function[y][x] = True

    def _draw_function_patterns(self) -> None:
        n = self.size
        for i in range(n):
            self._set_function(6, i, i % 2 == 0)
            self._set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        dist = max(abs(dx), abs(dy))
                        self._set_function(x, y, dist not in (2, 4))
        pos = _alignment_positions(self.version)
        last = len(pos) - 1
        for i, a in enumerate(pos):
            for j, b in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set_function(a + dx, b + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format_bits(0)
        self._draw_version()

    def _draw_format_bits(self, mask: int) -> None:
        data = _FORMAT_BITS[self.ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        n = self.size
        bit = lambda i: (bits >> i) & 1 != 0  # noqa: E731
        for i in range(6):
            self._set_function(8, i, bit(i))
        self._set_function(8, 7, bit(6))
        self._set_function(8, 8, bit(7))
        self._set_function(7, 8, bit(8))
        for i in range(9, 15):
            self._set_function(14 - i, 8, bit(i))
        for i in range(8):
            self._set_function(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set_function(8, n - 15 + i, bit(i))
        self._set_function(8, n - 8, True)

    def _draw_version(self) -> None:
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 != 0
            a, b = self.size - 11 + i % 3, i // 3
            self._set_function(a, b, dark)
            self._set_function(b, a, dark)

    def _draw_codewords(self, data: list[int]) -> None:
        i = 0
        n = self.size
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = n - 1 - vert if upward else vert
                    if not self.is_function[y][x] and i < len(data) * 8:
                        self.modules[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 != 0
                        i += 1
            right -= 2

    def _apply_mask(self, mask: int) -> None:
        fns = (
            lambda x, y: (x + y) % 2 == 0,
            lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        )
        f = fns[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.is_function[y][x] and f(x, y):
                    self.modules[y][x] = not self.modules[y][x]

    def _penalty(self) -> int:
        n, m = self.size, self.modules
        result = 0
        lines = [m[y] for y in range(n)] + [[m[y][x] for y in range(n)] for x in range(n)]
        for line in lines:
            run, prev = 0, None
            for c in line:
                if c == prev:
                    run += 1
                    if run == 5:
                        result += 3
                    elif run > 5:
                        result += 1
                else:
                    prev, run = c, 1
            s = "".join("1" if c else "0" for c in line)
            for pat in ("10111010000", "00001011101"):
                start = s.find(pat)
                while start != -1:
                    result += 40
                    start = s.find(pat, start + 1)
        for y in range(n - 1):
            for x in range(n - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    result += 3
        dark = sum(sum(row) for row in m)
        total = n * n
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        result += max(0, k) * 10
        return result

    # ---- output ------------------------------------------------------------------------------------
    def svg(self, border: int = 4, title: str = "QR code") -> str:
        parts = []
        for y in range(self.size):
            for x in range(self.size):
                if self.modules[y][x]:
                    parts.append(f"M{x + border},{y + border}h1v1h-1z")
        dim = self.size + border * 2
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" shape-rendering="crispEdges" '
                f'role="img" aria-label="{title}"><rect width="100%" height="100%" fill="#fff"/>'
                f'<path d="{" ".join(parts)}" fill="#000"/></svg>')


# ------------------------------------------------------------------ styled output (share kits, print)
def best(text: str, ecl: str = "M") -> QrCode:
    """The strongest error correction that fits, starting at `ecl` (H lets a logo cover the centre)."""
    order = "HQML"
    for level in order[order.index(ecl):]:
        try:
            return QrCode(text.encode("utf-8"), level)
        except ValueError:
            continue
    raise ValueError("Text too long for a QR code here")


def _finder(q: QrCode, x: int, y: int) -> bool:
    n = q.size
    return (x < 7 and y < 7) or (x >= n - 7 and y < 7) or (x < 7 and y >= n - 7)


def logo_box(q: QrCode, border: int) -> tuple[float, float]:
    """(x, size) in modules of the centre square kept clear for a logo (about 22% of the code at level H)."""
    frac = {"H": 0.24, "Q": 0.18, "M": 0.12, "L": 0.0}[q.ecl]
    size = round(q.size * frac)
    size += (q.size - size) % 2  # keep it centred on whole modules
    return border + (q.size - size) / 2, size


def styled_svg(q: QrCode, *, fg: str = "#000000", bg: str = "#ffffff", border: int = 4, style: str = "square",
               logo_uri: str | None = None, title: str = "QR code") -> str:
    n = q.size
    lx, ls = logo_box(q, border) if logo_uri else (0, 0)
    clear = lambda x, y: logo_uri and lx <= x + border < lx + ls and lx <= y + border < lx + ls  # noqa: E731
    parts, dots = [], []
    for y in range(n):
        for x in range(n):
            if not q.modules[y][x] or clear(x, y):
                continue
            if style == "dots" and not _finder(q, x, y):
                dots.append(f'<circle cx="{x + border + .5}" cy="{y + border + .5}" r=".46"/>')
            else:
                parts.append(f"M{x + border},{y + border}h1v1h-1z")
    dim = n + border * 2
    logo = ""
    if logo_uri:
        pad = ls * 0.1
        logo = (f'<rect x="{lx - pad / 2:.2f}" y="{lx - pad / 2:.2f}" width="{ls + pad:.2f}" height="{ls + pad:.2f}" rx="{ls * .18:.2f}" fill="{bg}"/>'
                f'<image href="{logo_uri}" x="{lx + pad:.2f}" y="{lx + pad:.2f}" width="{ls - 2 * pad:.2f}" height="{ls - 2 * pad:.2f}" '
                'preserveAspectRatio="xMidYMid meet"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" role="img" aria-label="{title}">'
            f'<rect width="100%" height="100%" fill="{bg}"/><g fill="{fg}" shape-rendering="crispEdges"><path d="{" ".join(parts)}"/></g>'
            f'<g fill="{fg}">{"".join(dots)}</g>{logo}</svg>')


def styled_png(q: QrCode, *, px: int = 1024, fg: str = "#000000", bg: str = "#ffffff", border: int = 4,
               style: str = "square", logo_png: bytes | None = None) -> bytes:
    import io

    from PIL import Image, ImageDraw

    dim = q.size + border * 2
    scale = max(4, px // dim)
    S = dim * scale
    img = Image.new("RGBA", (S, S), bg)
    d = ImageDraw.Draw(img)
    lx, ls = logo_box(q, border) if logo_png else (0, 0)
    for y in range(q.size):
        for x in range(q.size):
            if not q.modules[y][x]:
                continue
            X, Y = (x + border), (y + border)
            if logo_png and lx <= X < lx + ls and lx <= Y < lx + ls:
                continue
            box = [X * scale, Y * scale, (X + 1) * scale - 1, (Y + 1) * scale - 1]
            if style == "dots" and not _finder(q, x, y):
                m = scale * 0.06
                d.ellipse([box[0] + m, box[1] + m, box[2] - m, box[3] - m], fill=fg)
            else:
                d.rectangle(box, fill=fg)
    if logo_png:
        pad = ls * 0.1 * scale
        x0 = lx * scale
        size = ls * scale
        d.rounded_rectangle([x0 - pad / 2, x0 - pad / 2, x0 + size + pad / 2, x0 + size + pad / 2], radius=size * 0.18, fill=bg)
        try:
            lg = Image.open(io.BytesIO(logo_png)).convert("RGBA")
            inner = int(size - 2 * pad)
            lg.thumbnail((inner, inner), Image.LANCZOS)
            img.alpha_composite(lg, (int(x0 + (size - lg.width) / 2), int(x0 + (size - lg.height) / 2)))
        except Exception:  # noqa: BLE001 - a bad logo file just leaves the centre plain
            pass
    if S != px:
        img = img.resize((px, px), Image.NEAREST)
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def svg(text: str) -> str | None:
    """SVG markup of a QR code for `text` (level M, or L when long); None when it doesn't fit (over ~270 bytes)."""
    for ecl in ("M", "L"):
        try:
            return QrCode(text.encode("utf-8"), ecl).svg()
        except ValueError:
            continue
    return None
