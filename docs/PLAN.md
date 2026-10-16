# HRP Digital Marketing Studio — Solution Plan (v0.3)

**Owner:** Prashant · **Company:** HRP AI LLP · **Status:** Planning (no code yet) · **Date:** 2026-10-15
**Location:** `E:\HRP Projects\HRP\Digital Marketing\`

---

## 1. Goal

A locally hosted, 3-tier web app (React → FastAPI → PostgreSQL) that manages HRP AI LLP's social media accounts and SEO. It generates **unique** posts (text, images, GIFs, short videos, animations) on configurable schedules (for example, at least 2 posts a day on the LinkedIn company page) and publishes them **fully automatically** or **after approval**.

Phase 1 is single-tenant (HRP AI LLP only). It is designed so that it can become a **multi-tenant SaaS** later without a rewrite: every table carries `organization_id` from day one.

## 2. Decisions so far

| # | Decision |
|---|---|
| D1 | Start with HRP AI LLP only; scale to multi-tenant SaaS after real usage |
| D2 | Support both **fully automatic** and **approve-first** publishing, configurable per platform/schedule |
| D3 | Pluggable **multi-provider LLM layer**: Ollama (local), Gemini, OpenAI, Anthropic, xAI Grok, Hugging Face, Groq, OpenRouter, and others (free and paid). Configured in the Admin → Integrations panel |
| D4 | **X/Twitter on hold** (free tier closed Feb 2026; pay-per-use only). The adapter slot stays in the design |
| D5 | Media: images, GIFs, short videos, animations |
| D6 | Database: **PostgreSQL** (+ `pgvector` for uniqueness checks and brand RAG) |
| D7 | Runs locally on Prashant's laptop. Free-tier platform APIs only |
| D8 | Laptop: **24 GB RAM, 8 GB GPU**. Local models sized to fit (see §5.9) |
| D9 | Containers: **Podman** (`podman compose`) for Postgres + Redis. API, worker and Ollama run natively on Windows for easy GPU / Playwright access |
| D10 | LinkedIn: Prashant's personal account **is an admin** of the HRP AI LLP company page |
| D11 | SEO articles are **stored in Digital Marketing only** (`storage/seo/`, exportable as MDX/HTML). Website integration comes later |
| D12 | Notifications: **email, Telegram, WhatsApp, in-app** |
| D13 | Posting times (IST), posts per day and post types per platform are **all configurable in Settings** (nothing hard-coded) |
| D14 | Content must be grounded in a full **Company Intelligence** analysis: website, digital presence, competitors, analytics, products, offerings, studios (AI Studio etc.), use cases, clients (§5.0) |
| D15 | Single admin login at first. Admin can **create users, roles and permissions** (RBAC) |
| D16 | Project files live **only** under `E:\HRP Projects\HRP\Digital Marketing\` |

## 3. Platform coverage and onboarding

| Platform | API / access | Lead time | Media supported via API |
|---|---|---|---|
| Facebook Page | Meta Graph API (`pages_manage_posts`); development mode is fine for own pages | Same day | Text, link, image, multi-image, video, Reels |
| Instagram (Business) | Instagram Content Publishing API; must be linked to the FB Page | Same day | Image, carousel, Reels (video). **No GIF**, so GIFs are converted to MP4 |
| LinkedIn – personal | "Share on LinkedIn" (`w_member_social`), self-serve | Same day | Text, image, video, article link |
| LinkedIn – Company Page | **Community Management API** (reviewed by LinkedIn; needs a verified legal entity) | **Apply day 1**, wait for review | Text, image, multi-image, video, document |
| Pinterest | API v5. Trial access gives owner-only visibility; **Standard access** (needs a screencast) makes pins public | **Apply day 1**, ~1–2 weeks | Image pins, video pins |
| Google Business Profile | Business Profile API (access request) | Apply early | Posts (text + image + CTA) |
| Threads / Bluesky / Mastodon | Open free APIs (optional extras) | Same day | Text, image, video (varies) |
| YouTube Shorts (optional) | YouTube Data API v3 upload (free quota) | Same day | Video |
| X / Twitter | **On hold** (D4) | — | — |

**Fallback for any platform not yet approved:** the post is generated and queued as "Manual publish". The app shows a one-click *Copy text + download media + open platform* action, so the schedule still holds.

**No browser-automation posting:** posting through a scripted browser session breaks the platforms' terms and risks bans on the company page.

## 4. Architecture

```
┌──────────────────────────── Laptop (Windows) ─────────────────────────────┐
│                                                                            │
│  React + Vite (Admin UI)  ──REST/WebSocket──►  FastAPI (API tier)          │
│   • Dashboard / calendar                        • Auth (local admin)       │
│   • Approval queue                              • Content & schedule APIs  │
│   • Integrations / settings                     • OAuth callbacks          │
│   • Brand kit, analytics, SEO                   • Provider registry        │
│                                                       │                    │
│                         ┌─────────────────────────────┼──────────────┐     │
│                         ▼                             ▼              ▼     │
│                  Scheduler (APScheduler,      Worker queue      PostgreSQL │
│                  Postgres job store)          (arq/Celery →     + pgvector │
│                         │                      Redis, or         (data,    │
│                         ▼                      Postgres queue)    jobs,    │
│        ┌──── Generation pipeline ────┐                            vectors) │
│        │ Plan → Write → Media → QA → │──► Publish adapters ──► Platforms   │
│        │ Uniqueness → Review/Auto    │    (FB, IG, LI, Pinterest, GBP …)   │
│        └─────────────────────────────┘                                     │
│   LLM gateway (LiteLLM) ──► Ollama local / Gemini / OpenAI / Anthropic /   │
│                              Grok / HF / Groq / OpenRouter …               │
│   Media engine ──► ComfyUI/diffusers (local), image APIs, FFmpeg,          │
│                     Playwright (HTML→PNG/MP4), TTS                         │
│   Public media bridge ──► Cloudflare Tunnel or Cloudinary free tier        │
│                            (IG/Pinterest fetch media from a public URL)    │
└────────────────────────────────────────────────────────────────────────────┘
```

### Tech stack

- **Frontend:** React 18 + Vite + TypeScript, TanStack Query, Tailwind, shadcn/ui, FullCalendar (content calendar)
- **API:** FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, `httpx`
- **Jobs:** APScheduler (cron-style schedules, Postgres job store) plus a worker queue (**arq + Redis**, or a Postgres-backed queue to avoid Redis on Windows; decision pending, see Q list)
- **DB:** PostgreSQL 16 + `pgvector`
- **LLM gateway:** **LiteLLM**, one interface over 100+ providers with fallbacks and cost tracking
- **Media:** Pillow, Playwright (render branded HTML templates to PNG/MP4), FFmpeg + MoviePy, optional ComfyUI/diffusers, edge-tts / Piper for voice-over
- **Packaging:** **Podman** (`podman compose`) runs PostgreSQL 16 + pgvector and Redis. FastAPI, the worker, the Vite frontend and Ollama run natively on Windows (GPU, Playwright and FFmpeg are simpler there). One `start.ps1` brings everything up; a Windows Task Scheduler entry starts it at login
- **Crawling / intel:** httpx + selectolax/trafilatura (HTML → clean text), Playwright for JS-rendered pages, `unstructured`/`pypdf`/`python-docx`/`python-pptx` for uploads
- **Notifications:** aiosmtplib (email), python-telegram-bot (long polling), WhatsApp Cloud API via httpx

## 5. Functional modules

### 5.0 Company Intelligence (the foundation for everything else)

A research module that builds and keeps current a structured **Company Profile** plus a searchable knowledge base. All content generation draws on it.

| Source | What is collected | How (free) |
|---|---|---|
| Company website (hrpai.in) | Every page: products, services, offerings, studios (AI Studio etc.), use cases, industries, case studies, clients/testimonials, team, blog, pricing, CTAs | Crawler (sitemap.xml + link-following, respects robots.txt) → Playwright for JS pages → clean text → LLM extraction into structured entities |
| Own digital presence | Profiles on LinkedIn, FB, IG, Pinterest, GBP: bio, follower counts, post history, top posts, gaps/inconsistencies | Platform APIs for connected accounts |
| Analytics | Traffic, top pages, sources, conversions; search queries, CTR, rankings; Core Web Vitals | **GA4 Data API**, **Search Console API**, **PageSpeed Insights API** (all free) |
| Competitors | Admin enters competitor list (app can also suggest some). Collects: offerings, positioning, pricing signals, blog topics, keywords in titles, posting cadence where public | Crawl of competitor **public websites**, sitemaps and RSS. Read-only, low rate, respecting robots.txt. (Platform APIs don't expose competitors' private data) |
| Uploaded docs | Brochures, decks, proposals, product docs, client case studies | Upload → parse (PDF/DOCX/PPTX) → chunk → embed |

**Outputs (in the UI):**
- **Company Profile:** products, studios, offerings, use cases, ICPs/industries, differentiators, clients. The admin reviews and confirms it (the "source of truth")
- **Digital Presence Audit:** score per channel, profile-completeness gaps, posting consistency, recommendations
- **Competitor Matrix:** feature/offering comparison, content themes they cover and we don't (gap topics), keyword gaps
- **Opportunity Backlog:** auto-generated topic ideas ranked by relevance × search demand × gap. This feeds the content planner (§5.3)
- **Scheduled refresh:** weekly re-crawl with a change log ("competitor X launched Y"), which can trigger reactive posts

**Client mentions:** each client entity has a flag, *"approved for public mention"*. Posts never name a client unless that flag is on.

### 5.1 Brand Kit and Knowledge (RAG)

- Brand profile: voice and tone, do/don't list, hashtags, CTAs, logo, colours, fonts, legal disclaimers
- Knowledge sources: hrpai.in pages, product docs (HRMS, CRM, AI Studio …), case studies, uploaded PDFs. These are chunked and embedded into pgvector
- Every generated post is grounded in retrieved facts, so posts stay factual and on-brand rather than generic AI filler

### 5.2 Content Strategy

- **Content pillars** with weights (for example Product 30%, Thought leadership 25%, HR/AI tips 25%, Company culture 10%, Promotions 10%)
- **Campaigns** (launches, festivals, events) with date ranges
- **Schedules:** per platform and account, a cadence (for example LinkedIn company 2/day at 10:00 and 17:30 IST), the content mix, and the **mode**:
  - `manual` – generate only
  - `approve_first` – needs approval before it publishes
  - `auto_with_window` – publishes automatically unless rejected within N hours
  - `full_auto` – publishes immediately after QA passes
- Indian holiday/festival calendar and "blackout dates"

### 5.3 Generation pipeline (per scheduled slot)

1. **Plan:** pick pillar, topic and format, avoiding recently used topics
2. **Write:** LLM drafts a master message, then **platform variants** (LinkedIn long-form, IG caption + hashtags, Pinterest title/description/link, FB, GBP)
3. **Media:** build the image, carousel, GIF, video or animation per the platform format spec
4. **QA:** brand-rule check, length and hashtag limits, banned words, link check, optional LLM-as-judge score
5. **Uniqueness:** embedding similarity against all past posts (cosine threshold, for example < 0.85) plus exact-hash check on media. Regenerate on fail
6. **Review / auto:** route according to the schedule mode
7. **Publish** through the adapter. Retry with backoff. Log platform post ID and URL
8. **Measure:** pull engagement metrics on a schedule

Generation runs **ahead of time** (for example T-24h) so approve-first has time for review.

### 5.4 Media studio

| Type | Default (free / local) | Optional providers |
|---|---|---|
| Image | **Branded HTML templates → PNG via Playwright** (crisp text, logo, colours) + AI or stock background | ComfyUI / SDXL / FLUX (local GPU), HF Inference, Gemini image, OpenAI gpt-image, Stability |
| Carousel | Multi-slide HTML templates → PNG set / PDF (LinkedIn document posts) | — |
| GIF | Animated HTML/CSS template → frames → FFmpeg GIF (MP4 for IG/FB) | — |
| Short video (≤60 s) | MoviePy/FFmpeg: stock clips/images (Pexels & Pixabay free APIs) + Ken Burns + captions + TTS (edge-tts / Piper) + royalty-free music | Text-to-video APIs (Veo, Runway, Kling, paid); local LTX-Video if GPU allows |
| Animation | HTML/CSS/Lottie animations recorded with Playwright → MP4 | Remotion (check licence terms before use) |

AI image models garble text, so text always goes on via templates, never inside the AI image.

Format presets: IG 4:5 / 1:1 / Reels 9:16; Pinterest 2:3; LinkedIn 1.91:1 / 1:1 / 4:5; FB 1:1 / 9:16; GBP 4:3.

### 5.5 Integrations (Admin panel)

- **LLM providers:** add provider, API key or base URL (for example Ollama `http://localhost:11434`), models, enable/disable, cost per 1K tokens, test-connection button
- **Task routing:** pick provider/model per task (planning, writing, QA judge, embeddings, image prompt), with a **fallback chain** (for example Gemini free → Groq → Ollama local)
- **Media providers:** image, video, TTS, stock-media keys
- **Social accounts:** OAuth "Connect" buttons, token status and expiry, auto-refresh
- **Budget guard:** monthly spend cap per paid provider. The pipeline falls back to free/local providers when the cap is hit

