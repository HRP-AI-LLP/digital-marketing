"""Social accounts: connect (OAuth / app password / token / manual), list, settings, refresh, remove.

OAuth callbacks arrive without our auth header: the `state` row (looked up through a platform
session) says which organization and user started the flow; the work then runs in a session
scoped to that organization.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator, Field
from sqlalchemy import delete, select, update

from app.core.config import ROOT_DIR, get_settings
from app.core.db import SessionLocal, platform_session, set_tenant
from app.core.deps import Context, require
from app.models import OAuthState, ProviderConfig, Schedule, SocialAccount, User
from app.services import audit, publishing
from app.services.llm.catalog import BY_ID
from app.services.platforms import PLATFORMS
from app.services.social import common
from app.services.social.common import OAUTH_PROVIDERS, PublishError

router = APIRouter(prefix="/social", tags=["social"])

STATE_TTL = timedelta(minutes=20)
HIDDEN_PLATFORMS = {"google"}  # token holder for Google; not something you post to

# How each connector is set up in the UI. Platforms map to exactly one connector.
CONNECTORS: list[dict] = [
    {"id": "meta", "label": "Facebook and Instagram", "method": "oauth", "platforms": ["facebook_page", "instagram"],
     "blurb": "Connect your Facebook pages and the Instagram professional accounts linked to them."},
    {"id": "linkedin", "label": "LinkedIn", "method": "oauth", "platforms": ["linkedin_person", "linkedin_org"],
     "blurb": "Post on your LinkedIn profile and, once LinkedIn approves your app, on company pages you administer."},
    {"id": "threads", "label": "Threads", "method": "oauth", "platforms": ["threads"],
     "blurb": "Post to your Threads profile."},
    {"id": "pinterest", "label": "Pinterest", "method": "oauth", "platforms": ["pinterest"],
     "blurb": "Create image, carousel and video pins on a board you choose."},
    {"id": "google", "label": "Google Business Profile and YouTube", "method": "oauth", "platforms": ["gbp", "youtube"],
     "blurb": "One Google connection for Business Profile posts, YouTube Shorts, Google Analytics and Search Console."},
    {"id": "bluesky", "label": "Bluesky", "method": "app_password", "platforms": ["bluesky"],
     "blurb": "Sign in with your handle and an app password."},
    {"id": "mastodon", "label": "Mastodon", "method": "token", "platforms": ["mastodon"],
     "blurb": "Use an access token from your Mastodon server."},
    {"id": "manual", "label": "X and other platforms", "method": "manual", "platforms": ["x", "manual"],
     "blurb": "No API connection: posts are prepared for you to publish by hand. X is on hold because its API is paid."},
]
PLATFORM_CONNECTOR = {p: c["id"] for c in CONNECTORS for p in c["platforms"]}


# ------------------------------------------------------------------ guide
def _guide_sections() -> dict[str, str]:
    """Guide sections from docs/SETUP_SOCIAL.md, re-read whenever the file changes (no restart needed)."""
    path = ROOT_DIR / "docs" / "SETUP_SOCIAL.md"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    return _parse_guide(str(path), mtime)


@lru_cache(maxsize=4)
def _parse_guide(path: str, mtime: float) -> dict[str, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    parts = re.split(r"<!--\s*guide:([a-z_]+)\s*-->", text)
    out = {"intro": parts[0].strip()}
    for i in range(1, len(parts) - 1, 2):
        out[parts[i]] = parts[i + 1].strip()
    return out


def guide(section: str) -> str:
    return _guide_sections().get(section, "")


# ------------------------------------------------------------------ helpers
def _out(a: SocialAccount) -> dict:
    s = a.settings or {}
    spec = PLATFORMS.get(a.platform)
    return {
        "id": a.id, "platform": a.platform,
        "platform_label": spec.label if spec else ("Google connection" if a.platform == "google" else a.platform),
        "name": a.name, "handle": a.handle, "avatar_url": a.avatar_url, "profile_url": a.profile_url,
        "status": a.status, "status_message": a.status_message, "token_expires_at": a.token_expires_at,
        "settings": {k: v for k, v in s.items() if not k.startswith("_")},
        "last_checked_at": s.get("_last_checked_at"), "created_at": a.created_at,
    }


def _err(e: PublishError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY if e.retryable else status.HTTP_400_BAD_REQUEST, e.message)


async def _get(ctx: Context, aid: uuid.UUID) -> SocialAccount:
    a = await ctx.session.scalar(select(SocialAccount).where(SocialAccount.id == aid, SocialAccount.status != "removed"))
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found.")
    return a


def _audit(ctx: Context, action: str, acc: SocialAccount | None, summary: str, details: dict | None = None) -> None:
    audit.record(ctx.session, action=action, organization_id=ctx.org_id, actor=ctx.user, entity_type="social_account",
                 entity_id=acc.id if acc else None, summary=summary, details=details, ip=ctx.ip)


# ------------------------------------------------------------------ platforms & accounts
@router.get("/platforms")
async def platforms(ctx: Context = Depends(require("integration.view"))):
    configured = {p: bool(await common.app_credentials(ctx.session, p)) for p in OAUTH_PROVIDERS}
    media_host = await ctx.session.scalar(
        select(ProviderConfig.id).where(ProviderConfig.kind == "media_host", ProviderConfig.enabled.is_(True)).limit(1))
    connectors = []
    for c in CONNECTORS:
        oauth = c["method"] == "oauth"
        app = OAUTH_PROVIDERS[c["id"]]["app"] if oauth else None
        connectors.append({
            **c, "app_provider": app, "app_label": BY_ID[app].label if app in BY_ID else None,
            "app_configured": configured.get(c["id"], True) if oauth else True,
            "redirect_uri": common.redirect_uri(c["id"]) if oauth else None,
            "needs_public_media": any(PLATFORMS[p].needs_public_media for p in c["platforms"]),
            "guide": guide(c["id"]),
            "options": ([{"name": "company_pages", "label": "Include company pages (needs the Community Management API)",
                          "default": False},
                         {"name": "include_profile", "label": "Also sign in with OpenID Connect (profile)", "default": True}]
                        if c["id"] == "linkedin" else
                        [{"name": "include_instagram",
                          "label": "Include Instagram (needs the Instagram use case with Facebook Login on your Meta app)",
                          "default": False}]
                        if c["id"] == "meta" else []),
        })
    return {
        "platforms": [
            {"id": p.id, "label": p.label, "max_chars": p.max_chars, "post_types": list(p.post_types),
             "max_images": p.max_images, "needs_public_media": p.needs_public_media, "notes": p.notes,
             "connector": PLATFORM_CONNECTOR.get(p.id), "method": next(
                 (c["method"] for c in CONNECTORS if p.id in c["platforms"]), "manual"),
             "app_provider": p.oauth_app,
             "app_configured": configured.get(PLATFORM_CONNECTOR.get(p.id, ""), True),
             "redirect_uri": common.redirect_uri(PLATFORM_CONNECTOR[p.id])
             if PLATFORM_CONNECTOR.get(p.id) in OAUTH_PROVIDERS else None,
             "guide": guide(PLATFORM_CONNECTOR.get(p.id, "manual"))}
            for p in PLATFORMS.values()
        ],
        "connectors": connectors,
        "media_host_configured": bool(media_host or get_settings().public_base_url),
        "media_guide": guide("media"),
        "intro": guide("intro"),
        "redirect_base": common.base_url(),
    }


@router.get("/accounts")
async def list_accounts(all: bool = False, ctx: Context = Depends(require("integration.view"))):
    q = select(SocialAccount).where(SocialAccount.status != "removed").order_by(SocialAccount.platform, SocialAccount.name)
    if not all:
        q = q.where(SocialAccount.platform.not_in(HIDDEN_PLATFORMS))
    return [_out(a) for a in (await ctx.session.scalars(q)).all()]


# ------------------------------------------------------------------ OAuth
class ConnectIn(BaseModel):
    company_pages: bool = False
    include_profile: bool = True
    include_instagram: bool = False


@router.post("/connect/{provider}")
async def connect(provider: str, body: ConnectIn | None = None, ctx: Context = Depends(require("integration.manage"))):
    spec = OAUTH_PROVIDERS.get(provider)
    if spec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown provider: {provider}")
    options = (body or ConnectIn()).model_dump()
    variant = "pages" if provider == "linkedin" and options.get("company_pages") else None
    creds = await common.app_credentials(ctx.session, provider, variant)
    if not creds:
        label = BY_ID[spec["app"]].label if spec["app"] in BY_ID else spec["app"]
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Add the {label} credentials under Settings → Integrations first.")
    options["app_variant"] = creds.get("variant")
    state = common.new_state()
    verifier, challenge = common.pkce_pair() if provider == "google" else (None, None)
    redirect = common.redirect_uri(provider)
    async with platform_session() as ps:
        await ps.execute(delete(OAuthState).where(OAuthState.created_at < datetime.now(UTC) - timedelta(hours=2)))
        ps.add(OAuthState(state=state, organization_id=ctx.org_id, user_id=ctx.user.id, platform=provider,
                          data={"options": options, "redirect_uri": redirect, "code_verifier": verifier}))
        await ps.commit()
    mod = common.adapter(spec["module"])
    return {"authorize_url": mod.authorize_url(creds, redirect, state, options, challenge), "redirect_uri": redirect}


def _back(**params) -> RedirectResponse:
    url = f"{get_settings().web_base_url}/settings/accounts?{urlencode({k: v for k, v in params.items() if v is not None})}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/oauth/{provider}/callback", include_in_schema=False)
async def oauth_callback(provider: str, code: str | None = None, state: str | None = None, error: str | None = None,
                         error_description: str | None = None, error_reason: str | None = None):
    spec = OAUTH_PROVIDERS.get(provider)
    if spec is None:
        return _back(error=f"Unknown provider: {provider}")
    if not state:
        return _back(error="The sign-in response was missing its state. Start again from this page.")
    async with platform_session() as ps:
        st = await ps.get(OAuthState, state)
        if st is not None:
            await ps.delete(st)  # single use
            await ps.commit()
    if st is None or st.platform != provider:
        return _back(error="This sign-in has expired or was already used. Press Connect again.")
    created = st.created_at if st.created_at.tzinfo else st.created_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - created > STATE_TTL:
        return _back(error="The sign-in took too long. Press Connect again.")
    if error:
        msg = None
        if provider == "linkedin":
            from app.services.social.linkedin import oauth_error_message

            msg = oauth_error_message(error, error_description)
        return _back(error=msg or f"{spec['label']} sign-in was cancelled or refused: {error_description or error_reason or error}")
    if not code:
        return _back(error="The sign-in response had no authorization code. Press Connect again.")

    data = st.data or {}
    async with SessionLocal() as s:
        set_tenant(s, st.organization_id)
        creds = await common.app_credentials(s, provider, (data.get("options") or {}).get("app_variant"))
        if not creds:
            return _back(error="The app credentials were removed from Integrations. Add them and connect again.")
        try:
            accounts = await common.adapter(spec["module"]).complete(
                s, st.organization_id, st.user_id, creds, data.get("redirect_uri") or common.redirect_uri(provider),
                code, data)
        except PublishError as e:
            await s.rollback()
            return _back(error=e.message)
        except httpx.HTTPError as e:
            await s.rollback()
            return _back(error=f"Couldn't reach {spec['label']} ({type(e).__name__}). Try again.")
        user = await s.get(User, st.user_id)
        visible = [a for a in accounts if a.platform not in HIDDEN_PLATFORMS]
        audit.record(s, action="social.connected", organization_id=st.organization_id, actor=user,
                     entity_type="social_account", summary=f"Connected {spec['label']}: "
                     + (", ".join(a.name for a in accounts)[:400] or "no accounts"),
                     details={"provider": provider, "accounts": [str(a.id) for a in accounts]})
        await s.commit()
    return _back(connected=provider, count=str(len(visible)))


# ------------------------------------------------------------------ non-OAuth accounts
class BlueskyIn(BaseModel):
    handle: str = Field(min_length=3, max_length=200)
    app_password: str = Field(min_length=4, max_length=100)
    service: str | None = Field(None, max_length=300)


class MastodonIn(BaseModel):
    instance_url: str = Field(min_length=4, max_length=300)
    access_token: str = Field(min_length=8, max_length=500)


class ManualIn(BaseModel):
    platform: str = "manual"
    name: str = Field(min_length=1, max_length=200)
    handle: str | None = Field(None, max_length=200)
    profile_url: str | None = Field(None, max_length=1000)

    @field_validator("profile_url")
    @classmethod
    def _http_only(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        if not v:
            return None
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("Profile link must start with http:// or https://")
        return v


@router.post("/accounts/bluesky", status_code=201)
async def add_bluesky(body: BlueskyIn, ctx: Context = Depends(require("integration.manage"))):
    from app.services.social import bluesky

    try:
        acc = await bluesky.connect(ctx.session, ctx.org_id, ctx.user.id, body.handle, body.app_password, body.service)
    except PublishError as e:
        raise _err(e)
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach the Bluesky server. Check the address and try again.")
    _audit(ctx, "social.connected", acc, f"Connected Bluesky {acc.handle}")
    await ctx.session.commit()
    return _out(acc)


@router.post("/accounts/mastodon", status_code=201)
async def add_mastodon(body: MastodonIn, ctx: Context = Depends(require("integration.manage"))):
    from app.services.social import mastodon

    try:
        acc = await mastodon.connect(ctx.session, ctx.org_id, ctx.user.id, body.instance_url, body.access_token)
    except PublishError as e:
        raise _err(e)
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach that Mastodon server. Check the address and try again.")
    _audit(ctx, "social.connected", acc, f"Connected Mastodon {acc.handle}")
    await ctx.session.commit()
    return _out(acc)


@router.post("/accounts/manual", status_code=201)
async def add_manual(body: ManualIn, ctx: Context = Depends(require("integration.manage"))):
    if body.platform not in ("manual", "x"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Manual accounts can be X or Other.")
    acc = SocialAccount(organization_id=ctx.org_id, platform=body.platform, external_id=f"manual:{uuid.uuid4()}",
                        name=body.name.strip(), handle=(body.handle or "").strip() or None,
                        profile_url=(body.profile_url or "").strip() or None, status="manual", settings={},
                        connected_by=ctx.user.id)
    ctx.session.add(acc)
    await ctx.session.flush()
    _audit(ctx, "social.connected", acc, f"Added manual account {acc.name} ({PLATFORMS[body.platform].label})")
    await ctx.session.commit()
    return _out(acc)


# ------------------------------------------------------------------ account management
class AccountPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    settings: dict | None = None


@router.patch("/accounts/{aid}")
async def update_account(aid: uuid.UUID, body: AccountPatch, ctx: Context = Depends(require("integration.manage"))):
    acc = await _get(ctx, aid)
    if body.name is not None:
        acc.name = body.name.strip()
    if body.settings is not None:
        new = dict(acc.settings or {})
        for k, v in body.settings.items():
            if k.startswith("_"):
                continue
            if k == "privacy" and v not in ("public", "unlisted", "private"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Privacy must be public, unlisted or private.")
            if v is None:
                new.pop(k, None)
            else:
                new[k] = v
        acc.settings = new
    _audit(ctx, "social.updated", acc, f"Updated {acc.name}", {"fields": list(body.model_dump(exclude_unset=True))})
    await ctx.session.commit()
    return _out(acc)


@router.get("/accounts/{aid}/boards")
async def pinterest_boards(aid: uuid.UUID, ctx: Context = Depends(require("integration.view"))):
    acc = await _get(ctx, aid)
    if acc.platform != "pinterest":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Boards exist only for Pinterest accounts.")
    from app.services.social import pinterest

    try:
        return {"boards": await pinterest.list_boards(acc)}
    except PublishError as e:
        raise _err(e)
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Couldn't reach Pinterest. Try again.")


@router.post("/accounts/{aid}/refresh")
async def refresh(aid: uuid.UUID, ctx: Context = Depends(require("integration.manage"))):
    acc = await _get(ctx, aid)
    await publishing.refresh_account(ctx.session, acc)
    if acc.status == "connected":
        acc.settings = {k: v for k, v in (acc.settings or {}).items() if k != "_issue_notified"}
    _audit(ctx, "social.checked", acc, f"Checked {acc.name}: {acc.status}")
    await ctx.session.commit()
    return _out(acc)


@router.delete("/accounts/{aid}", status_code=204)
async def remove_account(aid: uuid.UUID, ctx: Context = Depends(require("integration.manage"))):
    acc = await _get(ctx, aid)
    now = datetime.now(UTC).isoformat()
    acc.tokens_enc, acc.token_expires_at = None, None
    acc.status, acc.status_message = "removed", None
    acc.settings = {**(acc.settings or {}), "_removed_at": now}
    # Its posts stay; its schedules stop so nothing new is planned for a removed account.
    await ctx.session.execute(update(Schedule).where(Schedule.social_account_id == acc.id).values(active=False))
    if acc.platform == "google":
        await ctx.session.execute(
            update(SocialAccount).where(SocialAccount.platform.in_(["gbp", "youtube"]), SocialAccount.status != "removed")
            .values(status="expired", status_message="Google was disconnected. Connect Google again."))
    _audit(ctx, "social.removed", acc, f"Removed {acc.name} ({acc.platform})")
    await ctx.session.commit()


# ------------------------------------------------------------------ Google helpers
class LocationsIn(BaseModel):
    locations: list[str]


@router.get("/google/locations")
async def google_locations(ctx: Context = Depends(require("integration.view"))):
    from app.services.social import google

    try:
        locs = await google.list_locations(ctx.session)
    except PublishError as e:
        await ctx.session.commit()
        raise _err(e)
    connected = set((await ctx.session.scalars(select(SocialAccount.external_id).where(
        SocialAccount.platform == "gbp", SocialAccount.status != "removed"))).all())
    await ctx.session.commit()  # a refreshed Google token is saved
    return {"locations": [{**loc, "connected": loc["name"] in connected} for loc in locs]}


@router.post("/google/locations")
async def add_google_locations(body: list[str] | LocationsIn = Body(...), ctx: Context = Depends(require("integration.manage"))):
    from app.services.social import google

    names = body.locations if isinstance(body, LocationsIn) else body
    if not names:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose at least one location.")
    try:
        accounts = await google.add_locations(ctx.session, ctx.org_id, ctx.user.id, names)
    except PublishError as e:
        raise _err(e)
    for a in accounts:
        _audit(ctx, "social.connected", a, f"Added Business Profile location {a.name}")
    await ctx.session.commit()
    return [_out(a) for a in accounts]


@router.get("/google/ga4-properties")
async def ga4_properties(ctx: Context = Depends(require("integration.view"))):
    from app.services.social import google

    try:
        props = await google.list_ga4_properties(ctx.session)
    except PublishError as e:
        await ctx.session.commit()
        raise _err(e)
    await ctx.session.commit()
    return {"properties": props}


@router.get("/google/gsc-sites")
async def gsc_sites(ctx: Context = Depends(require("integration.view"))):
    from app.services.social import google

    try:
        sites = await google.list_gsc_sites(ctx.session)
    except PublishError as e:
        await ctx.session.commit()
        raise _err(e)
    await ctx.session.commit()
    return {"sites": sites}
