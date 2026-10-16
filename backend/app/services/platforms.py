"""What each social platform accepts. Shared by content generation, media and publishing."""

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
    Platform("x", "X (Twitter)", 280, ("text", "link", "image", "gif", "video"), max_hashtags=2, image_size=(1200, 675), max_images=4,
             notes="On hold: publish by hand (the API is paid).", style="Concise, punchy, under 280 characters."),
    Platform("manual", "Other (publish by hand)", 3000, ("text", "link", "image", "carousel", "gif", "video", "animation", "document"),
             max_hashtags=5, max_images=10, notes="The app prepares the post; you copy and post it yourself."),
]}

POST_TYPES: dict[str, str] = {
    "text": "Text only", "link": "Link with preview", "image": "Single image", "carousel": "Carousel (several images)",
    "gif": "GIF", "video": "Short video", "animation": "Animated graphic", "document": "Document (PDF slides)",
}


def supported_types(platform: str) -> tuple[str, ...]:
    p = PLATFORMS.get(platform)
    return p.post_types if p else ("text",)