### 5.6 Approval workflow and notifications

- Queue with preview per platform, plus edit, regenerate (text or media only), approve, reject (with reason, fed back into prompts) and reschedule
- **Approve from your phone:** Telegram and WhatsApp messages carry the preview with **Approve / Reject / Regenerate** buttons

| Channel | How | Cost / notes |
|---|---|---|
| In-app | WebSocket bell + notification centre | Free |
| Email | SMTP (for example Gmail with an app password) | Free |
| Telegram | Bot API, **long polling**, so no public URL is needed | Free |
| WhatsApp | **WhatsApp Cloud API** (Meta). Needs a business phone number + approved message templates; webhook through Cloudflare Tunnel for button replies | Free-form and utility messages are **free inside the 24-hour window** after you message the bot. Outside it, utility templates are billed per message (small). App can send a daily "open window" prompt |

- Per-user preferences: which events go to which channel (awaiting approval, published, failed, token expiring, competitor change, daily digest)
- Quiet hours (IST)

### 5.7 Analytics

- Per post: impressions, reactions, comments, shares, clicks (where the API allows)
- Per pillar and format: what performs best. Later this feeds back into pillar weights (self-tuning)

### 5.8 SEO module

- **Blog/article generator** for hrpai.in: keyword-targeted long-form articles with meta title/description, schema.org JSON-LD, internal links and OG image (from the media studio)
- Later: publish to the main website (method to decide when integrating)
- **Google Search Console API** (free): queries, impressions, CTR, pages to improve, so the app can choose topics from real search data
- **PageSpeed Insights API** (free): periodic Core Web Vitals report
- Each blog post auto-spawns social posts linking back to it (content repurposing)
- **For now (D11):** articles live in `storage/seo/<slug>/` (`article.mdx`, `meta.json`, images). Export as MDX/HTML. Website integration comes later

