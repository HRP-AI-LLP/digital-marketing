# Architecture and conventions

Read this before changing code. Paths are relative to `Digital Marketing/`.

## Processes

| Process | Command | Role |
|---|---|---|
| API | `uvicorn app.main:app` (backend/) | REST API at `/api/v1`, used by the web app and later the mobile apps |
| Worker | `python -m app.worker` (backend/) | Runs queued jobs and periodic tasks |
| Web | `npm run dev` (frontend/) | React app; Vite proxies `/api` to the API |
| PostgreSQL 16 + pgvector, Redis | Podman containers | Data; Redis is reserved for later use |

## Multi-tenancy (must follow)

- Every tenant-owned table has `organization_id` and a row-level-security policy (see `app/db_rls.py`).
- Request handlers get `ctx: Context` from `require("<permission>")` or `get_context`. **Always query through
  `ctx.session`**: RLS scopes it to `ctx.org_id`. When inserting, set `organization_id=ctx.org_id`.
- Background jobs get `ctx.session` already scoped to the job's organization.
- `platform_session()` bypasses RLS: use it only for genuinely cross-tenant work (login lookup, OAuth state, worker claims).
- Never add or change models/migrations casually; extras go in the existing JSON columns (`settings`, `meta`, `data`).

## Backend layout

```
app/core/        config, db (tenant sessions), security, crypto (Fernet for secrets), storage, signing (media URLs), deps (auth/permissions)
app/models/      identity.py (tenancy, users, roles), marketing.py (all module tables)
app/api/         one router per area, mounted in app/main.py under /api/v1
app/modules/     worker-side code per area: @handler job handlers, @periodic tasks, @tester connection tests
app/services/    shared logic used by API and worker
  jobs.py          enqueue(...), @handler, @periodic, Worker, run_inline (tests)
  llm/             catalog.py (all integrations), client.py, router.py (complete, complete_json, embed_texts)
  notify.py        notify_org / notify_users (in-app, email, Telegram with buttons)
  platforms.py     PLATFORMS: limits and supported post types per social platform
  audit.py         record(...) for the activity log
```

### Module contracts (other modules depend on these signatures)

| Module | Function | Purpose |
|---|---|---|
| `app/services/knowledge.py` | `async retrieve(session, query, k=6) -> list[dict(text,url,title)]` | RAG over crawled site + uploads (vector search when embeddings exist, else full-text) |
| | `async company_context(session, max_chars=3500) -> str` | Compact confirmed company facts for prompts (only clients with `public_mention_ok`) |
| | `async next_opportunity(session, pillar_id) -> Opportunity \| None` | Best unused topic idea for a pillar; marks it used |
| `app/services/media.py` | `async generate_for_post(session, post, brand) -> list[MediaAsset]` | Builds media for `post.post_type` in the platform's format |
| | `async ensure_public_url(session, asset) -> str` | Uploads to the configured media host when a platform needs a URL |
| | `asset_out(asset) -> dict` | JSON for the UI incl. signed `url` (`app/core/signing.media_url`) |
| | `async save_upload(session, org_id, data, filename) -> MediaAsset` | Store an uploaded file |
| `app/services/publishing.py` | `async publish(session, post, account) -> (external_id, external_url)` | Raises `PublishError(msg, retryable, reconnect)` |
| | `async fetch_metrics(session, post, account) -> dict \| None` | Engagement numbers for a published post |
| | `async refresh_account(session, account) -> None` | Refresh tokens / validate an account |
| `app/services/content_actions.py` | `approve_by_id / reject_by_id / regenerate_by_id (session, post_id, user, ...) -> str` | Used by the UI and Telegram buttons |

### Jobs

`await enqueue(session, "kind", org_id, payload, run_after=..., dedupe_key=...)` inside the request transaction, then commit.
Handlers: `@handler("kind") async def h(ctx: JobContext) -> dict | None`. Raise `PermanentError` to fail without retries.
The UI polls `/api/v1/jobs/{id}` (frontend hook `useJob`).

Job kinds in use: `intel.crawl`, `intel.extract`, `intel.competitors`, `intel.competitor_crawl`, `intel.audit`, `intel.ideas`,
`intel.run_all`, `intel.analytics`, `post.generate`, `post.publish`, `post.metrics`, `media.generate`, `seo.write`, `seo.export`,
`seo.keywords`, `account.refresh`.

### Secrets

Provider credentials live in `provider_configs.secrets_enc` (Fernet via `app/core/crypto.py`), OAuth tokens in
`social_accounts.tokens_enc`. Never return raw secrets from the API; use `mask()`.

### Media files

Saved under `storage/orgs/<org_id>/<area>/<yyyy-mm>/` via `app/core/storage.py`; DB stores relative paths.
Browsers load them through `GET /api/v1/media/file/{id}?o=&e=&s=` (signed, no auth header needed).

## Frontend conventions

- Pages in `src/pages/`, routes in `src/App.tsx`, navigation in `src/components/Layout.tsx`.
- Data: `api<T>(path, {method, json})` from `src/api/client.ts` + TanStack Query. Long tasks: `useJob(id)` from `src/api/hooks.ts`.
- UI kit in `src/components/ui.tsx`: Button, Field, Input, Select, Textarea, Toggle, Tabs, Card, Modal, Alert, Badge,
  StatusBadge, EmptyState, Spinner, Stat, PageHeader, SecretReveal, formatDate.
- Theme follows hrpai.in: Inter, `primary-*` blue scale, `secondary-*` cyan, slate neutrals, blue→cyan gradient primary
  buttons (`Button` default), rounded-lg/xl, `ai-mesh-dark` for dark panels. Use tokens, not new colors.
- Copy: plain sentence case, active verbs, buttons say exactly what happens, errors say how to fix. No ALL-CAPS labels.
- Check permissions with `useAuth().can("perm")` to hide actions; the API enforces them anyway.

## Tests

`backend/tests/` run against a real PostgreSQL (`pytest`). Set `TEST_DB_NAME` to use a separate database.
External APIs are never called in tests: patch `httpx` calls or the module's client functions.
