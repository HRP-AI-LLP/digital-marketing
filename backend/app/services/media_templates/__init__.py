"""Branded HTML templates and their registry (used by the Media studio UI and the media pipeline).

render_html(name, fields, width, height, brand, photo=None, theme=None, motion="still", duration=6) -> HTML string

Every template is animatable: `motion="intro"` (enter, hold, logo sting; MP4), `"loop"` (seamless GIF loop) or
`"still"` (PNG). The page implements the `window.__seek(t)` contract from runtime.py.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.services.media_templates import carousel, layouts, motion, occasions, three_d
from app.services.media_templates.base import (  # noqa: F401
    BrandStyle,
    Page,
    default_logo_uri,
)
from app.services.media_templates.kit import Kit

TEXT, AREA, LIST = "text", "textarea", "list"


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    label: str
    description: str
    kinds: tuple[str, ...]
    fields: tuple[tuple[str, str, str], ...]  # (name, label, type: text|textarea|list|number)
    fn: Callable[[Kit, dict, str | None], str]
    theme: str = "dark"
    photo: bool = True  # can sit on a background photo
    animated: bool = False  # designed primarily as motion
    group: str = "General"
    themes: tuple[str, ...] = ("dark", "light", "brand")
    sample: dict | None = None  # preview content for the gallery
    three_d: bool = False


IMG = ("image", "gif", "animation")
CAR = ("carousel", "document")
F_EYEBROW = ("eyebrow", "Small label", TEXT)
F_HEAD = ("headline", "Headline", AREA)
F_BODY = ("body", "Supporting line", AREA)
F_CTA = ("cta", "Button text", TEXT)

_T = TemplateSpec
TEMPLATES: dict[str, TemplateSpec] = {t.name: t for t in [
    _T("photo_headline", "Hook on photo", "Bold headline over an AI or stock photo (or a brand aurora).", IMG,
       (F_EYEBROW, F_HEAD, F_BODY, F_CTA), layouts.photo_headline, group="Hook",
       sample={"eyebrow": "HR automation", "headline": "Hire *faster* without cutting corners",
               "body": "AI screening that explains every shortlist.", "cta": "See how"}),
    _T("hook_bold", "Bold statement", "A big typographic hook. Best for opinions, questions and one-liners.", IMG,
       (F_EYEBROW, F_HEAD, F_BODY, F_CTA), layouts.hook_bold, group="Hook",
       sample={"eyebrow": "Hot take", "headline": "Your HR team isn't slow. Your *tools* are.",
               "body": "Five hours a week go to copy-paste work. That's fixable."}),
    _T("quote_card", "Quote", "A quote with the speaker's name, on a glass card.", IMG,
       (("quote", "Quote", AREA), ("author", "Name", TEXT), ("role", "Role or company", TEXT)), layouts.quote_card, group="Quote",
       sample={"quote": "The best HR teams don't work harder. They let *software* do the repetitive part.", "author": "Prashant Srivastava",
               "role": "Founder, HRP AI"}),
    _T("stat_card", "Big number", "One striking number with a chart drawn from it (ring for %, bars for ×, trend line).", IMG,
       (F_EYEBROW, ("stat", "Number", TEXT), ("stat_label", "What it means", AREA), ("body", "Context", AREA), ("source", "Source", TEXT),
        ("chart", "Chart (donut, bars, line, none)", TEXT)), layouts.stat_card, group="Data",
       sample={"eyebrow": "Time saved", "stat": "68%", "stat_label": "less time spent on payroll each month",
               "body": "Across teams that moved attendance, leave and payroll into one place.", "source": "HRP AI customer survey"}),
    _T("data_chart", "Data highlight", "A clean bar chart from lines like “2024: 71”.", IMG,
       (F_EYEBROW, F_HEAD, ("bullets", "Data (one “Label: value” per line)", LIST), ("body", "Takeaway", AREA), ("source", "Source", TEXT)),
       layouts.data_chart, theme="light", photo=False, group="Data",
       sample={"eyebrow": "Adoption", "headline": "AI in HR is going *mainstream*", "bullets": ["2022: 18%", "2023: 29%", "2024: 44%", "2025: 61%"],
               "body": "Share of mid-size firms using AI in at least one HR workflow.", "source": "Illustrative data"}),
    _T("feature_spotlight", "Product spotlight", "A device frame showing your screenshot (or a generic product UI) with key features.", IMG,
       (F_EYEBROW, F_HEAD, ("bullets", "Features (one per line)", LIST), F_BODY, ("device", "Device (browser or phone)", TEXT), F_CTA),
       layouts.feature_spotlight, group="Product",
       sample={"eyebrow": "HRP HRMS", "headline": "Payroll, leave and attendance in *one* place",
               "bullets": ["Payroll in minutes", "Self-service leave", "Live attendance"], "cta": "Book a demo"}),
    _T("tip_card", "Tips list", "A title with 3–5 numbered tips on cards.", IMG,
       (F_EYEBROW, ("headline", "Title", AREA), ("bullets", "Tips (one per line)", LIST)), layouts.tip_card, theme="light", photo=False,
       group="List", sample={"eyebrow": "Quick wins", "headline": "4 ways to cut *onboarding* time",
                             "bullets": ["Send offer letters with e-sign", "Pre-fill documents from the CV", "Automate IT access requests",
                                         "Schedule day-one check-ins"]}),
    _T("steps_card", "Step by step", "A how-to with numbered steps on a timeline.", IMG,
       (F_EYEBROW, F_HEAD, ("bullets", "Steps (“Title: detail”, one per line)", LIST)), layouts.steps_card, theme="light", photo=False,
       group="List", sample={"eyebrow": "How it works", "headline": "Go live in *three* steps",
                             "bullets": ["Import: upload your employee sheet", "Configure: set leave and pay rules", "Launch: invite your team"]}),
    _T("myth_fact", "Myth vs fact", "Two sides: myth/fact, before/after or old way/new way.", IMG,
       (F_EYEBROW, F_HEAD, ("left_label", "Left label", TEXT), ("left", "Left side", AREA), ("right_label", "Right label", TEXT),
        ("right", "Right side", AREA)), layouts.myth_fact, photo=False, group="Compare",
       sample={"eyebrow": "Myth busting", "headline": "AI will replace *recruiters*?", "left": "AI makes hiring decisions on its own.",
               "right": "AI shortlists and explains why. People decide."}),
    _T("announcement", "Launch / announcement", "News, launches and milestones with a glow and a call to action.", IMG,
       (F_EYEBROW, F_HEAD, ("body", "Details", AREA), ("date", "Date or place", TEXT), F_CTA), layouts.announcement, group="News",
       sample={"eyebrow": "Now live", "headline": "Meet the new *HRP AI* agent", "body": "It answers policy questions in seconds, in 12 languages.",
               "date": "Available today", "cta": "Try it free"}),
    _T("event_card", "Event / webinar", "Date tile, time, place, speaker and a register button.", IMG,
       (F_EYEBROW, F_HEAD, ("date", "Date (e.g. 24 Oct)", TEXT), ("time", "Time", TEXT), ("place", "Place", TEXT),
        ("speaker", "Speaker", TEXT), F_CTA), layouts.event_card, group="News",
       sample={"eyebrow": "Live webinar", "headline": "Building *RAG chatbots* your HR team trusts", "date": "24 Oct 2026",
               "time": "4:00 PM IST", "place": "Online · Zoom", "speaker": "Prashant Srivastava", "cta": "Register free"}),
    _T("testimonial", "Testimonial", "A client's words with rating, name and company.", IMG,
       (("quote", "Quote", AREA), ("author", "Name", TEXT), ("role", "Role", TEXT), ("company", "Company", TEXT)),
       layouts.testimonial, theme="light", photo=False, group="Quote",
       sample={"quote": "We closed payroll two days earlier in the first month, and nobody had to chase attendance sheets.",
               "author": "Ananya Rao", "role": "HR Lead", "company": "Client name"}),
    _T("logo_wall", "Client wall", "“Trusted by” wall of client or partner names.", IMG,
       (F_EYEBROW, F_HEAD, ("names", "Names (one per line)", LIST), F_BODY), layouts.logo_wall, theme="light", photo=False, group="Proof",
       sample={"headline": "Trusted by *growing* teams", "names": ["Northwind", "Lumen Labs", "Acme Retail", "Bluepeak", "Orbit Health", "Kite & Co"]}),
    _T("hiring", "We're hiring", "Role, location, type, perks and an apply button.", IMG,
       (F_EYEBROW, ("headline", "Role", TEXT), ("location", "Location", TEXT), ("job_type", "Type", TEXT), ("experience", "Experience", TEXT),
        ("perks", "Perks (one per line)", LIST), F_CTA), layouts.hiring, group="Hiring",
       sample={"headline": "Senior *ML* Engineer", "location": "Bengaluru · Hybrid", "job_type": "Full-time", "experience": "4+ years",
               "perks": ["Ship AI to real users", "Learning budget", "Flexible hours", "Health cover for family"], "cta": "Apply now"}),
    _T("carousel_cover", "Carousel cover", "First slide: the hook.", CAR + ("image",),
       (F_EYEBROW, F_HEAD, ("body", "Subtitle", AREA)), carousel.carousel_cover, group="Carousel",
       sample={"eyebrow": "Guide", "headline": "7 HR tasks you should *automate* this year", "body": "Swipe for the playbook.", "total": 9}),
    _T("carousel_slide", "Carousel slide", "Content slide; becomes a stat or checklist slide automatically.", CAR,
       (("heading", "Heading", AREA), ("body", "Text", AREA), ("bullets", "Points (one per line)", LIST)), carousel.carousel_slide,
       theme="light", photo=False, group="Carousel",
       sample={"heading": "Automate leave approvals", "body": "Managers approve from email or chat; balances update instantly.", "index": 3, "total": 9, "number": 2}),
    _T("carousel_cta", "Carousel closing slide", "Last slide: follow, save, visit.", CAR, (("heading", "Heading", TEXT), ("body", "Text", AREA), F_CTA),
       carousel.carousel_cta, theme="brand", group="Carousel",
       sample={"heading": "Want the full checklist?", "body": "Follow for weekly HR automation playbooks.", "cta": "Visit hrpai.in", "index": 9, "total": 9}),
    _T("animated_text", "Kinetic text", "Lines reveal word by word over moving brand colours, then the logo.", IMG,
       (F_EYEBROW, ("lines", "Lines (one per line)", LIST), F_CTA), layouts.animated_text, animated=True, group="Motion",
       sample={"eyebrow": "HRP AI", "lines": ["Your HR team, *amplified*", "AI that drafts, screens and schedules", "So people can focus on people"]}),
    _T("video_caption_overlay", "Video caption", "Caption and logo laid over a video scene.", ("video",),
       (("title", "Title", TEXT), ("caption", "Caption", AREA)), motion.video_caption_overlay, photo=False, group="Video", themes=("dark",),
       sample={"title": "Quick tip", "caption": "Save *hours* every week"}),
    *occasions.specs(_T),
    *three_d.specs(_T),
]}


def render_html(name: str, fields: dict, width: int, height: int, brand: BrandStyle, photo: str | None = None,
                theme: str | None = None, transparent: bool | None = None, *, motion: str = "still", duration: float = 6.0,
                drift: bool | None = None) -> str:
    spec = TEMPLATES.get(name)
    if spec is None:
        raise KeyError(f"Unknown template: {name}")
    th = theme or (fields or {}).get("theme") or spec.theme
    if photo and spec.photo:
        th = "dark"  # text over photos always sits on a dark scrim
    if th not in ("dark", "light", "brand"):
        th = spec.theme
    k = Kit(width, height, brand, theme=th, key=f"{name}|{(fields or {}).get('headline') or ''}",
            transparent=spec.name == "video_caption_overlay" if transparent is None else transparent,
            motion=motion if motion in ("still", "intro", "loop") else "still", duration=float(duration),
            drift=(motion != "loop") if drift is None else drift)
    return spec.fn(k, fields or {}, photo if spec.photo else None)


def registry() -> list[dict]:
    return [
        {"name": t.name, "label": t.label, "description": t.description, "kinds": list(t.kinds), "theme": t.theme,
         "photo": t.photo, "animated": t.animated, "group": t.group, "themes": list(t.themes), "three_d": t.three_d,
         "fields": [{"name": n, "label": lbl, "type": typ} for n, lbl, typ in t.fields]}
        for t in TEMPLATES.values()
    ]