### 5.9 Model sizing for this laptop (24 GB RAM, 8 GB VRAM)

| Task | Local default (fits 8 GB VRAM) | When to use cloud |
|---|---|---|
| Writing / planning | 7–9B instruct models, 4-bit (for example Qwen / Llama / Gemma class) via Ollama | Long-form SEO articles, competitor analysis → Gemini/OpenAI/Anthropic via routing |
| Embeddings | Small local embedding model (for example nomic-embed / bge class) via Ollama | — |
| Images | SDXL / FLUX-schnell (quantised) in ComfyUI. About 20–60 s/image; one model loaded at a time | Batch or high-quality images |
| Video | Template + stock + TTS (CPU/FFmpeg). Local text-to-video is borderline at 8 GB, so it's optional only | Optional paid video APIs |
| TTS | edge-tts (free) / Piper (local) | — |

The GPU is shared, so the worker runs **one GPU job at a time** (LLM or image) via a GPU lock.

### 5.10 Settings and administration

- **Schedules:** per platform/account, posts per day, time slots (IST), allowed post types (text, image, carousel, GIF, video, animation, link, document) with mix ratios, mode (§5.2), catch-up policy
- **Users and RBAC:** one seeded admin. The admin can create users, define **custom roles**, and assign **permissions** (for example `post.approve`, `post.publish`, `schedule.edit`, `integration.manage`, `intel.view`, `user.manage`). Role assignment can later be scoped per organization (SaaS-ready)
- Audit log of every action (who approved, edited or published)

