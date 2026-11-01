import hmac
import logging
import re
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from app.api import (
    analytics, analytics_growth, auth, content, dashboard, integrations, intelligence, jobs, media, notifications, organization, platform, roles,
    seo, social, users,
)
from app.api import inbox as inbox_api
from app.api import brand_ai, research  # noqa: E402
from app.api import seo_site  # noqa: E402
from app.api import presence as presence_api  # noqa: E402
from app.api import aivis as aivis_api  # noqa: E402
from app.api import listening as listening_api  # noqa: E402
from app.api import siteaudit as siteaudit_api  # noqa: E402
from app.api import keywords as keywords_api  # noqa: E402
from app.api import canva as canva_api  # noqa: E402
from app.api import linkinbio  # noqa: E402
from app.api import links as links_api  # noqa: E402
from app.api import follow as follow_api  # noqa: E402
from app.api import system as system_api  # noqa: E402
from app.api import reports as reports_api  # noqa: E402
from app.api import email as email_api  # noqa: E402
from app.api import connections as connections_api  # noqa: E402
from app.api import collab as collab_api  # noqa: E402
from app.api import bulk_import as bulk_import_api  # noqa: E402
from app.api import experiments as experiments_api  # noqa: E402
from app.api import security as security_api  # noqa: E402
from app.api import backups as backups_api  # noqa: E402
from app.api import preferences as preferences_api  # noqa: E402
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.modules import load_all
from app.services.tenancy import bootstrap

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
from app.core import logsafe  # noqa: E402

logsafe.install()
API_PREFIX = "/api/v1"  # versioned for the web app and the future Android / iOS apps


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bootstrap()
    load_all()
    worker_task = None
    if settings.worker_in_process and not settings.cloud and settings.automation_enabled:
        import asyncio

        from app.services.jobs import Worker

        worker = Worker()
        worker_task = asyncio.create_task(worker.run())
    yield
    if worker_task:
        worker.stop()
        worker_task.cancel()


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Requests that arrive through the public HTTPS tunnel may only reach sign-in callbacks, signed media links and
# the public link-in-bio pages (page, click redirect, and the images the page shows) and tracked short links (/r/<code>).
TUNNEL_ALLOWED = re.compile(
    r"^(/api/v1/(social/oauth/[a-z_]+/callback|media/public/[0-9a-fA-F-]{36}/[^/]+|health)"
    r"|/l/[a-z0-9-]+(/go/([0-9a-f-]{36}|s-[a-z]+)|/img/[0-9a-f-]{36})?"
    r"|/r/[A-Za-z0-9]{4,16}"
    r"|/f/[a-z0-9-]{1,42})$")


def _public_host() -> str:
    return (urlsplit(settings.public_base_url).hostname or "").lower() if settings.public_base_url else ""


@app.middleware("http")
async def tunnel_guard(request: Request, call_next):
    if settings.cloud:
        # Cloud: every request reaches the API through the Cloudflare Worker, which adds the shared secret.
        # The Cloud Run URL itself refuses anything without it (health stays open for uptime checks).
        if request.url.path != f"{API_PREFIX}/health":
            ov = settings.origin_verify_secret
            if not ov or not hmac.compare_digest(request.headers.get("x-origin-verify", ""), ov):
                return PlainTextResponse("Not found", status_code=404)
        if request.method in ("GET", "HEAD", "OPTIONS") or not request.url.path.startswith(API_PREFIX + "/") \
                or request.url.path == f"{API_PREFIX}/internal/tick":
            return await call_next(request)
        from app.services import wake

        box: list = []
        token = wake.queued_in_request.set(box)
        try:
            response = await call_next(request)
        finally:
            wake.queued_in_request.reset(token)
        if response.status_code < 400:
            # work queued (or a post scheduled) in this request: run it now / on the minute, not at the next tick
            await (wake.book() if box else wake.plan())
        return response
    via_tunnel = "cf-connecting-ip" in request.headers or "cf-ray" in request.headers
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if via_tunnel or (_public_host() and host == _public_host()):
        if (settings.api_tokens_via_tunnel and request.url.path.startswith(API_PREFIX + "/")
                and (request.headers.get("authorization") or "").startswith("Bearer hrp_")):
            return await call_next(request)  # opt-in: automation tools with an API token (checked in get_context)
        if request.method not in ("GET", "HEAD") or not TUNNEL_ALLOWED.match(request.url.path):
            return PlainTextResponse("Not found", status_code=404)
    return await call_next(request)


for r in (auth.router, users.router, roles.router, organization.router, platform.router, integrations.router,
          notifications.router, jobs.router, intelligence.router, content.router, social.router, media.router,
          seo.router, analytics.router, analytics_growth.router, dashboard.router, inbox_api.router,
          research.router, brand_ai.router, seo_site.router, presence_api.router, aivis_api.router, listening_api.router, siteaudit_api.router, keywords_api.router, linkinbio.router, canva_api.router, system_api.router, reports_api.router, email_api.router,
          links_api.router, follow_api.router,
          connections_api.router,
          collab_api.inbox_router, collab_api.content_router,
          bulk_import_api.router, experiments_api.router, experiments_api.analytics_router,
          security_api.router, backups_api.router, preferences_api.router,
          ):
    app.include_router(r, prefix=API_PREFIX)
app.include_router(linkinbio.public)  # /l/<slug>: public link-in-bio pages (no sign-in)
app.include_router(links_api.public)  # /r/<code>: tracked short links (no sign-in)
app.include_router(follow_api.public)  # /f/<slug>: public "follow us" page (no sign-in)


@app.post(f"{API_PREFIX}/internal/tick", include_in_schema=False)
async def internal_tick(request: Request):
    """Cloud job runner: Cloud Scheduler calls this every minute (X-Cron-Secret). It runs due periodic tasks and
    works through queued jobs for about tick_seconds, then waits for the jobs it started."""
    secret = settings.cron_secret
    if not secret or secret == "none":
        return PlainTextResponse("Not found", status_code=404)
    if not hmac.compare_digest(request.headers.get("x-cron-secret", ""), secret):
        return PlainTextResponse("Forbidden", status_code=403)
    if not settings.automation_enabled:
        return {"skipped": "automation is switched off here"}
    from app.services.jobs import tick_worker

    from datetime import timedelta

    from app.services import wake

    out = await tick_worker().tick(settings.tick_seconds)
    booked = await wake.plan(None if out.get("jobs") else timedelta(seconds=60))
    out["next_wake"] = booked.isoformat() if booked else None
    return out


@app.get(f"{API_PREFIX}/health", tags=["health"])
async def health():
    async with SessionLocal() as s:
        await s.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "version": app.version}
