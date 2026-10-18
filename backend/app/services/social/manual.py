"""Accounts without an API connection: 'Other', and X accounts added by hand (when the paid X API isn't used). The content engine
routes their posts to 'manual publish' before calling publish; this is the safety net."""

from app.models import MediaAsset, Post, SocialAccount
from app.services.social.common import PublishError


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    where = "X" if account.platform == "x" else (account.name or "this account")
    raise PublishError(
        f"{where} has no API connection, so publish this post by hand: copy the text, download the media and "
        "post it on the platform, then mark it as published."
    )


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    return None


async def refresh(session, account: SocialAccount) -> None:
    account.status = "manual"