## 6. Data model (multi-tenant ready)

Every business table has `organization_id` (FK). Phase 1 has exactly one organization row.

- `organizations`, `users`, `roles`, `permissions`, `role_permissions`, `user_roles (organization-scoped)`
- `intel_sources (website|competitor|upload|analytics)`, `crawl_runs`, `crawled_pages (url, hash, text)`, `company_entities (type: product|studio|offering|use_case|client|industry|differentiator, data, confirmed, public_mention_ok)`, `competitors`, `competitor_snapshots`, `presence_audits`, `opportunities (topic, score, status)`
- `notification_channels`, `notification_prefs`, `notifications`
- `brand_profiles`, `brand_assets`, `knowledge_sources`, `knowledge_chunks (embedding vector)`
- `provider_configs (kind: llm|image|video|tts|stock, encrypted secrets)`, `task_routes`
- `social_accounts (platform, external_id, display_name)`, `oauth_tokens (encrypted, expires_at)`
- `content_pillars`, `campaigns`, `schedules (account_id, cron, mode, mix, timezone)`
- `posts (master)` → `post_variants (platform, text, hashtags, link)` → `media_assets (type, path, public_url, hash, spec)`
- `post_embeddings (vector)` for uniqueness
- `approvals (decision, user, reason)`, `publish_jobs`, `publish_attempts (status, error, platform_post_id, url)`
- `metrics_snapshots`, `seo_articles`, `seo_keywords`, `gsc_snapshots`
- `llm_usage (provider, model, tokens, cost)`, `audit_log`

