"""Animated and video templates.

`animated_text` uses CSS animations only (no JS timers) so media_render can pause every animation and
seek it frame by frame, which makes the output deterministic. `video_caption_overlay` renders a
transparent PNG laid over a video scene.
"""

from __future__ import annotations

from app.services.media_templates.base import Page, as_list, esc, plain, rich, size_for

MOTION_CSS = """
@keyframes rise{from{transform:translateY(108%);opacity:0}to{transform:none;opacity:1}}
@keyframes fadein{from{opacity:0;transform:translateY(calc(var(--u)*2))}to{opacity:1;transform:none}}
@keyframes sweep{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes out{from{opacity:1;transform:none}to{opacity:0;transform:translateY(calc(var(--u)*-3))}}
@keyframes logoin{from{opacity:0;transform:scale(.86)}to{opacity:1;transform:none}}
@keyframes meshdrift{0%{transform:translate(0,0) scale(1)}50%{transform:translate(-4%,3%) scale(1.07)}100%{transform:translate(3%,-2%) scale(1.03)}}
@keyframes glow{0%{opacity:.35}50%{opacity:.7}100%{opacity:.35}}
.lines{display:flex;flex-direction:column;gap:calc(var(--u)*1.2)}
.line{overflow:hidden;padding-bottom:.08em}
.line .in{display:inline-block}
.uline{height:calc(var(--u)*.7);width:calc(var(--u)*16);border-radius:999px;transform-origin:left center;
  background:linear-gradient(90deg,var(--accent),var(--accent2));margin-top:calc(var(--u)*3.4)}
.outro{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:calc(var(--u)*3);text-align:center}
.outro .chip{width:calc(var(--u)*22);height:calc(var(--u)*22);border-radius:calc(var(--u)*5.5)}
.outro .chip .mono{font-size:calc(var(--u)*8)}
.outro .oname{font-weight:800;font-size:calc(var(--u)*5);letter-spacing:-.02em;color:var(--fg)}
.outro .site{font-size:calc(var(--u)*2.8)}
"""


def animated_text(p: Page, f: dict, photo: str | None) -> str:
    dur = float(f.get("duration") or 6)
    lines = as_list(f.get("lines"), 5)
    if not lines:
        lines = [x for x in [f.get("headline"), f.get("body")] if x]
    lines = lines or [p.brand.name]
    eyebrow = f.get("eyebrow") or ""
    cta = f.get("cta") or ""
    longest = max(len(plain(x)) for x in lines)
    first = size_for(lines[0], 8.6 if not p.wide else 6.8, soft=26, floor=4.6)
    rest = min(first * 0.72, size_for(" ".join(lines[1:]) if len(lines) > 1 else "", 5.6, soft=60, floor=3.4))
    outro_at = max(1.8, dur - 1.7)
    ease = "cubic-bezier(.2,.75,.2,1)"
    out_anim = f"out .45s ease-in {outro_at:.2f}s both"
    parts = []
    t = 0.35
    if eyebrow:
        parts.append(f"<span class='eyebrow' style='animation:fadein .6s {ease} {t:.2f}s both'>{esc(eyebrow)}</span>"
                     "<div style='height:calc(var(--u)*2.6)'></div>")
        t += 0.3
    step = min(0.45, max(0.18, (outro_at - 1.6 - t) / max(1, len(lines))))
    for i, ln in enumerate(lines):
        size = first if i == 0 else rest
        weight = 800 if i == 0 else 600
        color = "var(--fg)" if i == 0 else "var(--fg-soft)"
        parts.append(
            f"<div class='line headline' data-fit style='font-size:calc(var(--u)*{size:.2f});font-weight:{weight};color:{color};"
            f"letter-spacing:{'-.03em' if i == 0 else '-.012em'}'><span class='in' style='animation:rise .8s {ease} {t:.2f}s both'>"
            f"{rich(ln)}</span></div>")
        t += step
    parts.append(f"<div class='uline' style='animation:sweep .9s {ease} {t:.2f}s both'></div>")
    if cta:
        parts.append(f"<div style='margin-top:calc(var(--u)*3.4);animation:fadein .6s {ease} {t + 0.25:.2f}s both'>"
                     f"<span class='pill'>{esc(cta)} →</span></div>")
    site = p.brand.website or p.brand.handle or ""
    logo = p.brandmark(show_name=False)
    outro = (f"<div class='layer outro' style='animation:logoin .7s {ease} {outro_at + 0.25:.2f}s both'>{logo}"
             f"<div class='oname'>{esc(p.brand.name)}</div>" + (f"<div class='site'>{esc(site)}</div>" if site else "") + "</div>")
    css = MOTION_CSS + f":root{{--dur:{dur:.2f}s}} .content{{justify-content:center}}"
    _ = longest
    return p.document(
        f"<div class='canvas'>{p.background(photo, ''.join(lines), animated=True)}"
        f"<div class='frame' style='animation:{out_anim}'><div class='top' style='animation:fadein .6s {ease} .1s both'>{p.brandmark()}</div>"
        f"<div class='content' data-fitbox><div class='lines'>{''.join(parts)}</div></div>"
        f"<div class='footer' style='animation:fadein .6s {ease} .5s both'><span class='rule'></span>"
        + (f"<span class='site'>{esc(site)}</span>" if site else "") + f"</div></div>{outro}</div>",
        css, plain(lines[0]))


