"""Webhook worker: delivers queued events and clears out old delivery records.

Job kind: webhook.deliver {webhook_id, event_id, event, created_at, data, attempt[, force, redelivery]}.
Periodic: webhooks_cleanup (daily) removes deliveries older than 30 days.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.core.db import platform_session
from app.models import Webhook, WebhookDelivery
from app.services import webhooks as svc
from app.services.jobs import JobContext, handler, periodic

log = logging.getLogger(__name__)


@handler("webhook.deliver")
async def h_deliver(ctx: JobContext) -> dict:
    result = await svc.deliver(ctx.session, ctx.payload or {}, force=bool((ctx.payload or {}).get("force")))
    hook = None
    if result.get("disabled"):
        hook = await ctx.session.get(Webhook, uuid.UUID(str(ctx.payload["webhook_id"])))
    await ctx.session.commit()
    if hook is not None:
        await svc.notify_disabled(hook.organization_id, hook.name, hook.disabled_reason)
    return result


@periodic(24 * 3600, name="webhooks_cleanup", offline_ok=True)
async def cleanup() -> None:
    cutoff = datetime.now(UTC) - timedelta(days=svc.KEEP_DAYS)
    async with platform_session() as s:
        await s.execute(delete(WebhookDelivery).where(WebhookDelivery.created_at < cutoff))
        await s.commit()