**Post state machine:** `planned → drafted → media_ready → qa_passed → in_review → approved → scheduled → publishing → published | failed | rejected | manual_pending`

## 7. Running locally: constraints and answers

| Constraint | Design answer |
|---|---|
| Posts only go out while the laptop is on | App starts with Windows (Task Scheduler). On startup, a **catch-up policy** per schedule: publish missed slots if < N hours late, else reschedule to the next free slot. A dashboard banner shows missed slots |
| IG/Pinterest need public media URLs | Upload to **Cloudinary free tier** (or Cloudflare Tunnel exposing `/media` only) just before publishing |
| OAuth redirects | `http://localhost:<port>/oauth/<platform>/callback`. Meta, LinkedIn and Pinterest allow localhost for dev apps (verify per app) |
| Token expiry (LinkedIn ~60 days, Meta long-lived) | Nightly refresh job plus an alert 7 days before expiry |
| Secrets | Fernet-encrypted in DB. Master key in `.env`, never committed |
| Rate limits | Per-platform rate limiter + exponential backoff + idempotency keys (no double posts) |

## 8. Folder structure

```
Digital Marketing/
├── docs/                 # this plan, ADRs, platform onboarding notes
├── backend/              # FastAPI
│   ├── app/
│   │   ├── api/          # routers
│   │   ├── core/         # config, security, tenancy
│   │   ├── models/       # SQLAlchemy
│   │   ├── services/
│   │   │   ├── llm/      # LiteLLM gateway, routing, budgets
│   │   │   ├── media/    # templates, image, gif, video, tts
│   │   │   ├── content/  # planner, writer, qa, uniqueness
│   │   │   ├── publishers/  # facebook.py, instagram.py, linkedin.py, pinterest.py, gbp.py …
│   │   │   ├── seo/      # blog generator, gsc, pagespeed
│   │   │   └── analytics/
│   │   ├── scheduler/
│   │   └── workers/
│   ├── alembic/
│   └── tests/
├── frontend/             # React + Vite
├── media_templates/      # HTML/CSS brand templates (image, carousel, animation)
├── storage/              # generated media (git-ignored)
├── infra/                # docker-compose.yml, start.ps1
└── .env.example
```

## 9. Delivery phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 – Foundations** (wk 1) | Repo, Podman compose (Postgres+pgvector, Redis), FastAPI skeleton, React shell, admin login + RBAC (users/roles/permissions), org/tenancy model, Alembic, `start.ps1`. **Submit LinkedIn CMA, Pinterest Standard, GBP access requests** | App boots with `start.ps1`; admin can create a user with a custom role |
| **P1 – Integrations & LLM** (wk 2) | Provider registry, LiteLLM gateway (Ollama, Gemini, OpenAI, Anthropic, Grok, HF, Groq, OpenRouter), task routing, fallback, budgets, test-connection, GPU lock | Same prompt runs through any configured provider |
| **P2 – Company Intelligence** (wk 3–4) | Website crawler, upload ingestion, entity extraction → Company Profile (review/confirm UI), competitor crawl + matrix, digital-presence audit, GA4/Search Console connectors, opportunity backlog | Confirmed Company Profile + competitor matrix + 50 ranked topic ideas |
| **P3 – Content engine** (wk 5) | Brand kit, pillars, configurable schedules/types/times, planner, writer, platform variants, QA, uniqueness, approval queue, calendar | Configured LinkedIn schedule produces drafts in the queue automatically |
| **P4 – Notifications** (wk 5) | In-app, email, Telegram (approve buttons), WhatsApp Cloud API (approve buttons via tunnel), per-user preferences | Approve a post from Telegram and WhatsApp |
| **P5 – Publishing** (wk 6) | FB Page, Instagram, LinkedIn personal + company (when approved), Pinterest, GBP, manual-publish fallback, retries, token refresh, catch-up | Real posts published in all 4 modes |
| **P6 – Media studio** (wk 7–8) | Branded image templates, carousels, AI/stock images, GIF, short video with TTS, animations, public media bridge | IG Reel + Pinterest video pin published end-to-end |
| **P7 – SEO & analytics** (wk 9) | SEO article generator (stored locally), metrics pulls, performance dashboard, weekly report | Weekly SEO + engagement report |
| **P8 – Hardening** (wk 10) | Tests, logging, DB/media backups, docs | Runs unattended for 2 weeks |
| **P9 – Mobile apps** | React Native + Expo app (Android + iOS): approvals queue, calendar, notifications (FCM push), quick post, analytics | Approve a post from the phone app |
| **Later – SaaS hosting** | Self-service sign-up, billing, cloud hosting, per-tenant OAuth apps, Meta/LinkedIn app reviews for third-party use | — |

