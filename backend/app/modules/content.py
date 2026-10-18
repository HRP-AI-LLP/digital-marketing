"""Content worker: post generation, publishing, metrics, planning/dispatch, daily digest.

Job kinds: post.generate {post_id}, post.publish {post_id}, post.metrics {post_id}.
Periodic: content_plan_and_dispatch (60 s), content_metrics_refresh (6 h), content_daily_digest (10 min check,
sends at ~09:00 organization time once a day).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.db import SessionLocal, set_tenant
from app.models import NotificationPref, Organization, Post, PostMetric, SocialAccount
from app.services import content as svc
from app.services import scheduler
from app.services.jobs import (
    JobContext,
    PermanentError,
    WaitForNetwork,
    enqueue,
    offline_now,
    handler,
    org_ids_active,
    periodic,
)
from app.services.llm.router import NoProviderError

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
NO_MODEL_MSG = "Add an AI text model in Settings → Integrations"


async def _notify(org_id: uuid.UUID, event: str, title: str, body: str | None = None, *, link: str | None = None,
                  permission: str | None = "content.view", actions: list[dict] | None = None) -> None:
    try:
        from app.services.notify import notify_org

        await notify_org(org_id, event, title, body, link=link, permission=permission, actions=actions)
    except Exception:
        log.exception("notification %s failed", event)


async def _load_post(ctx: JobContext) -> Post | None:
    pid = svc.as_uuid((ctx.payload or {}).get("post_id"))
    return await ctx.session.get(Post, pid) if pid else None


async def _tz(ctx: JobContext):
    org = await ctx.session.get(Organization, ctx.org_id) if ctx.org_id else None
    return svc.org_tz(org)


# ------------------------------------------------------------------ post.generate
async def _fail_generation(ctx: JobContext, post_id: uuid.UUID, message: str) -> None:
    s = ctx.session
    await s.rollback()
    post = await s.get(Post, post_id)
    if post is None:
        return
    post.status = "failed"
    post.error = message[:2000]
    svc.add_activity(s, post, "generation_failed", message[:500])
    await s.commit()


async def _wait_generation(ctx: JobContext, post_id: uuid.UUID) -> None:
    s = ctx.session
    await s.rollback()
    post = await s.get(Post, post_id)
    if post is not None:
        post.status, post.error = "planned", None
        await s.commit()


@handler("post.generate")
async def generate(ctx: JobContext) -> dict | None:
    s = ctx.session
    post = await _load_post(ctx)
    if post is None:
        return {"skipped": "The post no longer exists."}
    if post.status not in ("planned", "generating"):
        return {"skipped": f"Post is {post.status}."}
    post_id = post.id
    post.status = "generating"
    await s.commit()
    try:
        result = await svc.generate_post(s, post)
    except NoProviderError as e:
        if await offline_now():
            await _wait_generation(ctx, post_id)
            raise WaitForNetwork() from e
        await _fail_generation(ctx, post_id, NO_MODEL_MSG)
        raise PermanentError(NO_MODEL_MSG) from None
    except svc.GenerationError as e:
        await _fail_generation(ctx, post_id, str(e))
        raise PermanentError(str(e)) from None
    except Exception as e:
        if await offline_now(e):
            await _wait_generation(ctx, post_id)
            raise WaitForNetwork() from e
        msg = f"Couldn't write this post: {e}"
        if ctx.attempt >= MAX_ATTEMPTS:
            await _fail_generation(ctx, post_id, msg)
            raise PermanentError(msg) from e
        await s.rollback()
        post = await s.get(Post, post_id)
        if post is not None:
            post.status, post.error = "planned", msg[:2000]
            await s.commit()
        raise
    await s.commit()
    if result.notify_review:
        await svc.notify_review(post, await _tz(ctx))
    return {"post_id": str(post.id), "status": result.status, "warnings": result.warnings}


# ------------------------------------------------------------------ post.publish
@handler("post.publish")
async def publish(ctx: JobContext) -> dict | None:
    s = ctx.session
    post = await _load_post(ctx)
    if post is None:
        return {"skipped": "The post no longer exists."}
    if post.status == "published" or post.external_id:
        return {"skipped": "Already published."}
    if post.status not in ("scheduled", "approved", "publishing"):
        return {"skipped": f"Post is {post.status}."}
    org_id = post.organization_id
    label = svc.platform_of(post.platform).label
    account = await svc.account_for(s, post)
    if account is None or account.status == "removed":
        post.status, post.error = "failed", "The social account for this post was removed. Duplicate it to another account."
        svc.add_activity(s, post, "publish_failed", post.error)
        await s.commit()
        await _notify(org_id, "post_failed", f"A {label} post couldn't be published", post.error, link=f"/posts/{post.id}")
        return {"status": "failed"}

    # Platforms without an API (or accounts set to manual): hand over to a person.
    if post.platform in svc.MANUAL_PLATFORMS or account.platform in svc.MANUAL_PLATFORMS or account.status == "manual":
        return await _to_manual(ctx, post, account, "This account is published by hand.")

    if account.status in ("expired", "error"):
        post.status = "failed"
        post.error = f"{account.name} needs reconnecting. Reconnect it in Social accounts, then press Publish now."
        svc.add_activity(s, post, "publish_failed", post.error)
        await s.commit()
        await _notify(org_id, "account_issue", f"Reconnect {account.name}", post.error, link="/settings/accounts",
                      permission="integration.manage")
        return {"status": "failed"}

    try:
        from app.services import publishing
    except ImportError:
        return await _to_manual(ctx, post, account, "Automatic publishing isn't set up yet.")

    # Claim the post under a row lock so two jobs can never publish it twice. A post already in
    # 'publishing' may only be resumed by the same job (a retry), never by a duplicate job.
    post_id, account_id, job_key = post.id, account.id, str(ctx.job_id)
    await s.commit()
    post = await s.scalar(select(Post).where(Post.id == post_id).with_for_update())
    owner = (post.meta or {}).get("publish_job") if post else None
    if post is None or post.external_id or not (
        post.status in ("scheduled", "approved") or (post.status == "publishing" and owner == job_key)
    ):
        await s.rollback()
        return {"skipped": "Another task is already publishing this post, or it was already published."}
    post.status = "publishing"
    post.meta = {**(post.meta or {}), "publish_job": job_key}
    post.attempts = (post.attempts or 0) + 1
    await s.commit()
    try:
        external_id, external_url = await publishing.publish(s, post, account)
    except Exception as e:
        is_pub_err = type(e).__name__ == "PublishError" or isinstance(e, getattr(publishing, "PublishError", ()))
        reconnect = bool(getattr(e, "reconnect", False)) if is_pub_err else False
        # Unexpected errors may happen after the platform accepted the post: don't retry blindly.
        retryable = bool(getattr(e, "retryable", False)) if is_pub_err else False
        msg = str(e)[:1500] or type(e).__name__
        if not is_pub_err:
            msg = (f"Unexpected error while publishing ({msg}). Check the platform before pressing Publish now: "
                   "the post may already be live.")
        await s.rollback()
        post = await s.get(Post, post_id)
        account = await s.get(SocialAccount, account_id)
        if post is None:
            return {"skipped": "The post no longer exists."}
        post.error = msg
        if reconnect:
            if account is not None:
                account.status, account.status_message = "expired", msg[:500]
            post.status = "failed"
            svc.add_activity(s, post, "publish_failed", f"Account needs reconnecting: {msg}")
            await s.commit()
            name = account.name if account else label
            await _notify(org_id, "account_issue", f"Reconnect {name}",
                          f"Publishing failed because the connection expired: {msg}\nReconnect it in Social accounts, "
                          "then press Publish now on the post.", link="/settings/accounts", permission="integration.manage")
            return {"status": "failed", "reconnect": True}
        if retryable and await offline_now(e if not is_pub_err else None):
            # No internet: wait (status stays 'publishing', owned by this job) and post once it's back.
            svc.add_activity(s, post, "publish_waiting", "No internet connection. It will post as soon as the "
                                                         "connection is back.")
            post.error = None
            await s.commit()
            raise WaitForNetwork() from e
        if retryable and ctx.attempt < MAX_ATTEMPTS:
            svc.add_activity(s, post, "publish_retry", f"Attempt {ctx.attempt} failed: {msg}")
            await s.commit()  # status stays 'publishing' while the job retries
            raise
        post.status = "failed"
        svc.add_activity(s, post, "publish_failed", msg)
        await s.commit()
        await _notify(org_id, "post_failed", f"A {label} post couldn't be published",
                      f"{msg}\n\nFix the problem, then press Publish now on the post.", link=f"/posts/{post.id}")
        return {"status": "failed", "error": msg}

    post.status = "published"
    post.published_at = datetime.now(UTC)
    post.external_id = str(external_id) if external_id is not None else None
    post.external_url = external_url
    post.error = None
    svc.add_activity(s, post, "published", external_url or None)
    await s.commit()
    await _notify(org_id, "post_published", f"Published on {account.name}",
                  (post.title or post.text or "")[:300], link=external_url or f"/posts/{post.id}")
    return {"status": "published", "external_id": post.external_id, "external_url": external_url}


async def _to_manual(ctx: JobContext, post: Post, account: SocialAccount, why: str) -> dict:
    s = ctx.session
    post.status = "manual_pending"
    post.error = None
    svc.add_activity(s, post, "manual_pending", why)
    await s.commit()
    await _notify(post.organization_id, "manual_publish", f"Time to post on {account.name}",
                  f"{why} Copy the text and media from the app, post it, then press Mark as published.\n\n"
                  f"{svc.full_caption(post.text, post.hashtags)[:500]}",
                  link=f"/posts/{post.id}", permission="content.publish")
    return {"status": "manual_pending"}


# ------------------------------------------------------------------ post.metrics
METRIC_KEYS = ("impressions", "reach", "likes", "comments", "shares", "clicks", "saves", "video_views")


def _int(v) -> int:
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return 0


@handler("post.metrics")
async def metrics(ctx: JobContext) -> dict | None:
    s = ctx.session
    post = await _load_post(ctx)
    if post is None or post.status != "published":
        return {"skipped": "Not a published post."}
    account = await svc.account_for(s, post)
    if account is None or account.status != "connected":
        return {"skipped": "Account not connected."}
    try:
        from app.services import publishing
    except ImportError:
        return {"skipped": "Publishing module not available."}
    data = await publishing.fetch_metrics(s, post, account)
    if not data:
        return {"skipped": "No metrics available."}
    m = PostMetric(organization_id=post.organization_id, post_id=post.id, raw=dict(data),
                   **{k: _int(data.get(k)) for k in METRIC_KEYS})
    s.add(m)
    await s.commit()
    return {k: getattr(m, k) for k in METRIC_KEYS}


# ------------------------------------------------------------------ periodic tasks
@periodic(60, name="content_plan_and_dispatch")
async def plan_and_dispatch() -> None:
    for org_id in await org_ids_active():
        try:
            await scheduler.run_org(org_id)
        except Exception:
            log.exception("planning failed for org %s", org_id)


async def refresh_metrics_for(org_id: uuid.UUID, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        rows = (await s.scalars(select(Post.id).where(
            Post.status == "published", Post.published_at >= now - timedelta(days=30), Post.external_id.is_not(None),
            Post.platform.notin_(list(svc.MANUAL_PLATFORMS)),
        ))).all()
        for pid in rows:
            await enqueue(s, "post.metrics", org_id, {"post_id": str(pid)}, dedupe_key=f"met:{pid}", priority=8)
        await s.commit()
    return len(rows)


@periodic(6 * 3600, name="content_metrics_refresh")
async def metrics_refresh() -> None:
    for org_id in await org_ids_active():
        try:
            await refresh_metrics_for(org_id)
        except Exception:
            log.exception("metrics refresh failed for org %s", org_id)


DIGEST_HOUR = 9


async def daily_digest_for(org_id: uuid.UUID, now: datetime | None = None, *, force: bool = False) -> dict | None:
    """Send the daily summary once per local day at/after 09:00. Returns the counts when sent."""
    now = now or datetime.now(UTC)
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        org = await s.get(Organization, org_id)
        if org is None:
            return None
        tz = svc.org_tz(org)
        local = now.astimezone(tz)
        today = local.date().isoformat()
        brand = await svc.get_brand(s, org_id)
        settings = dict(brand.settings or {})
        if not force and (local.hour < DIGEST_HOUR or settings.get("last_digest_date") == today):
            await s.rollback()
            return None
        start_today = local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_yday, end_today = start_today - timedelta(days=1), start_today + timedelta(days=1)

        async def count(*where) -> int:
            return int(await s.scalar(select(func.count()).select_from(Post).where(*where)) or 0)

        counts = {
            "published_yesterday": await count(Post.status == "published", Post.published_at >= start_yday,
                                               Post.published_at < start_today),
            "waiting_for_approval": await count(Post.status == "in_review"),
            "failed": await count(Post.status == "failed", Post.updated_at >= now - timedelta(days=1)),
            "manual_pending": await count(Post.status == "manual_pending"),
            "upcoming_today": await count(Post.status.in_(["scheduled", "in_review", "approved"]),
                                          Post.scheduled_at >= now, Post.scheduled_at < end_today),
        }
        prefs = (await s.scalars(select(NotificationPref).where(NotificationPref.enabled.is_(True)))).all()
        users = list({p.user_id for p in prefs if "daily_digest" in (p.events or [])})
        settings["last_digest_date"] = today
        brand.settings = settings
        await s.commit()
    if users:
        from app.services.notify import notify_users

        body = (f"Published yesterday: {counts['published_yesterday']}\n"
                f"Waiting for approval: {counts['waiting_for_approval']}\n"
                f"Failed (last 24 h): {counts['failed']}\n"
                f"Ready to post by hand: {counts['manual_pending']}\n"
                f"Still to go out today: {counts['upcoming_today']}")
        try:
            await notify_users(org_id, users, "daily_digest", "Your daily content summary", body, link="/calendar")
        except Exception:
            log.exception("daily digest failed")
    return {**counts, "recipients": len(users)}


@periodic(600, name="content_daily_digest")
async def daily_digest() -> None:
    for org_id in await org_ids_active():
        try:
            await daily_digest_for(org_id)
        except Exception:
            log.exception("daily digest failed for org %s", org_id)
