"""Personal preferences (theme, accent, font, text size, language) and AI translation of the interface.

The interface is translated on the fly: the browser sends the English text it shows, we answer from a shared cache
and translate what's missing with the organization's AI text model. Any language works, including Indian national
and regional languages and dialects, because the model is simply asked for that language by name.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.db import platform_session
from app.core.deps import Context, get_context
from app.models import UiTranslation, User
from app.services.llm import router as llm_router

log = logging.getLogger(__name__)
router = APIRouter(tags=["preferences"])

class PreferencesIn(BaseModel):
    theme: Literal["light", "dark", "system", "navy", "graphite", "paper", "contrast"] | None = None
    accent: Literal["blue", "teal", "indigo", "violet", "emerald", "rose", "orange", "slate"] | None = None
    font: Literal["inter", "poppins", "roboto", "open-sans", "lato", "nunito", "dm-sans", "ibm-plex", "source-sans",
                  "lexend", "atkinson", "merriweather", "system"] | None = None
    text_size: Literal["s", "m", "l", "xl"] | None = None
    radius: Literal["rounded", "soft", "sharp"] | None = None
    density: Literal["comfortable", "compact"] | None = None
    reduce_motion: bool | None = None
    language: str | None = Field(None, max_length=80)  # BCP-47 code ("hi", "ta-IN") or a name ("Bhojpuri")
    language_name: str | None = Field(None, max_length=80)  # English name, used when asking the model


def _clean_lang(v: str | None) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())[:80]
    if v and not re.fullmatch(r"[\w\- ()'.,]+", v):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That language name has characters we can't use.")
    return v


@router.get("/me/preferences")
async def get_preferences(ctx: Context = Depends(get_context)) -> dict:
    return dict(ctx.user.preferences or {})


@router.put("/me/preferences")
async def put_preferences(body: PreferencesIn, ctx: Context = Depends(get_context)) -> dict:
    changes = body.model_dump(exclude_none=True)
    for k in ("language", "language_name"):
        if k in changes:
            changes[k] = _clean_lang(changes[k])
    async with platform_session() as s:
        user = await s.get(User, ctx.user.id)
        user.preferences = {**(user.preferences or {}), **changes}
        await s.commit()
        return dict(user.preferences)


# ------------------------------------------------------------------ interface translation
MAX_TEXTS = 150
MAX_LEN = 400
BATCH = 40


class TranslateIn(BaseModel):
    language: str = Field(min_length=2, max_length=80)
    language_name: str | None = Field(None, max_length=80)
    texts: list[str] = Field(default_factory=list, max_length=MAX_TEXTS)


def _key(language: str) -> str:
    return " ".join(language.lower().split())


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _usable(texts: list[str]) -> list[str]:
    out = []
    for t in dict.fromkeys(texts):
        if t and len(t) <= MAX_LEN and t.strip() and re.search(r"[A-Za-z]", t):
            out.append(t)
    return out


async def _cached(language: str, texts: list[str]) -> dict[str, str]:
    if not texts:
        return {}
    by_hash = {_hash(t): t for t in texts}
    async with platform_session() as s:
        rows = (await s.execute(select(UiTranslation.source_hash, UiTranslation.text).where(
            UiTranslation.language == _key(language), UiTranslation.source_hash.in_(list(by_hash))))).all()
    return {by_hash[h]: txt for h, txt in rows if h in by_hash}


async def _store(language: str, pairs: dict[str, str]) -> None:
    if not pairs:
        return
    async with platform_session() as s:
        for src, txt in pairs.items():
            await s.execute(insert(UiTranslation).values(
                id=uuid.uuid4(), language=_key(language), source_hash=_hash(src), source=src, text=txt,
            ).on_conflict_do_nothing(constraint="uq_ui_translations_lang_hash"))
        await s.commit()


PROMPT = """Translate the user-interface strings below from English into {lang}.

Rules:
- This is the interface of "HRP AI Marketing Studio", a social media and marketing app. Use the words people who use
  {lang} would expect in software (buttons, menus, settings). Short, natural, polite.
- Write in the script normally used for {lang} (for example Devanagari for Hindi or Bhojpuri, Tamil script for Tamil,
  Arabic script for Urdu). For a dialect without a standard spelling, use its most common written form.
- Keep exactly as they are: product and brand names (HRP AI, HRP AI Studio, LinkedIn, Instagram, Facebook, YouTube,
  Pinterest, X, Bluesky, Mastodon, Telegram, Discord, Reddit, WhatsApp, Google, Canva, Cloudflare, GoDaddy, Gemini, Groq,
  Ollama), numbers, URLs, email addresses, file names, code, placeholders like {{name}} or %s, and punctuation such
  as "·", "→", "…" at the same place.
- Keep leading and trailing spaces. Keep the same meaning; don't add or drop information.
- If a string is already not English, or is only a name or code, return it unchanged.

Return JSON: {{"t": [<one translation per input string, same order, same count>]}}

Strings:
{items}"""


async def _translate(ctx: Context, language: str, name: str, texts: list[str]) -> dict[str, str]:
    import json

    out: dict[str, str] = {}

    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        items = json.dumps(chunk, ensure_ascii=False, indent=0)
        try:
            data = await llm_router.complete_json(ctx.session, "translate", PROMPT.format(lang=name, items=items),
                                                  temperature=0.2, max_tokens=4000)
        except llm_router.NoProviderError:
            raise
        except Exception as e:  # noqa: BLE001 - one bad batch must not lose the others
            log.warning("UI translation batch into %s failed: %s", name, e)
            continue
        got = data.get("t") if isinstance(data, dict) else data
        if not isinstance(got, list) or len(got) != len(chunk):
            log.warning("UI translation into %s returned %s items for %s", name, len(got or []), len(chunk))
            continue
        for src, dst in zip(chunk, got):
            if isinstance(dst, str) and dst.strip():
                # keep the original's outer whitespace so text joins up with its neighbours
                lead = src[: len(src) - len(src.lstrip())]
                trail = src[len(src.rstrip()):]
                out[src] = lead + dst.strip() + trail
    await ctx.session.commit()  # usage records
    return out


@router.post("/i18n/lookup")
async def lookup(body: TranslateIn) -> dict:
    """Cached translations only (no AI call); works before sign-in, e.g. on the sign-in page."""
    if _key(body.language) in ("en", "english"):
        return {"translations": {}}
    texts = _usable(body.texts)
    return {"translations": await _cached(body.language, texts)}


@router.post("/i18n/translate")
async def translate(body: TranslateIn, ctx: Context = Depends(get_context)) -> dict:
    language = _clean_lang(body.language) or "en"
    if _key(language) in ("en", "english"):
        return {"translations": {}, "missing": 0}
    texts = _usable(body.texts)
    have = await _cached(language, texts)
    missing = [t for t in texts if t not in have]
    if missing and ctx.org is not None:
        name = _clean_lang(body.language_name) or language
        try:
            fresh = await _translate(ctx, language, name, missing)
        except llm_router.NoProviderError:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Translating the app needs an AI text model. Add one in Settings → Integrations.")
        await _store(language, fresh)
        have.update(fresh)
    return {"translations": have, "missing": len([t for t in texts if t not in have])}