## 10. Risks

- **Platform approvals** (LinkedIn company page, Pinterest, GBP) are outside our control. Mitigated by the manual-publish fallback and applying on day 1
- **Laptop GPU/RAM** limits local image/video models. Mitigated by templates + stock + cloud-provider fallback
- **Content quality / repetition at 2+ posts/day.** Mitigated by RAG grounding, pillar rotation, uniqueness check and approval mode
- **Policy changes and API pricing** (as happened with X). The adapter pattern isolates each platform
- **Engagement-bait / spam signals** from over-posting. Cadence is configurable per platform, with sensible defaults (for example IG 1/day, Pinterest 3–5/day, LinkedIn 1–2/day)

## 11. Answered (2026-10-15)

Laptop specs → D8 · Podman → D9 · LinkedIn admin → D10 · SEO storage → D11 · Notifications → D12 · Configurable schedules → D13 · Company Intelligence → D14 · RBAC → D15

| # | Decision (2026-10-15, round 3) |
|---|---|
| D17 | **Competitors are discovered automatically.** The LLM derives search queries from the confirmed Company Profile (offerings × industries × geography). Queries run through a **self-hosted SearXNG** container (free meta-search, Podman) and optional free search APIs. Candidate sites are crawled and scored for overlap with our offerings. The top N go to the admin for one-click confirm/reject, and the list is re-run monthly |
| D18 | GA4 and Search Console are set up for hrpai.in. Both connectors are in scope for P2 |
| D19 | **WhatsApp deferred** (no business number yet). Channel slot kept; in-app + email + Telegram first |
| D20 | **Every client named on the company website may be mentioned publicly.** Website-sourced clients default to `public_mention_ok = true`; clients from other sources default to false |
| D21 | `Digital Marketing` is its **own git repository** |
| D22 | **Multi-tenant from day one** (supersedes D1's "single-tenant first"). Organizations are tenants; HRP AI LLP is tenant #1. Tenant isolation is enforced by **PostgreSQL row-level security** (app connects as non-superuser `dm_app`; every transaction sets `app.org_id`). Platform admins manage tenants. Users are global identities with per-organization memberships and roles, and can switch organizations |
| D24 | **Visual identity follows hrpai.in**: Inter font, primary blue scale (#075492 / #0f8dde), cyan secondary (#06a6c7 / #22d3ee), slate surfaces (#f8fafc / #0f172a), blue-to-cyan gradient buttons, dark navy mesh panels, HRP AI logo. The same tokens will be reused by the mobile apps |
| D23 | **Android and iOS apps** will follow. Implications now: versioned API (`/api/v1`), bearer access tokens + rotating per-device refresh tokens, OpenAPI schema for client generation, push notifications via **Firebase Cloud Messaging** (free; covers Android and iOS) added to the notification channels. Recommended stack: **React Native + Expo** (shares TypeScript/React with the web app). Phones reach the laptop-hosted API on the same Wi-Fi or through a Cloudflare Tunnel until the SaaS is cloud-hosted |

## 12. Remaining questions

None blocking.

**Status (2026-10-15):** P0–P7 built and tested (124 automated tests, live end-to-end run with a stand-in model): integrations and AI routing, company intelligence, content engine with approvals, notifications, publishing adapters, media studio, SEO and analytics. Remaining: real-account verification per platform once developer apps are approved, P8 hardening (backups), P9 mobile apps.

Implementation notes: the LLM layer uses its own OpenAI-compatible + Anthropic clients instead of LiteLLM (fewer dependencies on Windows, same provider coverage). Jobs use a PostgreSQL queue (SKIP LOCKED) instead of Redis.
