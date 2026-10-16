"""Catalog of every external service the Integrations page can configure.

Fields per provider:
  kind      llm | image | video | tts | stock | search | media_host | social_app | google | smtp | telegram
  style     how we talk to it (openai = OpenAI-compatible REST, anthropic, gemini_image, hf_image, ...)
  base_url  default endpoint (editable)
  key       whether an API key is required
  fields    extra secret/setting fields shown in the form: (name, label, secret?)
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    kind: str
    label: str
    style: str
    base_url: str = ""
    key: bool = True
    free: str = ""  # short note on free tier / cost
    docs: str = ""
    examples: tuple[str, ...] = ()  # example model ids (the form can also fetch the live list)
    fields: tuple[tuple[str, str, bool], ...] = field(default_factory=tuple)
    local: bool = False


P = ProviderSpec

CATALOG: list[ProviderSpec] = [
    # ---------------------------------------------------------------- text models (LLM)
    P("ollama", "llm", "Ollama (local)", "openai", "http://localhost:11434/v1", key=False, local=True,
      free="Free, runs on this laptop", docs="https://ollama.com/library",
      examples=("qwen2.5:7b", "llama3.1:8b", "gemma2:9b", "nomic-embed-text")),
    P("lmstudio", "llm", "LM Studio (local)", "openai", "http://localhost:1234/v1", key=False, local=True,
      free="Free, runs on this laptop", docs="https://lmstudio.ai"),
    P("gemini", "llm", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
      free="Free tier with daily limits", docs="https://aistudio.google.com/apikey",
      examples=("gemini-2.5-flash", "gemini-2.5-pro", "text-embedding-004")),
    P("openai", "llm", "OpenAI", "openai", "https://api.openai.com/v1", free="Paid",
      docs="https://platform.openai.com/api-keys", examples=("gpt-4o-mini", "gpt-4o", "text-embedding-3-small")),
    P("anthropic", "llm", "Anthropic Claude", "anthropic", "https://api.anthropic.com", free="Paid",
      docs="https://console.anthropic.com/settings/keys"),
    P("xai", "llm", "xAI Grok", "openai", "https://api.x.ai/v1", free="Paid", docs="https://console.x.ai"),
    P("groq", "llm", "Groq", "openai", "https://api.groq.com/openai/v1", free="Free tier with rate limits",
      docs="https://console.groq.com/keys", examples=("llama-3.3-70b-versatile", "llama-3.1-8b-instant")),
    P("openrouter", "llm", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
      free="Some free models (':free' suffix)", docs="https://openrouter.ai/keys"),
    P("huggingface", "llm", "Hugging Face Inference", "openai", "https://router.huggingface.co/v1",
      free="Free monthly credits", docs="https://huggingface.co/settings/tokens"),
    P("mistral", "llm", "Mistral", "openai", "https://api.mistral.ai/v1", free="Free tier", docs="https://console.mistral.ai"),
    P("deepseek", "llm", "DeepSeek", "openai", "https://api.deepseek.com/v1", free="Paid (low cost)", docs="https://platform.deepseek.com"),
    P("together", "llm", "Together AI", "openai", "https://api.together.xyz/v1", free="Paid", docs="https://api.together.ai"),
    P("cerebras", "llm", "Cerebras", "openai", "https://api.cerebras.ai/v1", free="Free tier", docs="https://cloud.cerebras.ai"),
    P("custom_openai", "llm", "Other OpenAI-compatible API", "openai", "", free="Depends on provider"),

    # ---------------------------------------------------------------- images
    P("openai_images", "image", "OpenAI Images", "openai_image", "https://api.openai.com/v1", free="Paid",
      examples=("gpt-image-1", "dall-e-3")),
    P("gemini_images", "image", "Google Gemini image", "gemini_image", "https://generativelanguage.googleapis.com/v1beta",
      free="Limited free tier", examples=("gemini-2.5-flash-image",)),
    P("xai_images", "image", "xAI Grok image", "openai_image", "https://api.x.ai/v1", free="Paid", examples=("grok-2-image",)),
    P("together_images", "image", "Together AI images", "openai_image", "https://api.together.xyz/v1", free="Some free models",
      examples=("black-forest-labs/FLUX.1-schnell-Free",)),
    P("hf_images", "image", "Hugging Face text-to-image", "hf_image", "https://router.huggingface.co/hf-inference/models",
      free="Free monthly credits", examples=("black-forest-labs/FLUX.1-schnell", "stabilityai/stable-diffusion-xl-base-1.0")),
    P("stability", "image", "Stability AI", "stability", "https://api.stability.ai", free="Paid (credits)",
      examples=("core", "ultra")),
    P("a1111", "image", "Stable Diffusion WebUI / Forge (local)", "a1111", "http://127.0.0.1:7860", key=False, local=True,
      free="Free, runs on this laptop (GPU)"),
    P("comfyui", "image", "ComfyUI (local)", "comfyui", "http://127.0.0.1:8188", key=False, local=True,
      free="Free, runs on this laptop (GPU)",
      fields=(("workflow_json", "Workflow (API format) with {{prompt}}, {{width}}, {{height}}, {{seed}} placeholders", False),)),

    # ---------------------------------------------------------------- video generation (optional, paid)
    P("replicate_video", "video", "Replicate (video models)", "replicate", "https://api.replicate.com/v1", free="Paid",
      examples=("minimax/video-01",)),

    # ---------------------------------------------------------------- voice-over
    P("edge_tts", "tts", "Microsoft Edge voices", "edge_tts", key=False, free="Free",
      examples=("en-IN-NeerjaNeural", "en-IN-PrabhatNeural", "en-US-AriaNeural", "hi-IN-SwaraNeural")),
    P("openai_tts", "tts", "OpenAI text-to-speech", "openai_tts", "https://api.openai.com/v1", free="Paid",
      examples=("gpt-4o-mini-tts", "tts-1")),

    # ---------------------------------------------------------------- stock photos & clips
    P("pexels", "stock", "Pexels", "pexels", "https://api.pexels.com", free="Free API key", docs="https://www.pexels.com/api/"),
    P("pixabay", "stock", "Pixabay", "pixabay", "https://pixabay.com/api", free="Free API key", docs="https://pixabay.com/api/docs/"),
    P("unsplash", "stock", "Unsplash", "unsplash", "https://api.unsplash.com", free="Free API key", docs="https://unsplash.com/developers"),

    # ---------------------------------------------------------------- web search (competitor discovery)
    P("ddgs", "search", "DuckDuckGo (no key)", "ddgs", key=False, free="Free"),
    P("searxng", "search", "SearXNG (self-hosted)", "searxng", "http://localhost:8888", key=False, free="Free, local container"),
    P("brave", "search", "Brave Search API", "brave", "https://api.search.brave.com/res/v1", free="Free tier",
      docs="https://brave.com/search/api/"),
    P("tavily", "search", "Tavily", "tavily", "https://api.tavily.com", free="Free monthly credits", docs="https://tavily.com"),
    P("serper", "search", "Serper (Google results)", "serper", "https://google.serper.dev", free="Free trial credits",
      docs="https://serper.dev"),

    # ---------------------------------------------------------------- public media hosting (Instagram, Threads, GBP need a URL)
    P("cloudinary", "media_host", "Cloudinary", "cloudinary", key=True, free="Free plan",
      docs="https://console.cloudinary.com",
      fields=(("cloud_name", "Cloud name", False), ("api_secret", "API secret", True))),
    P("public_base_url", "media_host", "My own public URL (e.g. Cloudflare Tunnel)", "public_base_url", key=False, free="Free",
      fields=(("public_base_url", "Public base URL that reaches this app's API", False),)),

    # ---------------------------------------------------------------- social developer apps (OAuth)
    P("meta_app", "social_app", "Meta app (Facebook Pages, Instagram, Threads)", "oauth", key=False, free="Free",
      docs="https://developers.facebook.com/apps",
      fields=(("client_id", "App ID", False), ("client_secret", "App secret", True),
              ("threads_app_id", "Threads app ID (optional, if different)", False),
              ("threads_app_secret", "Threads app secret (optional)", True))),
    P("linkedin_app", "social_app", "LinkedIn app", "oauth", key=False, free="Free",
      docs="https://www.linkedin.com/developers/apps",
      fields=(("client_id", "Client ID", False), ("client_secret", "Client secret", True),
              ("pages_client_id", "Company pages app: Client ID (optional, app with only Community Management API)", False),
              ("pages_client_secret", "Company pages app: Client secret (optional)", True))),
    P("pinterest_app", "social_app", "Pinterest app", "oauth", key=False, free="Free",
      docs="https://developers.pinterest.com/apps/",
      fields=(("client_id", "App ID", False), ("client_secret", "App secret key", True))),
    P("google_app", "google", "Google Cloud OAuth client (Business Profile, Analytics, Search Console, YouTube)", "oauth",
      key=False, free="Free", docs="https://console.cloud.google.com/apis/credentials",
      fields=(("client_id", "Client ID", False), ("client_secret", "Client secret", True),
              ("pagespeed_key", "PageSpeed Insights API key (optional)", True))),

    # ---------------------------------------------------------------- notifications
    P("smtp", "smtp", "Email (SMTP)", "smtp", key=False, free="Free with Gmail app password",
      docs="https://support.google.com/accounts/answer/185833",
      fields=(("host", "SMTP host", False), ("port", "Port", False), ("username", "Username", False),
              ("password", "Password / app password", True), ("from_email", "From address", False),
              ("use_tls", "Use STARTTLS (true/false)", False))),
    P("telegram", "telegram", "Telegram bot", "telegram", key=True, free="Free", docs="https://t.me/BotFather",
      fields=(("bot_username", "Bot username (without @)", False),)),
]

BY_ID = {p.id: p for p in CATALOG}

# Tasks that route to text models. 'embed' routes to an embedding model.
TASKS: dict[str, str] = {
    "write": "Write social posts",
    "plan": "Plan topics and ideas",
    "qa": "Review drafts (quality check)",
    "extract": "Read websites and documents",
    "longform": "SEO articles and analysis",
    "image_prompt": "Describe images to generate",
    "embed": "Embeddings (duplicate check and search)",
}

MEDIA_TASKS: dict[str, str] = {
    "image": "Generate images",
    "tts": "Voice-over",
    "stock": "Stock photos and clips",
    "search": "Web search",
    "media_host": "Public media hosting",
    "video": "AI video clips",
}

# Everything Task routing can order, with the provider kind and the model type each step needs.
ROUTE_TASKS: dict[str, dict[str, str]] = {
    **{t: {"label": lbl, "kind": "llm", "model_type": "embed" if t == "embed" else "chat"} for t, lbl in TASKS.items()},
    "image": {"label": "Generate images", "kind": "image", "model_type": "image"},
    "tts": {"label": "Voice-over", "kind": "tts", "model_type": "voice"},
    "video": {"label": "AI video clips", "kind": "video", "model_type": "video"},
}
