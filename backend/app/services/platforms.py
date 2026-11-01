"""What each social platform accepts. Shared by content generation, media and publishing.

Some platforms can be switched off for the whole studio (DISABLED_PLATFORMS, default "tiktok" because TikTok is
banned in India). A switched-off platform keeps its spec and adapter, so existing accounts and posts still work and
it can be turned back on, but it isn't offered anywhere a person picks a platform: use `offered_platforms()` /
`is_offered()` for those lists and PLATFORMS for lookups.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    max_chars: int
    post_types: tuple[str, ...]
    max_hashtags: int = 5
    title_chars: int = 0  # >0 when the platform has a separate title (Pinterest, YouTube)
    image_size: tuple[int, int] = (1080, 1080)
    video_size: tuple[int, int] = (1080, 1920)
    max_images: int = 1
    needs_public_media: bool = False
    oauth_app: str | None = None  # provider id in the integrations catalog holding app credentials
    notes: str = ""
    style: str = ""  # writing guidance for the model
    extra: dict = field(default_factory=dict)
    manual: bool = False  # no API: posts are prepared and published by hand
    unavailable_note: str = ""  # shown on existing accounts when the platform is switched off


PLATFORMS: dict[str, Platform] = {p.id: p for p in [
    Platform("linkedin_org", "LinkedIn company page", 3000, ("text", "link", "image", "carousel", "gif", "video", "animation", "document"),
             max_hashtags=5, image_size=(1200, 1200), video_size=(1080, 1350), max_images=9, oauth_app="linkedin_app",
             style="Professional, insight-led. Strong first line (hook) before the 'see more' cut at ~200 characters. "
                   "Short paragraphs, line breaks, 3–5 relevant hashtags at the end."),
    Platform("linkedin_person", "LinkedIn profile", 3000, ("text", "link", "image", "carousel", "gif", "video", "animation", "document"),
             max_hashtags=5, image_size=(1200, 1200), video_size=(1080, 1350), max_images=9, oauth_app="linkedin_app",
             style="First person, thoughtful, professional. Hook in the first line, short paragraphs, 3–5 hashtags."),
    Platform("facebook_page", "Facebook page", 2000, ("text", "link", "image", "carousel", "gif", "video", "animation"),
             max_hashtags=3, image_size=(1200, 1200), video_size=(1080, 1920), max_images=10, oauth_app="meta_app",
             style="Conversational and warm, invite comments with a question. 1–3 hashtags."),
    Platform("instagram", "Instagram", 2200, ("image", "carousel", "video", "animation", "gif"),
             max_hashtags=15, image_size=(1080, 1350), video_size=(1080, 1920), max_images=10, needs_public_media=True,
             oauth_app="meta_app",
             style="Visual-first caption: hook line, 2–4 short lines, a call to action, then a block of 8–15 hashtags."),
    Platform("threads", "Threads", 500, ("text", "image", "carousel", "video"), max_hashtags=1, image_size=(1080, 1350),
             max_images=10, needs_public_media=True, oauth_app="meta_app",
             style="Casual, punchy, conversational. One idea. At most one hashtag."),
    Platform("pinterest", "Pinterest", 500, ("image", "carousel", "video", "animation"), max_hashtags=5, title_chars=100,
             image_size=(1000, 1500), video_size=(1080, 1920), max_images=5, oauth_app="pinterest_app",
             style="Keyword-rich title and description written for search; describe the value of clicking through."),
    Platform("gbp", "Google Business Profile", 1500, ("text", "image", "link"), max_hashtags=0, image_size=(1200, 900),
             needs_public_media=True, oauth_app="google_app",
             style="Local-business update: clear offer or news, plain language, a call to action. No hashtags."),
    Platform("youtube", "YouTube Shorts", 5000, ("video", "animation"), max_hashtags=5, title_chars=100, video_size=(1080, 1920),
             oauth_app="google_app", style="Title under 100 characters; description with context and a link; 3–5 hashtags."),
    Platform("bluesky", "Bluesky", 300, ("text", "link", "image", "video"), max_hashtags=2, image_size=(1200, 1200), max_images=4,
             style="Short, direct, human. Fits in 300 characters including hashtags."),
    Platform("mastodon", "Mastodon", 500, ("text", "link", "image", "gif", "video"), max_hashtags=4, max_images=4,
             style="Plain, informative, CamelCase hashtags for accessibility."),
    Platform("x", "X (Twitter)", 280, ("text", "link", "image", "carousel", "gif", "video", "animation"), max_hashtags=2,
             image_size=(1200, 675), video_size=(1080, 1920), max_images=4, oauth_app="x_app",
             notes="Longer text is posted as a thread. Up to 4 images, or one GIF or video. The free X API tier allows "
                   "only a few posts a day.",
             style="Concise, punchy, under 280 characters including hashtags and links (a link counts as 23). "
                   "One clear idea, 1–2 hashtags at most."),
    Platform("tiktok", "TikTok", 2200, ("video", "animation", "image", "carousel"), max_hashtags=5,
             image_size=(1080, 1920), video_size=(1080, 1920), max_images=35, needs_public_media=True,
             oauth_app="tiktok_app",
             notes="Vertical video (or a photo carousel). Until TikTok audits your app, posts are visible only to you.",
             style="Caption for a short vertical video: a hook in the first line, one or two short lines of context, "
                   "a call to action, then 3–5 relevant hashtags. Casual, energetic, no corporate jargon.",
             unavailable_note="TikTok isn't available in India, so it is no longer offered for new accounts or posts. "
                              "This account keeps working until you remove it."),
    Platform("telegram", "Telegram channel", 4096, ("text", "link", "image", "carousel", "gif", "video", "animation", "document"),
             max_hashtags=3, image_size=(1280, 1280), video_size=(1080, 1920), max_images=10,
             notes="Text up to 4,096 characters. With media the caption can hold 1,024 characters; longer text is sent "
                   "as its own message right after the media. Telegram doesn't share view counts with bots.",
             style="Channel update for subscribers who opted in: a bold-worthy first line that says what's new, then "
                   "2–4 short paragraphs or a few bullet points (use • or –), and a clear call to action with the link. "
                   "Friendly, useful, no clickbait. Emojis sparingly as visual markers. 0–3 hashtags at the end, only "
                   "if they help people find related posts in the channel."),
    Platform("discord", "Discord channel", 2000, ("text", "link", "image", "carousel", "gif", "video", "animation", "document"),
             max_hashtags=0, image_size=(1200, 675), video_size=(1920, 1080), max_images=10,
             notes="Up to 2,000 characters and 10 files (25 MB each unless the server is boosted). Posted through a "
                   "channel webhook; mentions like @everyone are never triggered.",
             style="Community announcement in a Discord server: conversational and direct, like a team member talking "
                   "to members. Start with what's new in one line, then short lines or bullets. Discord markdown is "
                   "fine (**bold** for the key point). No hashtags: Discord doesn't use them. End with a question or "
                   "a clear next step."),
    Platform("reddit", "Reddit", 40000, ("text", "link", "image"), max_hashtags=0, title_chars=300,
             image_size=(1200, 1200), needs_public_media=True, oauth_app="reddit_app",
             notes="Needs a title (up to 300 characters). Text posts use Reddit markdown; link posts are the title "
                   "and the link; images are posted as a link to the image. Each subreddit has its own rules on "
                   "self-promotion: post where your content genuinely helps.",
             style="Write for Reddit's community norms: a specific, honest, non-clickbait title; a body that gives "
                   "real value up front (what you learned, how to do it, numbers you actually have), plain first-person "
                   "voice, short paragraphs, markdown lists where useful, about 150–400 words. No marketing slogans, no hashtags, no emojis. "
                   "Mention the company or product at most once, transparently, and invite discussion."),
    Platform("whatsapp", "WhatsApp channel (post by hand)", 1024,
             ("text", "link", "image", "gif", "video", "animation", "document"), max_hashtags=0, image_size=(1080, 1080),
             video_size=(1080, 1920), max_images=1, manual=True,
             notes="WhatsApp has no API for posting to Channels, so the app prepares the update and you post it: "
                   "Open WhatsApp (or copy the text), attach the media, and send it to your channel.",
             style="WhatsApp channel update read on a phone: a short first line that works as a headline (WhatsApp "
                   "*bold* with single asterisks is fine), 2–4 short lines, one clear call to action with the link on "
                   "its own line. Warm and personal, like a message from the team. 1–3 relevant emojis at most. No "
                   "hashtags (they aren't clickable on WhatsApp). Keep it under about 700 characters."),
    Platform("manual", "Other (publish by hand)", 3000, ("text", "link", "image", "carousel", "gif", "video", "animation", "document"),
             max_hashtags=5, max_images=10, manual=True, notes="The app prepares the post; you copy and post it yourself."),
]}

POST_TYPES: dict[str, str] = {
    "text": "Text only", "link": "Link with preview", "image": "Single image", "carousel": "Carousel (several images)",
    "gif": "GIF", "video": "Short video", "animation": "Animated graphic", "document": "Document (PDF slides)",
}


def supported_types(platform: str) -> tuple[str, ...]:
    p = PLATFORMS.get(platform)
    return p.post_types if p else ("text",)


def disabled_platforms() -> set[str]:
    """Platforms switched off for the studio (DISABLED_PLATFORMS, comma separated; default "tiktok")."""
    from app.core.config import get_settings

    raw = get_settings().disabled_platforms or ""
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def is_offered(platform: str) -> bool:
    """Whether people can pick this platform for new accounts, media sizes and filters."""
    return platform in PLATFORMS and platform not in disabled_platforms()


def offered_platforms() -> list[Platform]:
    off = disabled_platforms()
    return [p for p in PLATFORMS.values() if p.id not in off]


def unavailable_note(platform: str) -> str | None:
    """Plain note for existing accounts on a switched-off platform (None when the platform is offered)."""
    p = PLATFORMS.get(platform)
    if p is None or is_offered(platform):
        return None
    return p.unavailable_note or (f"{p.label} is switched off for this studio, so it isn't offered for new accounts. "
                                  "This account keeps working until you remove it.")


def hidden_app_providers() -> set[str]:
    """Developer-app providers used only by switched-off platforms (e.g. "tiktok_app")."""
    off = disabled_platforms()
    hidden = {p.oauth_app for p in PLATFORMS.values() if p.id in off and p.oauth_app}
    still = {p.oauth_app for p in PLATFORMS.values() if p.id not in off and p.oauth_app}
    return hidden - still
