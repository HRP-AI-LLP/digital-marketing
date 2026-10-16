"""Branded HTML templates and their registry (used by the Media studio UI and the media pipeline).

render_html(name, fields, width, height, brand, photo=None, theme=None) -> HTML string
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.services.media_templates.base import (  # noqa: F401
    BrandStyle,
    Page,
    default_logo_uri,
)
from app.services.media_templates.carousel import (
    carousel_cover,
    carousel_cta,
    carousel_slide,
)
from app.services.media_templates.motion import animated_text, video_caption_overlay
from app.services.media_templates.static import (
    announcement,
    photo_headline,
    quote_card,
    stat_card,
    tip_card,
)


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    label: str
    description: str
    kinds: tuple[str, ...]
    fields: tuple[tuple[str, str, str], ...]  # (name, label, type: text|textarea|list|number)
    fn: Callable[[Page, dict, str | None], str]
    theme: str = "dark"
    photo: bool = True  # can sit on a background photo
    animated: bool = False


_T = TemplateSpec
TEMPLATES: dict[str, TemplateSpec] = {t.name: t for t in [
    _T("photo_headline", "Headline on photo", "Bold headline over an AI or stock photo (or a brand gradient).",
       ("image",), (("eyebrow", "Small label", "text"), ("headline", "Headline", "textarea"), ("body", "Supporting line", "textarea"),
                    ("cta", "Button text", "text")), photo_headline),
    _T("quote_card", "Quote", "A quote with the speaker's name.", ("image",),
       (("quote", "Quote", "textarea"), ("author", "Name", "text"), ("role", "Role or company", "text")), quote_card),
    _T("tip_card", "Tips list", "A title with 3–5 numbered tips.", ("image",),
       (("eyebrow", "Small label", "text"), ("headline", "Title", "textarea"), ("bullets", "Tips (one per line)", "list")),
       tip_card, theme="light", photo=False),
    _T("stat_card", "Big number", "One striking number with a short label.", ("image",),
       (("eyebrow", "Small label", "text"), ("stat", "Number", "text"), ("stat_label", "What it means", "textarea"),
        ("body", "Context", "textarea"), ("source", "Source", "text")), stat_card),
    _T("announcement", "Announcement", "News, launches and events with a call to action.", ("image",),
       (("eyebrow", "Small label", "text"), ("headline", "Headline", "textarea"), ("body", "Details", "textarea"),
        ("date", "Date or place", "text"), ("cta", "Button text", "text")), announcement),
    _T("carousel_cover", "Carousel cover", "First slide: the hook.", ("carousel", "document"),
       (("eyebrow", "Small label", "text"), ("headline", "Headline", "textarea"), ("body", "Subtitle", "textarea")), carousel_cover),
    _T("carousel_slide", "Carousel slide", "Numbered content slide.", ("carousel", "document"),
       (("heading", "Heading", "textarea"), ("body", "Text", "textarea"), ("bullets", "Points (one per line)", "list")),
       carousel_slide, theme="light", photo=False),
    _T("carousel_cta", "Carousel closing slide", "Last slide: follow, save, visit.", ("carousel", "document"),
       (("heading", "Heading", "text"), ("body", "Text", "textarea"), ("cta", "Button text", "text")), carousel_cta),
    _T("animated_text", "Animated text", "Lines reveal one by one over moving brand colours, then the logo.",
       ("gif", "animation"), (("eyebrow", "Small label", "text"), ("lines", "Lines (one per line)", "list"), ("cta", "Button text", "text")),
       animated_text, animated=True),
    _T("video_caption_overlay", "Video caption", "Caption and logo laid over a video scene.", ("video",),
       (("title", "Title", "text"), ("caption", "Caption", "textarea")), video_caption_overlay, photo=False),
]}


def render_html(name: str, fields: dict, width: int, height: int, brand: BrandStyle, photo: str | None = None,
                theme: str | None = None, transparent: bool | None = None) -> str:
    spec = TEMPLATES.get(name)
    if spec is None:
        raise KeyError(f"Unknown template: {name}")
    th = theme or fields.get("theme") or spec.theme
    if photo:
        th = "dark"  # text over photos always sits on a dark scrim
    if th not in ("dark", "light"):
        th = spec.theme
    page = Page(width, height, brand, theme=th,
                transparent=spec.name == "video_caption_overlay" if transparent is None else transparent)
    return spec.fn(page, fields or {}, photo if spec.photo else None)


def registry() -> list[dict]:
    return [
        {"name": t.name, "label": t.label, "description": t.description, "kinds": list(t.kinds), "theme": t.theme,
         "photo": t.photo, "animated": t.animated,
         "fields": [{"name": n, "label": lbl, "type": typ} for n, lbl, typ in t.fields]}
        for t in TEMPLATES.values()
    ]
