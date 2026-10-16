# Getting started

The order that gets HRP AI LLP posting fastest. Each step links to the page in the app.

## 1. Start the app

```powershell
cd "E:\HRP Projects\HRP\Digital Marketing"
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The first run after this update installs new Python packages and the Chromium browser used to render images
and videos (about 150 MB, once). Then open http://localhost:5180.

Three processes run in the background: the API, the **worker** (it plans, writes, renders and publishes on schedule)
and the web app. Logs are in `storage\logs\`. Posts only go out while the laptop is on; when it comes back, missed slots
inside the catch-up window are published, older ones are marked "missed" and you're notified.

## 2. Connect an AI model (Settings → Integrations → AI text models)

Pick at least one. You can add several and choose which one does what in **Task routing**.

| Option | Cost | Setup |
|---|---|---|
| **Ollama on this laptop** | Free | Install from ollama.com, then in PowerShell: `ollama pull qwen2.5:7b` and `ollama pull nomic-embed-text`. Add "Ollama (local)" with default model `qwen2.5:7b` and embedding model `nomic-embed-text`. Fits the 8 GB GPU. |
| **Google Gemini** | Free tier | Create a key at aistudio.google.com/apikey. Add "Google Gemini", model `gemini-2.5-flash`, embedding model `text-embedding-004`. |
| **Groq** | Free tier | Key from console.groq.com. Fast; good for writing and review. |
| OpenAI, Anthropic, xAI Grok, Mistral, DeepSeek, OpenRouter, Hugging Face, Together, Cerebras, LM Studio, any OpenAI-compatible API | Varies | Add the key; set prices and a monthly budget so paid models stop at your limit. |

Suggested routing: writing and review on Gemini or Groq (better English), with Ollama as backup;
embeddings on Ollama `nomic-embed-text` (free, private). Press **Test** on each integration.

## 3. Build the company profile (Company intelligence)

1. Check the website is set in **Settings → Organization** (https://hrpai.in).
2. Press **Analyze everything**. It reads the site, builds the profile (products, studios, services, use cases, clients),
   audits your digital presence, finds competitors and suggests topics. It takes a few minutes; you can leave the page.
3. Review **Company profile**: fix anything wrong and press **Confirm**. Every post is grounded in this.
4. Review **Competitors**: confirm the real ones, reject the rest. Confirmed competitors are re-read weekly.
5. Upload brochures, decks or case studies in **Sources** to give the writer more facts.

## 4. Set the brand (Settings → Brand and topics)

Voice, audience, rules, banned words, hashtags and calls to action; **Look** for colours, logo and the name shown on
images; **Topic pillars** (press "Add suggested pillars") and their weights; **Campaigns** for launches and festivals.

## 5. Connect social accounts (Settings → Social accounts)

Each platform needs a free developer app of your own; follow the **Setup guide** on each card (also in
`docs/SETUP_SOCIAL.md`). Put the app credentials in **Integrations → Social apps and Google**, register the redirect
URI shown on the card, then press **Connect**.

| Platform | What you need | Notes |
|---|---|---|
| LinkedIn profile | LinkedIn app with "Share on LinkedIn" + "Sign In with LinkedIn using OpenID Connect" | Works straight away. |
| LinkedIn company page | Same app + **Community Management API** approval | Apply on day one; approval takes time. Until then, use the profile or a manual account. |
| Facebook page + Instagram | Meta app with Facebook Login; Instagram must be a Business/Creator account linked to the page | Instagram needs a **public media host** (below). |
| Threads | Meta app with Threads API | Needs the public media host; may need an https redirect (tunnel). |
| Pinterest | Pinterest app | Trial access: pins visible only to you until Standard access is approved. |
| Google Business Profile, YouTube, Analytics, Search Console | Google Cloud OAuth client; enable the APIs listed in the guide | Business Profile API needs an access request. |
| Bluesky, Mastodon | App password / access token | No developer app needed. |
| X, anything else | Add a **manual** account | The app writes the post and notifies you; you post it and press "Mark as published". |

**Public media host** (Instagram, Threads, Google Business Profile fetch images from a URL): add **Cloudinary**
(free plan) in Integrations → Search and hosting, or run a Cloudflare Tunnel and add "My own public URL".

## 6. Create posting schedules (Settings → Posting schedules)

For each account: days, times (IST), how many posts, which post types (text, image, carousel, GIF, video,
animation, document) and the approval mode:

- **Generate only**: drafts you post yourself.
- **Approve first**: nothing goes out until someone approves it.
- **Auto unless rejected**: goes out at its time unless someone rejects it within the approval window.
- **Fully automatic**: goes out after passing the quality and duplicate checks. Posts that fail the checks are held
  for approval instead.

Posts are written ahead of time (default 24 hours) so reviewers have time. Use **Run now** to try a schedule immediately.

## 7. Get notified (Settings → Notifications)

Everyone chooses their own events and channels. Email needs an SMTP account in Integrations (a Gmail app password works).
Telegram needs a bot from @BotFather in Integrations; then each person presses **Link Telegram** and can approve,
reject or regenerate posts from their phone.

## 8. Images, video and voice (optional)

Branded images, carousels, GIFs, animations and PDF carousels work with no extra setup. For richer visuals add, in
Integrations → Images, video and voice: a stock provider (Pexels or Pixabay, free keys), an AI image provider
(Gemini image, OpenAI, Together's free FLUX model, Hugging Face, or Stable Diffusion WebUI / ComfyUI on this laptop),
and a voice (Microsoft Edge voices are free and need no key). Royalty-free music files placed in
`storage\music\` are mixed under video voice-overs.

## 9. SEO and analytics

- **SEO articles**: pick a keyword (or let it suggest from Search Console and competitor gaps); it writes the article
  with headings, FAQ, meta tags and schema, scores it, and exports MDX/HTML to `storage\seo\`. **Share as posts**
  turns it into social posts for review.
- **Analytics**: connect Google (Social accounts), then pick the GA4 property and Search Console site in
  Analytics → Settings. Social metrics are collected every 6 hours for published posts.

## Adding another organization (SaaS)

As platform admin: **All organizations → New organization**. Each organization has its own users, roles, brand,
accounts, integrations and data, isolated in the database. Switch between them from the sidebar.
