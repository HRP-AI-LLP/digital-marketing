# HRP Digital Marketing Studio

A multi-tenant web app to plan, generate, approve and publish social media and SEO content.
HRP AI LLP is the first organization (tenant). It runs locally on this laptop.

The full plan is in [docs/PLAN.md](docs/PLAN.md).

## Run it

You need **Python 3.11+**, **Node.js 20+** and **Podman** (Podman Desktop on Windows).

```powershell
cd "E:\HRP Projects\HRP\Digital Marketing"
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The first run takes a few minutes. It:

1. creates `.env` with random secrets and a temporary admin password (printed at the end),
2. starts **PostgreSQL 16 + pgvector** (port 5433) and **Redis** (port 6380) in Podman,
3. creates `backend\.venv`, installs Python packages and migrates the database,
4. installs the web app packages,
5. starts the API on http://127.0.0.1:8600 and the web app on http://localhost:5180
   (change `API_PORT` / `WEB_PORT` in `.env` if those ports are taken).

Sign in as `admin@hrpai.in` with the printed password; you'll be asked to choose your own.
Stop everything with `.\stop.ps1` (data stays in Podman volumes).

## What it does

| Area | Where | Highlights |
|---|---|---|
| Company intelligence | Company intelligence | Reads the website and your documents, builds the company profile (products, studios, services, use cases, clients), finds competitors automatically, audits your digital presence, suggests topics |
| Content engine | Calendar, Approvals, All posts, Brand and topics, Posting schedules | Schedules per account (days, IST times, post-type mix, topic pillars, campaigns), AI writing grounded in your company facts, quality checks, duplicate detection, four approval modes, catch-up when the laptop was off |
| Media studio | Media studio | Branded images, carousels, PDF carousels, GIFs, animations and short videos with voice-over; AI images, stock media; library |
| Publishing | Social accounts | LinkedIn (profile, company page), Facebook page, Instagram, Threads, Pinterest, Google Business Profile, YouTube Shorts, Bluesky, Mastodon; manual accounts for X and anything else |
| SEO | SEO articles | Keyword ideas, full articles with FAQ, meta tags and schema, SEO score, MDX/HTML export to `storage/seo/`, repurposing into posts |
| Analytics | Analytics | Post engagement, best posting times, GA4, Search Console, PageSpeed |
| AI providers | Integrations | Ollama, LM Studio, Gemini, OpenAI, Anthropic, xAI Grok, Groq, OpenRouter, Hugging Face, Mistral, DeepSeek, Together, Cerebras, any OpenAI-compatible API; model lists refreshed from each provider every 6 hours; ordered fallbacks for text, embeddings, images, voice and video, with models resting automatically after rate/daily limits, billing or key errors; per-model usage (tokens, images, audio seconds, clips, cost) |
| Notifications | Notifications | In-app, email, Telegram with approve/reject buttons |
| Multi-tenancy | All organizations, Users, Roles | Organizations isolated by PostgreSQL row-level security; custom roles and permissions |

Start with **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**. Platform app setup: [docs/SETUP_SOCIAL.md](docs/SETUP_SOCIAL.md).
Code conventions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Three processes run: the API, the **worker** (`python -m app.worker`: planning, writing, rendering, publishing,
crawling, token refresh, metrics) and the web app. `start.ps1` starts all three; `stop.ps1` stops them.

## When the laptop is off, asleep or offline

Nothing scheduled is ever dropped or marked as missed.

- **Laptop off or app closed.** On the next start, every post whose time has passed goes out: oldest first, one at a
  time per account (20 minutes apart by default; Settings → Organization). You get one "Welcome back" summary
  listing what goes out when. To skip running `start.ps1` after a restart, run `.\install-autostart.ps1` once.
  The studio then starts by itself a minute after you sign in to Windows. `-Remove` turns it off.
- **Asleep or hibernating.** The worker sees the jump in the clock when the laptop wakes and catches up the same way.
  Every periodic task (planning, metrics, research…) runs straight away instead of waiting for its next turn.
  Optional: Settings → Organization → "Wake this laptop…" sets a Windows wake timer about 3 minutes before the next
  post. It works only when the laptop is plugged in, and it can't wake a laptop that is shut down. While a post is
  due, the laptop is kept awake.
- **No internet.** The worker checks the connection. While it's down, it pauses: nothing is claimed, no retries are
  used up and no post fails. A post interrupted by a dropped connection waits too. A banner shows the pause. When
  the connection returns, everything resumes on its own.
- **Approve-first posts** still waiting for approval after their time simply wait. You get one reminder, and once
  you approve one it goes out straight away. Festival greetings whose day has passed come back to review instead of
  posting late on their own.

Downtime is recorded in `downtime_periods`, and the worker heartbeat is in `system_state`. `GET /api/v1/system/status`
powers the banner.

## Backups and restoring

The worker backs up the database every night (default **02:30**, laptop time; if the laptop was off then, as soon
as the studio runs again). Platform admins manage it in **Platform → Backups**: see every backup, download one,
press **Back up now**, and change the settings.

- **Where:** `storage\backups\dm-YYYYMMDD-HHMMSS-daily.dump` (compressed PostgreSQL custom format). The dump is
  made with `podman exec hrp-dm-postgres pg_dump -U dm_owner -Fc digital_marketing`, or with a local `pg_dump`
  (PostgreSQL command-line tools) when the container isn't there. Each file is checked before it counts.
- **How many:** the newest backup of each of the last 14 days plus one per week for 8 weeks (both adjustable).
- **A second copy:** set *Also copy to* to a folder that OneDrive or Google Drive syncs (for example
  `C:\Users\you\OneDrive\HRP Backups`), so a backup survives a lost or broken laptop.
- **Media files:** optionally zip `storage\orgs` (generated and uploaded images and videos, without caches) next to
  each backup. It can be large, so it's off by default.
- **Failures** notify platform admins (in the app, and by email or Telegram per their notification settings).

**Restore** (stops the app, asks you to type RESTORE, saves a safety copy of the current database, restores inside
the container with `pg_restore --clean --if-exists` in one transaction, then starts the studio again):

```powershell
powershell -ExecutionPolicy Bypass -File .\restore.ps1                      # newest backup
powershell -ExecutionPolicy Bypass -File .\restore.ps1 -File storage\backups\dm-20261019-023000-daily.dump -Media
```

On a new laptop: copy the project folder, run `start.ps1` once (it creates the containers), copy the backup into
`storage\backups\` (and the same `.env`, which holds the key that decrypts stored passwords and API keys), then run
`restore.ps1`.

## Sign-in security

- **Two-step sign-in:** Account → Security → *Set up*: scan the QR code with an authenticator app (Google or
  Microsoft Authenticator, Authy, 1Password…), type the 6-digit code, and keep the 10 one-time recovery codes.
  After that, signing in asks for a code. Five wrong codes in a row lock the second step for 10 minutes.
- **Require it for everyone:** Settings → Organization → Sign-in security (Administrators only). Members set it up
  at their next sign-in. An Administrator can reset it for someone who lost their phone (Users → Edit).
- **Devices:** Account → Security lists every signed-in device; *Sign out other devices* ends them at once. Signing
  in from a new device sends an email (when an email sender is set up in Settings → Organization → Email).

## Layout

```
backend/    FastAPI + SQLAlchemy 2 (async) + Alembic
  app/core/       config, database + tenant context, security, crypto, storage, signed media URLs, auth deps
  app/models/     identity.py (tenancy, users, roles), marketing.py (all module tables)
  app/api/        one router per area (auth, users, roles, integrations, intel, content, social, media, seo, analytics, ...)
  app/modules/    worker-side job handlers and periodic tasks per area
  app/services/   shared logic: llm/ (providers, routing), knowledge, crawler, content, scheduler, publishing,
                  social/ (one adapter per platform), media (+ templates, rendering, video, tts, stock), seo, analytics
  alembic/        migrations (tables, row-level-security policies, search indexes)
  tests/          124 tests against a real PostgreSQL (APIs, tenancy, RBAC, pipelines with mocked external services)
frontend/   React 18 + Vite + TypeScript + Tailwind + TanStack Query
infra/      compose.yaml (reference; start.ps1 runs the containers directly)
docs/       PLAN.md
storage/    logs and, later, generated media and SEO articles (not in git)
```

## Tests

With the containers running (`start.ps1`):

```powershell
cd backend
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest
```

Tests create and migrate a separate `digital_marketing_test` database. No external service is called.

## Adding a tenant-owned table later

1. Model it with `TenantMixin` (adds `organization_id`).
2. In its migration call `enable_tenant_rls("<table>")` from `app.db_rls`.
3. Query it through the request's `ctx.session`; RLS scopes it automatically.
4. Add any new permissions to `app/core/permissions.py`; Administrator roles get them on next start.
