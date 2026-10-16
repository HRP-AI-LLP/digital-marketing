"""Automatic layout choice per post: content shape (stat, quote, list, steps, compare, event, hiring, launch,
product, occasion), text length, platform format, with variety (recently used layouts are penalised)."""

from __future__ import annotations

import re

from app.services.media_templates.base import as_list, plain
from app.services.media_templates.occasions import match_occasion

STATIC_LAYOUTS = ("photo_headline", "hook_bold", "quote_card", "stat_card", "data_chart", "feature_spotlight", "tip_card",
                  "steps_card", "myth_fact", "announcement", "event_card", "testimonial", "logo_wall", "hiring", "animated_text")

_RX = {
    "hiring": r"\b(we('| a)re hiring|now hiring|join (our|the) team|open (role|position)s?|vacanc|apply now|job opening|careers?)\b",
    "event": r"\b(webinar|workshop|meetup|live session|register|summit|conference|join us (on|at|live)|masterclass|ama)\b",
    "launch": r"\b(launch|launching|introducing|announc|now live|now available|just shipped|new feature|release[sd]?|unveil|milestone)\b",
    "compare": r"\b(myth|fact|before|after|vs\.?|versus|old way|new way|instead of|stop doing)\b",
    "steps": r"\b(step[s ]|how to|how-to|guide|process|in \d+ steps|workflow|set ?up|get started)\b",
    "product": r"\b(hrms|dashboard|feature|product|platform|app|module|integration|demo|screen|chatbot|agent|portal)\b",
    "testimonial": r"\b(client|customer) (story|stories|says|review)|testimonial|case study\b",
    "proof": r"\b(trusted by|our clients|partners|customers include)\b",
    "data": r"\b(\d+(\.\d+)?\s?%|\d+x\b|survey|report|data|study|statistic|percent)\b",
}


def signals(meta: dict, text: str = "", title: str = "") -> dict:
    t = plain(f"{title}\n{text}\n{meta.get('headline') or ''}\n{meta.get('image_brief') or ''}").lower()
    v = meta.get("visual") if isinstance(meta.get("visual"), dict) else {}
    bullets = as_list(meta.get("bullets") or v.get("bullets"))
    return {
        "stat": bool(meta.get("stat") or v.get("stat")),
        "quote": bool(meta.get("quote") or v.get("quote")),
        "bullets": len(bullets),
        "kv": sum(1 for b in bullets if re.search(r"[:–—-]\s*[^\d]*\d", b)),
        "compare": bool((meta.get("left") or v.get("left")) and (meta.get("right") or v.get("right"))) or bool(re.search(_RX["compare"], t)),
        **{k: bool(re.search(rx, t)) for k, rx in _RX.items() if k != "compare"},
        "len": len(plain(meta.get("headline") or title or text)),
        "names": len(as_list(meta.get("names") or v.get("names"))),
        "date": bool(meta.get("date") or v.get("date")),
    }


def score(sig: dict, photo_ok: bool) -> dict[str, float]:
    s: dict[str, float] = {n: 0.0 for n in STATIC_LAYOUTS}
    s["photo_headline"] = 2.0 + (1.0 if photo_ok else -0.5)
    s["hook_bold"] = 2.2 if sig["len"] <= 70 else 1.2
    s["animated_text"] = 0.5
    if sig["stat"]:
        s["stat_card"] = 9
    if sig["kv"] >= 2:
        s["data_chart"] = 9.5
    elif sig["data"] and sig["stat"]:
        s["data_chart"] = 3
    if sig["quote"]:
        s["quote_card"] = 8.5
        if sig["testimonial"]:
            s["testimonial"] = 9
    if sig["bullets"] >= 2:
        s["tip_card"] = 6 + (0 if sig["steps"] else 1)
        s["steps_card"] = 6.5 + (2 if sig["steps"] else 0)
        if sig["product"]:
            s["feature_spotlight"] = 6.8
        if sig["hiring"]:
            s["hiring"] = 9.5
    if sig["compare"]:
        s["myth_fact"] = 8 if sig["compare"] is True and sig.get("bullets", 0) == 0 else 5
    if sig["event"] or (sig["date"] and not sig["launch"]):
        s["event_card"] = 8.8
    if sig["launch"]:
        s["announcement"] = 7.5
    if sig["product"]:
        s["feature_spotlight"] = max(s["feature_spotlight"], 5.0)
    if sig["hiring"]:
        s["hiring"] = max(s["hiring"], 8.0)
    if sig["names"] >= 3 or sig["proof"]:
        s["logo_wall"] = 7 if sig["names"] >= 3 else 2
    return s


def choose(meta: dict, *, text: str = "", title: str = "", kind: str = "image", photo_ok: bool = False,
           recent: list[str] | None = None, exclude: tuple[str, ...] = ()) -> str:
    """Pick a layout. An explicit meta['template'] (or visual.layout from the writer) always wins."""
    from app.services.media_templates import TEMPLATES

    for cand in (meta.get("template"), (meta.get("visual") or {}).get("layout") if isinstance(meta.get("visual"), dict) else None):
        if cand in TEMPLATES and kind in TEMPLATES[cand].kinds and cand not in exclude:
            return cand
    occ = match_occasion(meta.get("occasion"), None if meta.get("occasion") else f"{title}\n{text}")
    if occ and f"greeting_{occ}" in TEMPLATES and f"greeting_{occ}" not in exclude:
        return f"greeting_{occ}"
    sig = signals(meta, text, title)
    sc = score(sig, photo_ok)
    if kind in ("gif", "animation"):
        sc["animated_text"] += 2.6  # kinetic type is the default motion piece unless the content has a stronger shape
    recent = [r for r in (recent or []) if r]
    for i, r in enumerate(recent[:3]):
        if r in sc:
            sc[r] -= (2.2, 1.2, .6)[i]  # variety: avoid repeating the last layouts
    ranked = sorted(((v, n) for n, v in sc.items() if n not in exclude and kind in TEMPLATES[n].kinds), reverse=True)
    return ranked[0][1] if ranked else "photo_headline"


def ranked(meta: dict, *, text: str = "", title: str = "", kind: str = "image", photo_ok: bool = False, n: int = 3) -> list[str]:
    """Top-n distinct layouts for 'Generate variants'."""
    out: list[str] = []
    for _ in range(n):
        out.append(choose(meta, text=text, title=title, kind=kind, photo_ok=photo_ok, exclude=tuple(out)))
    return out