def video_caption_overlay(p: Page, f: dict, photo: str | None = None) -> str:
    caption = f.get("caption") or ""
    title = f.get("title") or ""
    index = int(f.get("index") or 0)
    total = int(f.get("total") or 0)
    cs = size_for(caption, 5.6, soft=60, floor=3.6)
    css = """
.cap{position:absolute;left:var(--px);right:var(--px);bottom:var(--pb);display:flex;justify-content:center}
.capbox{max-height:calc(var(--h)*0.3);overflow:hidden;background:rgba(var(--dark-rgb),.80);border:1px solid rgba(255,255,255,.12);
  border-radius:calc(var(--u)*2.6);padding:calc(var(--u)*2.2) calc(var(--u)*3.2);box-shadow:0 calc(var(--u)*1) calc(var(--u)*4) rgba(0,0,0,.35)}
.captxt{font-weight:750;line-height:1.24;letter-spacing:-.01em;color:#fff;text-align:center;overflow-wrap:break-word}
.captxt .hl{background:none;color:#22d3ee;-webkit-text-fill-color:#22d3ee}
.head{position:absolute;left:var(--px);right:var(--px);top:var(--pt);display:flex;align-items:center;gap:calc(var(--u)*2)}
.head .chip{width:calc(var(--u)*8);height:calc(var(--u)*8)}
.ttl{font-weight:700;font-size:calc(var(--u)*3);color:#fff;background:rgba(var(--dark-rgb),.72);border-radius:999px;
  padding:calc(var(--u)*1) calc(var(--u)*2.4);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.bar{position:absolute;left:var(--px);right:var(--px);top:calc(var(--pt) - var(--u)*3.2);display:flex;gap:calc(var(--u)*.8)}
.bar i{flex:1;height:calc(var(--u)*.6);border-radius:999px;background:rgba(255,255,255,.35)}
.bar i.on{background:#fff}
"""
    bar = ""
    if total > 1:
        bar = "<div class='bar'>" + "".join(f"<i class='{'on' if i < index else ''}'></i>" for i in range(min(total, 12))) + "</div>"
    head = (f"<div class='head'>{p.brandmark(show_name=False)}"
            + (f"<span class='ttl'>{esc(title)}</span>" if title else "") + "</div>")
    cap = (f"<div class='cap'><div class='capbox' data-fitbox><div class='captxt' data-fit style='font-size:calc(var(--u)*{cs:.2f})'>"
           f"{rich(caption)}</div></div></div>") if caption else ""
    return p.document(f"<div class='canvas'>{bar}{head}{cap}</div>", css, plain(caption))
