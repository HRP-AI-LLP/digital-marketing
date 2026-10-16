"""Shared building blocks for branded HTML templates (rendered to PNG/MP4/GIF by media_render).

Templates are plain Python functions returning a full HTML document. Rules:
- every user-supplied string goes through `rich()` / `esc()` (HTML-escaped);
- sizes scale with `--u` (1% of the shorter side) so one template works for 1:1, 4:5, 9:16, 1.91:1;
- text blocks marked `data-fit` shrink until they fit their `data-fitbox` (see FIT_JS), and the page sets
  `body[data-ready]` once fonts are loaded and fitting is done;
- colours come from the brand (hrpai.in palette by default) with contrast enforced (>= 4.5:1 body text).
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "media_assets"

# hrpai.in palette (PLAN D24)
DEFAULT_COLORS = {
    "primary": "#075492",
    "primary_light": "#0f8dde",
    "secondary": "#06a6c7",
    "secondary_light": "#22d3ee",
    "dark": "#062f59",
    "light": "#f8fafc",
    "ink": "#0f172a",
}


# ----------------------------------------------------------------------------- colour helpers
def _hex(c: str | None, default: str) -> str:
    c = (c or "").strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", c):
        return "#" + c.lstrip("#").lower()
    if re.fullmatch(r"#?[0-9a-fA-F]{3}", c):
        c = c.lstrip("#")
        return "#" + "".join(ch * 2 for ch in c).lower()
    return default


def rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def rgb_str(c: str) -> str:
    r, g, b = rgb(c)
    return f"{r},{g},{b}"


def _lum(c: str) -> float:
    def ch(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = rgb(c)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, t: float) -> str:
    ra, ga, ba = rgb(a)
    rb, gb, bb = rgb(b)
    return "#{:02x}{:02x}{:02x}".format(round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def ensure_contrast(color: str, bg: str, ratio: float = 4.5) -> str:
    """Nudge `color` toward white or black until it reaches `ratio` against `bg`."""
    if contrast(color, bg) >= ratio:
        return color
    target = "#ffffff" if _lum(bg) < 0.4 else "#000000"
    for i in range(1, 21):
        c = mix(color, target, i / 20)
        if contrast(c, bg) >= ratio:
            return c
    return target


# ----------------------------------------------------------------------------- brand
@dataclass
class BrandStyle:
    name: str = "HRP AI"
    website: str | None = "hrpai.in"
    handle: str | None = None
    logo_uri: str | None = None  # data: URI
    colors: dict = field(default_factory=lambda: dict(DEFAULT_COLORS))
    heading_font: str = "Plus Jakarta Sans"
    body_font: str = "Inter"

    @classmethod
    def from_profile(cls, colors: dict | None = None, fonts: dict | None = None, **kw) -> BrandStyle:
        colors = colors or {}
        c = dict(DEFAULT_COLORS)
        c["primary"] = _hex(colors.get("primary"), c["primary"])
        c["secondary"] = _hex(colors.get("secondary"), c["secondary"])
        c["dark"] = _hex(colors.get("dark"), c["dark"])
        c["light"] = _hex(colors.get("light"), c["light"])
        if colors.get("primary"):
            c["primary_light"] = _hex(colors.get("primary_light"), mix(c["primary"], "#ffffff", 0.25))
        if colors.get("secondary"):
            c["secondary_light"] = _hex(colors.get("secondary_light"), mix(c["secondary"], "#ffffff", 0.3))
        fonts = fonts or {}
        safe = lambda f: re.sub(r"[^A-Za-z0-9 \-]", "", str(f or ""))[:40].strip() or "Inter"  # noqa: E731
        return cls(colors=c, heading_font=safe(fonts.get("heading") or fonts.get("primary") or "Plus Jakarta Sans"),
                   body_font=safe(fonts.get("body") or fonts.get("secondary")), **kw)


def default_logo_uri() -> str:
    return data_uri((ASSETS / "logo-mark.png").read_bytes(), "image/png")


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


@lru_cache(maxsize=1)
def _font_face() -> str:
    p = ASSETS / "fonts" / "inter-latin-wght-normal.woff2"
    if not p.exists():
        return ""
    b64 = base64.b64encode(p.read_bytes()).decode()
    return ("@font-face{font-family:'Inter';font-style:normal;font-display:block;font-weight:100 900;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}")


# ----------------------------------------------------------------------------- text helpers
def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


_HL = re.compile(r"\*([^*\n]{1,80})\*")


def rich(s: object) -> str:
    """Escape, keep line breaks, and turn *words* into an accent highlight."""
    t = esc(s).replace("\r", "")
    t = _HL.sub(r'<em class="hl">\1</em>', t)
    return t.replace("\n", "<br>")


def plain(s: object) -> str:
    return re.sub(r"\*([^*\n]{1,80})\*", r"\1", str(s or "")).strip()


def size_for(text: str, base: float, *, soft: int, floor: float) -> float:
    """Initial font size (in --u) that shrinks with text length; the in-page fitter refines it."""
    n = len(plain(text))
    if n <= soft:
        return base
    return max(floor, base * (soft / n) ** 0.5)


def as_list(v, limit: int = 8) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        items = [x.strip(" -•\t") for x in v.split("\n")]
    else:
        items = [str(x.get("text") if isinstance(x, dict) else x).strip() for x in v]
    return [x for x in items if x][:limit]


def seed(*parts: object) -> int:
    return int(hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


# ----------------------------------------------------------------------------- page scaffolding
FIT_JS = """
(function(){
  function overflow(box){return box.scrollHeight>box.clientHeight+1||box.scrollWidth>box.clientWidth+1;}
  function fitBox(box){
    var els=[].slice.call(box.querySelectorAll('[data-fit]'));
    if(!els.length) return;
    var sizes=els.map(function(e){return parseFloat(getComputedStyle(e).fontSize);});
    var mins=els.map(function(e,i){return parseFloat(e.dataset.min||sizes[i]*0.45);});
    var k=1, n=0;
    while(overflow(box)&&n<60){
      k*=0.95; n++; var any=false;
      els.forEach(function(e,i){var s=Math.max(mins[i],sizes[i]*k); if(s<parseFloat(e.style.fontSize||sizes[i])-0.01){any=true;} e.style.fontSize=s+'px';});
      if(!any) break;
    }
    // single words wider than the box (long URLs, German compounds)
    els.forEach(function(e){var s=parseFloat(getComputedStyle(e).fontSize),m=0;while(e.scrollWidth>e.clientWidth+1&&m<30){s*=0.94;e.style.fontSize=s+'px';m++;}});
  }
  function run(){
    document.querySelectorAll('[data-fitbox]').forEach(fitBox);
    document.body.setAttribute('data-ready','1');
  }
  var go=function(){(document.fonts&&document.fonts.ready?document.fonts.ready:Promise.resolve()).then(function(){requestAnimationFrame(run);});};
  if(document.readyState==='complete') go(); else window.addEventListener('load',go);
})();
"""

BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:var(--w);height:var(--h);overflow:hidden}
body{font-family:var(--font-body);color:var(--fg);background:var(--bg0);-webkit-font-smoothing:antialiased;
  text-rendering:geometricPrecision;font-feature-settings:"cv11","ss01","tnum" 0;font-optical-sizing:auto}
.canvas{position:relative;width:var(--w);height:var(--h);overflow:hidden}
.layer{position:absolute;inset:0}
.photo{background-size:cover;background-position:center}
.scrim{background:linear-gradient(180deg,rgba(var(--dark-rgb),.30) 0%,rgba(var(--dark-rgb),.50) 38%,rgba(var(--dark-rgb),.90) 72%,rgba(var(--dark-rgb),.96) 100%)}
.scrim-top{background:linear-gradient(180deg,rgba(var(--dark-rgb),.55) 0%,rgba(var(--dark-rgb),0) 30%)}
.grid{background-image:linear-gradient(rgba(var(--grid-rgb),.06) 1px,transparent 1px),linear-gradient(90deg,rgba(var(--grid-rgb),.06) 1px,transparent 1px);
  background-size:calc(var(--u)*7) calc(var(--u)*7);-webkit-mask-image:radial-gradient(ellipse at 78% 18%,#000 0%,transparent 62%);mask-image:radial-gradient(ellipse at 78% 18%,#000 0%,transparent 62%)}
.circuit{position:absolute;opacity:.5;pointer-events:none}
.frame{position:absolute;inset:0;padding:var(--pt) var(--px) var(--pb);display:flex;flex-direction:column}
.top{display:flex;align-items:center;justify-content:space-between;gap:calc(var(--u)*2);min-height:calc(var(--u)*7)}
.content{flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.grow{flex:1;min-height:calc(var(--u)*2)}
.eyebrow{display:inline-flex;align-self:flex-start;align-items:center;gap:calc(var(--u)*1);font-weight:650;
  font-size:calc(var(--u)*2.5);letter-spacing:.01em;color:var(--accent-text);background:var(--chip-bg);
  border:1px solid var(--chip-line);padding:calc(var(--u)*.8) calc(var(--u)*2);border-radius:999px;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.eyebrow::before{content:"";width:calc(var(--u)*1.1);height:calc(var(--u)*1.1);border-radius:50%;background:var(--accent);flex:none}
.headline{font-family:var(--font-head);font-weight:800;letter-spacing:-.028em;line-height:1.05;color:var(--fg);overflow-wrap:break-word}
.hl{font-style:normal;background:linear-gradient(92deg,var(--accent) 0%,var(--accent2) 100%);-webkit-background-clip:text;background-clip:text;color:transparent;
  -webkit-box-decoration-break:clone;box-decoration-break:clone}
.body{font-weight:420;line-height:1.45;color:var(--fg-soft);overflow-wrap:break-word}
.footer{display:flex;align-items:center;justify-content:space-between;gap:calc(var(--u)*2);padding-top:calc(var(--u)*3)}
.brandmark{display:flex;align-items:center;gap:calc(var(--u)*1.6);min-width:0}
.chip{flex:none;width:calc(var(--u)*7);height:calc(var(--u)*7);border-radius:calc(var(--u)*1.8);background:#fff;display:grid;place-items:center;
  box-shadow:0 calc(var(--u)*.4) calc(var(--u)*1.6) rgba(2,12,27,.18)}
.chip img{width:88%;height:88%;object-fit:contain}
.chip .mono{font-weight:800;font-size:calc(var(--u)*3);color:var(--primary);letter-spacing:-.03em}
.bname{font-weight:700;font-size:calc(var(--u)*2.5);line-height:1.15;color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.site{font-weight:500;font-size:calc(var(--u)*2.2);color:var(--fg-soft);white-space:nowrap}
.pill{display:inline-flex;align-items:center;gap:calc(var(--u)*1.2);font-weight:700;font-size:calc(var(--u)*2.7);color:#fff;
  background:linear-gradient(92deg,var(--primary) 0%,var(--secondary-deep) 100%);padding:calc(var(--u)*1.5) calc(var(--u)*3);border-radius:999px;
  box-shadow:0 calc(var(--u)*.6) calc(var(--u)*2.2) rgba(var(--primary-rgb),.35);white-space:nowrap}
.rule{height:calc(var(--u)*.6);width:calc(var(--u)*12);border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent2))}
"""


