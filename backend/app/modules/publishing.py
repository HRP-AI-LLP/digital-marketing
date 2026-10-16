"""Publishing background module: account checks / token refresh, and the OAuth app tester."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.integrations import tester
from app.core.crypto import decrypt_json
from app.models import ProviderConfig, SocialAccount
from app.services.jobs import JobContext, enqueue_now, handler, org_ids_active, periodic
from app.services.publishing import refresh_account
from app.services.social import common

log = logging.getLogger("publishing")

REFRESH_EVERY_SECONDS = 6 * 3600
EXPIRY_WINDOW = timedelta(days=7)
SKIP = {"manual", "x"}


@handler("account.refresh")
async def account_refresh(ctx: JobContext) -> dict:
    """Refresh tokens that expire within 7 days and check every connected account.
    payload: {"account_id": "..."} to check one account only."""
    q = select(SocialAccount).where(SocialAccount.status != "removed", SocialAccount.platform.not_in(SKIP))
    if ctx.payload.get("account_id"):
        q = q.where(SocialAccount.id == uuid.UUID(str(ctx.payload["account_id"])))
    accounts = list((await ctx.session.scalars(q)).all())
    # Google first: Business Profile and YouTube accounts use its token.
    accounts.sort(key=lambda a: 0 if a.platform == "google" else 1)
    checked, expired, notify = 0, [], []
    ids = [a.id for a in accounts]
    for acc_id in ids:
        acc = await ctx.session.get(SocialAccount, acc_id)
        if acc is None:
            continue
        before = acc.status
        try:
            await refresh_account(ctx.session, acc)
        except Exception as e:  # noqa: BLE001 - one broken adapter must not lose other accounts' rotated tokens
            log.warning("refresh of account %s failed: %s", acc_id, e)
            await ctx.session.rollback()
            continue
        checked += 1
        settings = dict(acc.settings or {})
        if acc.status == "expired":
            expired.append(acc.name)
            if not settings.get("_issue_notified"):
                settings["_issue_notified"] = datetime.now(UTC).isoformat()
                notify.append((acc.name, acc.platform, acc.status_message))
        elif acc.status == "connected" and common.expiring(acc, EXPIRY_WINDOW) and acc.platform not in ("google",):
            # Still works but will expire soon and couldn't be refreshed automatically (e.g. no refresh token).
            key = "_expiry_notified"
            if not settings.get(key):
                settings[key] = datetime.now(UTC).isoformat()
                exp = acc.token_expires_at
                notify.append((acc.name, acc.platform,
                               f"Access expires on {exp:%d %b %Y}. Reconnect the account before then." if exp else None))
        elif acc.status == "connected":
            settings.pop("_issue_notified", None)
            if not common.expiring(acc, EXPIRY_WINDOW):
                settings.pop("_expiry_notified", None)
        if before != acc.status:
            log.info("account %s (%s): %s -> %s", acc.id, acc.platform, before, acc.status)
        acc.settings = settings
        await ctx.session.commit()  # per account, so rotated refresh tokens are saved immediately

    if notify and ctx.org_id:
        from app.services.notify import notify_org

        lines = [f"• {n} ({p}): {m or 'needs reconnecting'}" for n, p, m in notify]
        try:
            await notify_org(ctx.org_id, "account_issue",
                             "A social account needs attention" if len(notify) == 1 else
                             f"{len(notify)} social accounts need attention",
                             "\n".join(lines), link="/settings/accounts", permission="integration.manage")
        except Exception:
            log.exception("account issue notification failed")
    return {"checked": checked, "expired": expired, "notified": len(notify)}


@periodic(REFRESH_EVERY_SECONDS, name="account_refresh")
async def schedule_account_refresh() -> None:
    for org_id in await org_ids_active():
        await enqueue_now("account.refresh", org_id, {}, dedupe_key=f"account.refresh:{org_id}", max_attempts=1)


# ------------------------------------------------------------------ OAuth app tester
APP_TO_PROVIDERS = {"meta_app": ["meta", "threads"], "linkedin_app": ["linkedin"],
                    "pinterest_app": ["pinterest"], "google_app": ["google"]}


@tester("oauth")
async def test_oauth_app(cfg: ProviderConfig) -> str:
    sec = decrypt_json(cfg.secrets_enc)
    missing = [label for key, label in (("client_id", "client ID / app ID"), ("client_secret", "client secret"))
               if not (sec.get(key) or "").strip()]
    if missing:
        raise ValueError(f"Add the {' and '.join(missing)}.")
    uris = [common.redirect_uri(p) for p in APP_TO_PROVIDERS.get(cfg.provider, [])]
    if not uris:
        return "Credentials saved."
    return ("Credentials saved. Register this redirect URI in the developer app: " if len(uris) == 1 else
            "Credentials saved. Register these redirect URIs in the developer app: ") + " , ".join(uris) + \
        ". Then connect accounts in Settings → Social accounts."
