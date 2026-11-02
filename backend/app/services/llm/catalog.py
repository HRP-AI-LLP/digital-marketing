"""Catalog of every external service the Integrations page can configure.

Fields per provider:
  kind      llm | image | video | tts | stock | search | seo_data | media_host | social_app | google | social_data | website | smtp | telegram
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
      free="Free, runs on this laptop (not reachable from the cloud: use Ollama Cloud there)", docs="https://ollama.com/library",
      examples=("qwen2.5:7b", "llama3.1:8b", "gemma2:9b", "nomic-embed-text")),
    P("ollama_cloud", "llm", "Ollama Cloud (hosted Ollama models)", "openai", "https://ollama.com/v1",
      free="Free plan with hourly and weekly usage limits (key from ollama.com/settings/keys); works in the cloud too",
      docs="https://ollama.com/settings/keys",
      examples=("gpt-oss:120b", "gpt-oss:20b", "deepseek-v3.1:671b", "qwen3-coder:480b", "gemma4:31b")),
    P("ollama_remote", "llm", "Ollama on your own server (remote)", "openai", "https://YOUR-SERVER:11434/v1", key=False,
      free="Free, your own hardware; must be reachable over HTTPS from the studio",
      docs="https://docs.ollama.com/faq#how-do-i-configure-ollama-server",
      examples=("qwen2.5:7b", "llama3.1:8b", "nomic-embed-text"),
      fields=(("api_key", "API key or bearer token (optional, if your server or proxy needs one)", True),)),
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
    P("perplexity", "llm", "Perplexity (Sonar, answers with web search)", "openai", "https://api.perplexity.ai", free="Paid",
      docs="https://www.perplexity.ai/account/api/keys", examples=("sonar", "sonar-pro")),
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
    # more free tiers and free trial credits (all OpenAI-compatible; add several keys per connection for more quota)
    P("github_models", "llm", "GitHub Models", "openai", "https://models.github.ai/inference",
      free="Free with a GitHub account (rate-limited; use a personal access token with 'models: read')",
      docs="https://github.com/marketplace/models", examples=("openai/gpt-4.1-mini", "meta/Llama-3.3-70B-Instruct", "deepseek/DeepSeek-V3-0324")),
    P("sambanova", "llm", "SambaNova Cloud", "openai", "https://api.sambanova.ai/v1", free="Free tier with rate limits",
      docs="https://cloud.sambanova.ai/apis", examples=("Meta-Llama-3.3-70B-Instruct", "DeepSeek-V3-0324")),
    P("nvidia_nim", "llm", "NVIDIA NIM (build.nvidia.com)", "openai", "https://integrate.api.nvidia.com/v1",
      free="Free credits for development", docs="https://build.nvidia.com/explore/discover",
      examples=("meta/llama-3.3-70b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1", "nvidia/nv-embedqa-e5-v5")),
    P("cloudflare_ai", "llm", "Cloudflare Workers AI", "openai", "https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID/ai/v1",
      free="Free daily allowance (put your account ID in the address)", docs="https://dash.cloudflare.com/profile/api-tokens",
      examples=("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/qwen/qwen2.5-coder-32b-instruct", "@cf/baai/bge-m3")),
    P("cohere", "llm", "Cohere", "openai", "https://api.cohere.ai/compatibility/v1", free="Free trial key (monthly limit)",
      docs="https://dashboard.cohere.com/api-keys", examples=("command-a-03-2025", "command-r7b-12-2024", "embed-v4.0")),
    P("deepinfra", "llm", "DeepInfra", "openai", "https://api.deepinfra.com/v1/openai", free="Free trial credits, then low cost",
      docs="https://deepinfra.com/dash/api_keys", examples=("meta-llama/Llama-3.3-70B-Instruct-Turbo", "BAAI/bge-m3")),
    P("fireworks", "llm", "Fireworks AI", "openai", "https://api.fireworks.ai/inference/v1", free="Free trial credits, then low cost",
      docs="https://fireworks.ai/account/api-keys", examples=("accounts/fireworks/models/llama-v3p3-70b-instruct",)),
    P("hyperbolic", "llm", "Hyperbolic", "openai", "https://api.hyperbolic.xyz/v1", free="Free trial credits, then low cost",
      docs="https://app.hyperbolic.xyz/settings", examples=("meta-llama/Llama-3.3-70B-Instruct",)),
    P("novita", "llm", "Novita AI", "openai", "https://api.novita.ai/v3/openai", free="Free trial credits, then low cost",
      docs="https://novita.ai/settings/key-management", examples=("meta-llama/llama-3.3-70b-instruct",)),
    P("jina", "llm", "Jina AI (embeddings)", "openai", "https://api.jina.ai/v1", free="Free tokens for every new key",
      docs="https://jina.ai/api-dashboard/key-manager", examples=("jina-embeddings-v3",)),
    P("zai", "llm", "Z.ai (Zhipu GLM)", "openai", "https://api.z.ai/api/paas/v4",
      free="Free models (GLM Flash) plus paid ones", docs="https://z.ai/manage-apikey/apikey-list",
      examples=("glm-4.5-flash", "glm-4.5-air", "embedding-3")),
    P("dashscope", "llm", "Alibaba Cloud Model Studio (Qwen)", "openai", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
      free="Free quota for new accounts (per model), then low cost", docs="https://modelstudio.console.alibabacloud.com/",
      examples=("qwen-plus", "qwen-turbo", "qwen-max", "text-embedding-v3")),
    P("siliconflow", "llm", "SiliconFlow", "openai", "https://api.siliconflow.com/v1",
      free="Free models and sign-up credits", docs="https://cloud.siliconflow.com/account/ak",
      examples=("Qwen/Qwen2.5-7B-Instruct", "THUDM/glm-4-9b-chat", "BAAI/bge-m3")),
    P("scaleway", "llm", "Scaleway Generative APIs (Europe)", "openai", "https://api.scaleway.ai/v1",
      free="Free tier (1M tokens), then pay as you go", docs="https://console.scaleway.com/iam/api-keys",
      examples=("llama-3.3-70b-instruct", "mistral-small-3.2-24b-instruct-2506", "bge-multilingual-gemma2")),
    P("ovh_ai", "llm", "OVHcloud AI Endpoints", "openai", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", key=False,
      free="Free without a key at low rate limits; a free key raises them", docs="https://endpoints.ai.cloud.ovh.net/",
      examples=("Meta-Llama-3_3-70B-Instruct", "Mistral-Small-3.2-24B-Instruct-2506", "bge-multilingual-gemma2"),
      fields=(("api_key", "API key (optional)", True),)),
    P("nebius", "llm", "Nebius AI Studio", "openai", "https://api.studio.nebius.com/v1",
      free="Free trial credits, then low cost", docs="https://studio.nebius.com/settings/api-keys",
      examples=("meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen3-235B-A22B", "BAAI/bge-en-icl")),
    P("llm7", "llm", "LLM7.io (free gateway)", "openai", "https://api.llm7.io/v1", key=False,
      free="Free, no key needed (a free token from token.llm7.io raises the limits)", docs="https://llm7.io",
      examples=("gpt-4.1-nano", "mistral-small-3.1-24b-instruct-2503", "deepseek-r1"),
      fields=(("api_key", "Token (optional)", True),)),
    P("voyage", "llm", "Voyage AI (embeddings)", "openai", "https://api.voyageai.com/v1",
      free="Free tokens for every account (embeddings only)", docs="https://dashboard.voyageai.com/api-keys",
      examples=("voyage-3.5-lite", "voyage-3.5")),
    P("jan", "llm", "Jan (local)", "openai", "http://localhost:1337/v1", key=False, local=True, free="Free, runs on this laptop",
      docs="https://jan.ai"),
    P("localai", "llm", "LocalAI / llama.cpp server (local)", "openai", "http://localhost:8080/v1", key=False, local=True,
      free="Free, runs on this laptop", docs="https://localai.io"),
    P("vllm", "llm", "vLLM server (local or your own GPU)", "openai", "http://localhost:8000/v1", key=False, local=True,
      free="Free, runs on your own machine", docs="https://docs.vllm.ai"),
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
    P("pollinations", "image", "Pollinations.ai (free, no account needed)", "pollinations", "https://image.pollinations.ai",
      key=False, free="Free, no key needed (optional free token for higher limits)", docs="https://pollinations.ai",
      examples=("flux", "turbo"), fields=(("api_key", "Token (optional, from auth.pollinations.ai)", True),)),
    P("cloudflare_images", "image", "Cloudflare Workers AI images", "cf_image",
      "https://api.cloudflare.com/client/v4/accounts/YOUR_ACCOUNT_ID/ai/run",
      free="Free daily allowance (put your account ID in the address)", docs="https://developers.cloudflare.com/workers-ai/models/",
      examples=("@cf/black-forest-labs/flux-1-schnell", "@cf/stabilityai/stable-diffusion-xl-base-1.0",
                "@cf/bytedance/stable-diffusion-xl-lightning")),
    P("nvidia_images", "image", "NVIDIA NIM images (build.nvidia.com)", "nvidia_image", "https://ai.api.nvidia.com/v1/genai",
      free="Free developer credits", docs="https://build.nvidia.com/black-forest-labs/flux_1-schnell",
      examples=("black-forest-labs/flux.1-schnell", "black-forest-labs/flux.1-dev", "stabilityai/stable-diffusion-3-medium")),
    P("siliconflow_images", "image", "SiliconFlow images", "siliconflow_image", "https://api.siliconflow.com/v1",
      free="Free models (e.g. Kolors) and sign-up credits", docs="https://docs.siliconflow.com",
      examples=("Kwai-Kolors/Kolors", "black-forest-labs/FLUX.1-schnell")),
    P("ai_horde", "image", "AI Horde (free, community GPUs)", "horde", "https://aihorde.net/api/v2", key=False,
      free="Free; slower (queued). Optional free key from aihorde.net/register for priority", docs="https://aihorde.net",
      examples=("AlbedoBase XL (SDXL)", "Juggernaut XL", "stable_diffusion"),
      fields=(("api_key", "API key (optional; 0000000000 is anonymous)", True),)),
    P("deepinfra_images", "image", "DeepInfra images", "openai_image", "https://api.deepinfra.com/v1/openai",
      free="Free trial credits, then low cost", docs="https://deepinfra.com/dash/api_keys",
      examples=("black-forest-labs/FLUX-1-schnell", "stabilityai/sdxl-turbo")),
    P("nebius_images", "image", "Nebius AI Studio images", "openai_image", "https://api.studio.nebius.com/v1",
      free="Free trial credits, then low cost", docs="https://studio.nebius.com/settings/api-keys",
      examples=("black-forest-labs/flux-schnell", "stability-ai/sdxl")),
    P("zai_images", "image", "Z.ai CogView images", "openai_image", "https://api.z.ai/api/paas/v4",
      free="Free credits on sign-up, then low cost", docs="https://z.ai/manage-apikey/apikey-list",
      examples=("cogview-4-250304",)),
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
    P("openverse", "stock", "Openverse (Creative Commons photos)", "openverse", "https://api.openverse.org/v1", key=False,
      free="Free, no key needed", docs="https://api.openverse.org/v1/"),

    # ---------------------------------------------------------------- web search (competitor discovery)
    P("ddgs", "search", "DuckDuckGo (no key)", "ddgs", key=False, free="Free"),
    P("searxng", "search", "SearXNG (self-hosted)", "searxng", "http://localhost:8888", key=False, free="Free, local container"),
    P("brave", "search", "Brave Search API", "brave", "https://api.search.brave.com/res/v1", free="Free tier",
      docs="https://brave.com/search/api/"),
    P("tavily", "search", "Tavily", "tavily", "https://api.tavily.com", free="Free monthly credits", docs="https://tavily.com"),
    P("serper", "search", "Serper (Google results)", "serper", "https://google.serper.dev", free="Free trial credits",
      docs="https://serper.dev"),

    # ---------------------------------------------------------------- keyword data (volumes, difficulty, rankings)
    P("dataforseo", "seo_data", "DataForSEO (keyword volumes, gaps and rankings)", "dataforseo", "https://api.dataforseo.com/v3",
      key=False, free="Optional, pay as you go (about $0.002 per ranking check, $0.01–0.05 per report). Keyword tools work free without it.",
      docs="https://app.dataforseo.com/api-access",
      fields=(("login", "API login (email)", False), ("password", "API password", True))),

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
    P("x_app", "social_app", "X (Twitter) app", "oauth", key=False, free="Free tier is write-limited; paid plans for more",
      docs="https://developer.x.com/en/portal/dashboard",
      fields=(("client_id", "OAuth 2.0 Client ID", False), ("client_secret", "OAuth 2.0 Client secret", True))),
    P("tiktok_app", "social_app", "TikTok app (Content Posting API)", "oauth", key=False, free="Free",
      docs="https://developers.tiktok.com/apps/",
      fields=(("client_id", "Client key", False), ("client_secret", "Client secret", True))),
    P("reddit_app", "social_app", "Reddit app (posting)", "oauth", key=False,
      free="Free for moderate, non-commercial use (Reddit's Data API terms)", docs="https://www.reddit.com/prefs/apps",
      fields=(("client_id", "Client ID (the code under the app's name)", False), ("client_secret", "Secret", True),
              ("developer_username", "Your Reddit username (Reddit asks apps to name their developer)", False))),
    P("canva_app", "social_app", "Canva integration (Connect API, design import)", "oauth", key=False, free="Free",
      docs="https://www.canva.com/developers/integrations/connect-api",
      fields=(("client_id", "Client ID", False), ("client_secret", "Client secret", True))),
    P("google_app", "google","Google Cloud OAuth client (Business Profile, Analytics, Search Console, YouTube)", "oauth",
      key=False, free="Free", docs="https://console.cloud.google.com/apis/credentials",
      fields=(("client_id", "Client ID", False), ("client_secret", "Client secret", True),
              ("pagespeed_key", "PageSpeed Insights API key (optional)", True))),

    # ---------------------------------------------------------------- company website (publish SEO articles)
    P("wordpress", "website", "WordPress site", "wordpress", key=False, free="Free (built into WordPress 5.6+)",
      docs="https://wordpress.org/documentation/article/application-passwords/",
      fields=(("site_url", "Site address, e.g. https://www.example.com", False), ("username", "WordPress username", False),
              ("app_password", "Application password (Users → Profile → Application passwords)", True),
              ("indexnow_key", "IndexNow key (optional, see the article's Publish panel)", False))),
    P("website_webhook", "website", "Custom website (signed webhook)", "webhook", key=False, free="Free",
      fields=(("webhook_url", "Webhook address that receives articles (https://…)", False),
              ("signing_secret", "Signing secret (used for the X-HRP-Signature header)", True),
              ("article_url", "Article address pattern, e.g. https://www.example.com/blog/{slug}", False),
              ("indexnow_key", "IndexNow key (optional, see the article's Publish panel)", False))),
    # ---------------------------------------------------------------- public social data (Social presence and Listening)
    P("youtube_data", "social_data", "YouTube Data API key (social presence and listening)", "youtube_data",
      "https://www.googleapis.com/youtube/v3", free="Free (10,000 units a day)",
      docs="https://console.cloud.google.com/apis/library/youtube.googleapis.com"),
    P("github_api", "social_data", "GitHub token (optional, higher limits)", "github_api", "https://api.github.com",
      free="Free; works without a token at lower limits", docs="https://github.com/settings/tokens"),
    P("x_api", "social_data", "X (Twitter) API bearer token (social presence and listening; paid, optional)", "x_api",
      "https://api.x.com/2", free="Paid X API plan; without it X uses limited public data and listening skips X", docs="https://developer.x.com/en/portal/dashboard"),

    # ---------------------------------------------------------------- notifications
    P("smtp", "smtp", "Email (SMTP)", "smtp", key=False,
      free="Your own mailbox: GoDaddy, Microsoft 365, Google Workspace/Gmail, Zoho or any SMTP server",
      docs="https://www.godaddy.com/help/use-imap-settings-to-add-my-professional-email-to-a-client-32204",
      fields=(("host", "SMTP server", False), ("port", "Port", False),
              ("security", "Security (ssl, starttls or none)", False), ("username", "Username (usually the full email address)", False),
              ("password", "Password / app password", True), ("from_email", "Send from (email address)", False),
              ("from_name", "Sender name (e.g. HRP AI LLP)", False), ("reply_to", "Replies go to (optional)", False))),
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
