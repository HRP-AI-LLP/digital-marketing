"""Accounts without an API connection: 'Other', WhatsApp channels, and X accounts added by hand (when the paid X API isn't
used). The content engine routes their posts to 'manual publish' before calling publish; this is the safety net.

WhatsApp has no API for posting to Channels (and the Business API's broadcasts need approved templates and opted-in
contacts), so WhatsApp is a by-hand platform: the post page offers a wa.me share link that opens WhatsApp with the
text filled in."""

from urllib.parse import quote

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common
from app.services.social.common import PublishError

WA_SHARE = "https://wa.me/?text="


def whatsapp_share_url(text: str) -> str:
    """A link that opens WhatsApp (app or web) with the text ready to send or forward to a channel."""
    return WA_SHARE + quote(text or "", safe="")


def share_text(post: Post) -> str:
    """The text as it should be pasted: title (if any), text with hashtags, then the link."""
    body = common.compose_text(post, link=True)
    title = (post.title or "").strip()
    return f"{title}\n\n{body}".strip() if title and title not in body else body


def share_url(post: Post) -> str | None:
    return whatsapp_share_url(share_text(post)) if post.platform == "whatsapp" else None


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    where = {"x": "X", "whatsapp": "WhatsApp"}.get(account.platform) or account.name or "this account"
    raise PublishError(
        f"{where} has no API connection, so publish this post by hand: copy the text, download the media and "
        "post it on the platform, then mark it as published."
    )


async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    return None


async def refresh(session, account: SocialAccount) -> None:
    account.status = "manual"
