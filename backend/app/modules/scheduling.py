"""Smart scheduling background work: evergreen recycling.

Periodic: evergreen_recycle (hourly) reshares one evergreen post per organization when one is due
(see app.services.evergreen). Best-time slots need no task of their own: the scheduler computes them while planning.
"""

import logging

from app.services import blog_autopilot, evergreen
from app.services.jobs import org_ids_active, periodic

log = logging.getLogger(__name__)


@periodic(3600, name="evergreen_recycle")
async def evergreen_recycle() -> None:
    for org_id in await org_ids_active():
        try:
            await evergreen.run_org(org_id)
        except Exception:
            log.exception("evergreen recycling failed for org %s", org_id)


@periodic(1800, name="blog_autopilot")
async def blog_autopilot_tick() -> None:
    """Writes the scheduled blog articles (Settings → Organization → Auto-approval → Blog autopilot)."""
    for org_id in await org_ids_active():
        try:
            await blog_autopilot.run_org(org_id)
        except Exception:
            log.exception("blog autopilot failed for org %s", org_id)
