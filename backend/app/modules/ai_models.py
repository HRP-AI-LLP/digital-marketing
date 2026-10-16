"""Keeps every organization's AI model lists current (text, embeddings, images, voices, video).

Every 6 hours each enabled AI integration is asked which models it offers. Lists in the Integrations page and
Task routing are pre-filled from this, and models you use that disappear are flagged (with one notification)
so their tasks fall back to the next model in the chain.
"""

import logging

from app.core.db import SessionLocal, set_tenant
from app.services.jobs import org_ids_active, periodic
from app.services.llm import discovery

log = logging.getLogger(__name__)


async def refresh_org(org_id) -> int:
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        n = await discovery.sync_org(s)
        await s.commit()
        return n


@periodic(6 * 3600, name="ai_model_lists_refresh")
async def refresh_model_lists() -> None:
    for org_id in await org_ids_active():
        try:
            await refresh_org(org_id)
        except Exception:
            log.exception("model list refresh failed for org %s", org_id)