@dataclass
class Page:
    width: int
    height: int
    brand: BrandStyle
    theme: str = "dark"  # dark | light
    transparent: bool = False

    @property
    def u(self) -> float:
        return min(self.width, self.height) / 100

    @property
    def tall(self) -> bool:
        return self.height / self.width >= 1.6

    @property
    def wide(self) -> bool:
        return self.width / self.height >= 1.5

    def vars(self) -> dict[str, str]:
        c = self.brand.colors
        u = self.u
        dark = c["dark"]
        if self.theme == "light":
            bg = c["light"]
            fg = ensure_contrast(c["dark"], bg, 7)
            soft = ensure_contrast(mix(c["ink"], "#64748b", 0.35), bg, 4.5)
            accent = ensure_contrast(c["primary"], bg, 3.2)
            accent2 = ensure_contrast(c["secondary"], bg, 3.2)
            accent_text = ensure_contrast(c["primary"], mix(bg, c["primary"], 0.08), 4.5)
            chip_bg, chip_line, grid = f"rgba({rgb_str(c['primary'])},.07)", f"rgba({rgb_str(c['primary'])},.18)", rgb_str(c["primary"])
        else:
            bg = dark
            fg = "#ffffff" if contrast("#ffffff", dark) >= 4.5 else c["ink"]
            soft = ensure_contrast(mix(fg, dark, 0.2), dark, 4.5)
            accent = ensure_contrast(c["secondary_light"], dark, 3.2)
            accent2 = ensure_contrast(mix(c["primary_light"], "#ffffff", 0.35), dark, 3.2)
            accent_text = ensure_contrast(c["secondary_light"], mix(dark, "#ffffff", 0.08), 4.5)
            chip_bg, chip_line, grid = "rgba(255,255,255,.08)", "rgba(255,255,255,.18)", "255,255,255"
        px = u * (6 if self.wide else 7.5)
        pt = pb = u * 7
        if self.tall:  # 9:16 safe areas: keep text out of the top/bottom 12%
            pt = pb = self.height * 0.12
        elif self.height / self.width >= 1.2:
            pt = pb = u * 8
        return {
            "--w": f"{self.width}px", "--h": f"{self.height}px", "--u": f"{u:.3f}px",
            "--px": f"{px:.1f}px", "--pt": f"{pt:.1f}px", "--pb": f"{pb:.1f}px",
            "--bg0": "transparent" if self.transparent else bg, "--fg": fg, "--fg-soft": soft,
            "--accent": accent, "--accent2": accent2, "--accent-text": accent_text,
            "--chip-bg": chip_bg, "--chip-line": chip_line, "--grid-rgb": grid,
            "--primary": c["primary"], "--primary-rgb": rgb_str(c["primary"]),
            "--secondary": c["secondary"], "--secondary-deep": mix(c["secondary"], "#000000", 0.22),
            "--dark": dark, "--dark-rgb": rgb_str(dark), "--light": c["light"],
            "--font-head": f"'{self.brand.heading_font}','Inter',system-ui,sans-serif",
            "--font-body": f"'{self.brand.body_font}','Inter',system-ui,sans-serif",
        }

    def document(self, body: str, css: str = "", title: str = "media") -> str:
        root = ";".join(f"{k}:{v}" for k, v in self.vars().items())
        bgcss = "html,body{background:transparent!important}" if self.transparent else ""
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{esc(title)}</title><style>{_font_face()}:root{{{root}}}{BASE_CSS}{bgcss}{css}</style></head>"
            f"<body>{body}<script>{FIT_JS}</script></body></html>"
        )

    # ---- reusable pieces
    def background(self, photo_uri: str | None, key: str = "", animated: bool = False) -> str:
        """Photo + scrim, or a generated brand mesh (deterministic per `key`)."""
        if photo_uri:
            return (f"<div class='layer photo' style=\"background-image:url('{photo_uri}')\"></div>"
                    "<div class='layer scrim'></div>")
        return self.mesh(key, animated=animated)

    def mesh(self, key: str = "", animated: bool = False) -> str:
        c = self.brand.colors
        s = seed(key, self.width, self.height)
        pos = [(s >> (i * 4)) % 100 for i in range(6)]
        if self.theme == "light":
            base = f"linear-gradient(160deg,{c['light']} 0%,{mix(c['light'], c['secondary_light'], .10)} 100%)"
            blobs = [
                f"radial-gradient(circle at {55 + pos[0] % 40}% {pos[1] % 30}%,rgba({rgb_str(c['secondary_light'])},.22) 0,transparent 42%)",
                f"radial-gradient(circle at {pos[2] % 30}% {70 + pos[3] % 30}%,rgba({rgb_str(c['primary_light'])},.16) 0,transparent 45%)",
            ]
        else:
            base = f"linear-gradient(150deg,{c['dark']} 0%,{mix(c['dark'], c['primary'], .55)} 100%)"
            blobs = [
                f"radial-gradient(circle at {60 + pos[0] % 40}% {pos[1] % 35}%,rgba({rgb_str(c['secondary'])},.45) 0,transparent 42%)",
                f"radial-gradient(circle at {pos[2] % 35}% {65 + pos[3] % 35}%,rgba({rgb_str(c['primary_light'])},.40) 0,transparent 48%)",
                f"radial-gradient(circle at {30 + pos[4] % 40}% {30 + pos[5] % 40}%,rgba({rgb_str(c['primary'])},.55) 0,transparent 60%)",
            ]
        anim = ";animation:meshdrift var(--dur,8s) ease-in-out both" if animated else ""
        mesh_css = f"background:{','.join(blobs)},{base}{anim}"
        return (f"<div class='layer' style='background:{base}'></div>"
                f"<div class='layer' style='inset:-18%'><div class='layer' style=\"{mesh_css}\"></div></div>"
                "<div class='layer grid'></div>" + self.circuit(s))

    def circuit(self, s: int) -> str:
        """Thin circuit lines with nodes, echoing the HRP AI mark. Decorative only."""
        u = self.u
        col = "rgba(255,255,255,.22)" if self.theme != "light" else f"rgba({rgb_str(self.brand.colors['primary'])},.16)"
        size = u * 34
        flip = "scaleX(-1)" if s % 2 else "none"
        r = u * 1.3
        sw = max(1.5, u * 0.28)
        svg = (f"<svg width='{size:.0f}' height='{size:.0f}' viewBox='0 0 100 100' style='transform:{flip}'>"
               f"<g fill='none' stroke='{col}' stroke-width='{sw * 100 / size:.2f}' stroke-linecap='round'>"
               "<path d='M100 18 H62 L40 40 V100'/><path d='M100 46 H78 L66 58 V100'/>"
               f"<circle cx='40' cy='40' r='{r * 100 / size:.2f}'/><circle cx='62' cy='18' r='{r * 100 / size:.2f}'/>"
               f"<circle cx='78' cy='46' r='{r * 100 / size:.2f}'/></g></svg>")
        side = "right:0" if s % 2 == 0 else "left:0"
        return f"<div class='circuit' style='top:0;{side}'>{svg}</div>"

    def brandmark(self, show_name: bool = True, show_site: bool = False) -> str:
        b = self.brand
        if b.logo_uri:
            chip = f"<span class='chip'><img alt='' src='{b.logo_uri}'></span>"
        else:
            initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", b.name)[:2]).upper() or "•"
            chip = f"<span class='chip'><span class='mono'>{esc(initials)}</span></span>"
        txt = ""
        if show_name or show_site:
            name = f"<div class='bname'>{esc(b.name)}</div>" if show_name else ""
            site = f"<div class='site'>{esc(b.website or b.handle or '')}</div>" if show_site and (b.website or b.handle) else ""
            txt = f"<div style='min-width:0'>{name}{site}</div>"
        return f"<div class='brandmark'>{chip}{txt}</div>"

    def footer(self, right: str = "") -> str:
        site = self.brand.website or self.brand.handle
        right = right or (f"<span class='site'>{esc(site)}</span>" if site else "")
        return f"<div class='footer'>{self.brandmark()}{right}</div>"
