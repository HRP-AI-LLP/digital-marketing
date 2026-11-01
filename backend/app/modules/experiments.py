"""A/B tests: a periodic check that compares the variants once their results are in.

Periodic:
  experiments_evaluate   every hour, for organizations with running A/B tests
"""

import logging

from sqlalchemy import select

from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import Experiment
from app.services import experiments as svc
from app.services.jobs import periodic

log = logging.getLogger(__name__)


async def run_once(now=None) -> int:
    """Evaluate every running experiment, one organization at a time. Returns how many finished."""
    async with platform_session() as s:
        org_ids = list((await s.scalars(select(Experiment.organization_id).where(Experiment.status == "running")
                                        .group_by(Experiment.organization_id))).all())
    finished = 0
    for org_id in org_ids:
        try:
            async with SessionLocal() as s:
                set_tenant(s, org_id)
                done = await svc.evaluate_org(s, now=now)
                await s.commit()
            for exp in done:
                await svc.notify_done(exp)
            finished += len(done)
        except Exception:  # noqa: BLE001
            log.exception("A/B test check failed for organization %s", org_id)
    return finished


@periodic(3600, name="experiments_evaluate", offline_ok=True)
async def evaluate_running() -> None:
    await run_once()


__all__ = ["run_once"]
