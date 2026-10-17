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
    if settings.worker_in_process:
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

# Requests that arrive through the public HTTPS tunnel may only reach sign-in callbacks and signed media links.
TUNNEL_ALLOWED = re.compile(r"^/api/v1/(social/oauth/[a-z_]+/callback|media/public/[0-9a-fA-F-]{36}/[^/]+|health)$")


def _public_host() -> str:
    return (urlsplit(settings.public_base_url).hostname or "").lower() if settings.public_base_url else ""


@app.middleware("http")
async def tunnel_guard(request: Request, call_next):
    via_tunnel = "cf-connecting-ip" in request.headers or "cf-ray" in request.headers
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if via_tunnel or (_public_host() and host == _public_host()):
        if request.method not in ("GET", "HEAD") or not TUNNEL_ALLOWED.match(request.url.path):
            return PlainTextResponse("Not found", status_code=404)
    return await call_next(request)


for r in (auth.router, users.router, roles.router, organization.router, platform.router, integrations.router,
          notifications.router, jobs.router, intelligence.router, content.router, social.router, media.router,
          seo.router, analytics.router, analytics_growth.router, dashboard.router, inbox_api.router,
          research.router, brand_ai.router):
    app.include_router(r, prefix=API_PREFIX)


@app.get(f"{API_PREFIX}/health", tags=["health"])
async def health():
    async with SessionLocal() as s:
        await s.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "version": app.version}
