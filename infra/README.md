# Cloud: UAT and Production

| | UAT (dev & test) | Production |
|---|---|---|
| Address | https://uat-dm.hrpai.in | https://dm.hrpai.in |
| Deploys | every push to `main` | a tag `dm-vX.Y.Z`, after your approval in GitHub |
| Data | a copy of the laptop (HRP AI LLP, users, connected accounts, posts, history) | empty: only the platform admin |
| Posting | live (real accounts) | live, once organizations are added |

How it fits together (same pattern as HRMS):

- **Cloudflare Worker** `dm-frontend-uat` / `dm-frontend-prod` serves the app. It forwards `/api`, `/r`, `/l`, `/f` to
  the API and adds a shared secret, so the API refuses anything that didn't come through the Worker.
- **Google Cloud Run** `dm-api-uat` / `dm-api-prod` (Singapore) runs the API and the job runner. It costs nothing
  while idle. **Cloud Scheduler** calls it every 20 minutes for the routine work (inbox sync, metrics, research).
  **Cloud Tasks** wakes it on the minute for scheduled posts, and immediately when you queue something in the app.
- **Neon**, organization **HRP AI** (org-mute-haze-38808562), projects `dm-uat` (odd-recipe-92981685) and `dm-prod`
  (bitter-silence-25555054). Both run PostgreSQL 16 in Singapore with database `digital_marketing` and owner role
  `dm_owner`. Compute is 0.25 CU and sleeps after 5 idle minutes. On the free plan each project has 100 compute-hours
  and 0.5 GB a month; waking only for work keeps it well inside that. Today's laptop database is about 150 MB.
- **Cloudflare R2** `dm-uat-media` / `dm-prod-media` holds images, videos and exports.

Expected cost: about $0–3 a month in total. Cloud Run, Scheduler, Tasks and R2 (10 GB) mostly stay inside their free
tiers, and Neon stays on its free plan. The optional budget alert in step 4 emails you at $7.50, $13.50 and $15.

---

## One-time setup

Do the steps in order. Secrets are only ever typed into hidden prompts; none of them goes into chat, a file or GitHub.

### 1. Push the code to GitHub
On the laptop, in the Digital Marketing folder: commit and push to `HRP-AI-LLP/digital-marketing` (`main`).
The `deploy` workflow skips itself until step 4 is done; the `ci` workflow runs the tests.

### 2. Neon: connection strings (the projects already exist)
**dm-uat** and **dm-prod** were created on 1 Nov 2026 in the HRP AI organization. For each one, in console.neon.tech
open the project → **Connect**. Choose branch `main`, database `digital_marketing`, role `dm_owner`, and turn
**Connection pooling off**: the studio needs the direct host, without `-pooler`. Copy the connection string; you paste
it in steps 5 (dm-uat) and 6 (dm-prod). The scripts fix a pooled string or a missing `sslmode` for you.

### 3. Cloudflare
- **R2** → Create bucket **dm-uat-media** and **dm-prod-media** (location hint: Asia-Pacific).
- R2 → **Manage API tokens** → Create API token: permission **Object Read & Write**, specific bucket. Make one for
  `dm-uat-media` and one for `dm-prod-media`. Keep each token page open: you paste its Access Key ID and Secret Access
  Key in steps 5 and 6.
- Note your **Account ID** (R2 overview, right-hand side).
- My Profile → API Tokens → Create token → template **Edit Cloudflare Workers**. Account: yours. Zone: `hrpai.in`.
  This one is for GitHub (step 4).

### 4. Google Cloud and GitHub
In **Google Cloud Shell** (console.cloud.google.com, the `>_` button), project `hrp-hrms` or a new one:
```bash
git clone https://github.com/HRP-AI-LLP/digital-marketing.git && cd digital-marketing
PROJECT_ID=hrp-hrms ./infra/gcp/setup.sh          # add BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX for a budget alert
```
It ends by printing the GitHub values. In GitHub → the repo → Settings:
- **Secrets and variables → Actions → Variables**: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`,
  `GCP_DEPLOYER_SA` (as printed), and `R2_ENDPOINT` = `https://<account id>.r2.cloudflarestorage.com`.
- **Secrets**: `CLOUDFLARE_API_TOKEN` (the token from step 3) and `CLOUDFLARE_ACCOUNT_ID`.
- **Environments**: create `uat` and `production`. In `production`, tick **Required reviewers** and add yourself.

### 5. Move the laptop's data to UAT
Run two windows side by side.
- **Laptop** (PowerShell, in the Digital Marketing folder):
  `powershell -ExecutionPolicy Bypass -File .\infra\move-to-uat.ps1`
  It stops the laptop studio and switches its automatic work off. It copies the database to Neon dm-uat (it checks
  the copy by counting organizations, users, accounts, posts and media), creates the cloud login, and uploads the
  media to R2.
- When it reaches *Values for the cloud*: in **Cloud Shell** run `./infra/gcp/secrets.sh uat`. The laptop copies
  each value to the clipboard in the same order Cloud Shell asks for them: owner connection, app connection, R2 key
  ID, R2 secret, encryption key. Press Enter on the laptop, paste in Cloud Shell, and repeat.
  If gcloud is installed on the laptop, the script stores the values directly instead. Then run
  `./infra/gcp/secrets.sh uat` once anyway, for the generated values.

### 6. Production values
In Cloud Shell: `./infra/gcp/secrets.sh prod`. Paste the dm-prod owner connection string and the dm-prod-media R2
keys. Everything else is generated, including the first-sign-in password for the platform admin.

### 7. First deploy
GitHub → Actions → **deploy** → Run workflow → `uat` (or push any commit to `main`). About 10 minutes. It builds the
API image, updates the database, deploys Cloud Run, sets up the 20-minute scheduler, deploys the Worker on
uat-dm.hrpai.in, and checks the public address.

### 8. Check UAT
Open https://uat-dm.hrpai.in and sign in with your usual laptop sign-in (same users). Then:
- **Connections**: the accounts show as connected. Their tokens were copied with the database.
- Schedule a post for 5 minutes ahead on one account. It goes out on the minute.
- **Inbox**, **Grow followers**, **Activity** and **Insights** show the laptop's data.

### 9. Sign-in addresses for each platform (once per environment)
When you reconnect an account, each platform only accepts addresses registered in its developer app. Add these
next to the existing ones (keep the old ones until the laptop is retired). The Connections page shows the exact
address for each connector.

| Developer app | UAT | Production |
|---|---|---|
| Meta (Facebook, Instagram) | `https://uat-dm.hrpai.in/api/v1/social/oauth/meta/callback` | `https://dm.hrpai.in/api/v1/social/oauth/meta/callback` |
| Threads | `…/oauth/threads/callback` | same on dm.hrpai.in |
| LinkedIn (both apps) | `…/oauth/linkedin/callback` | same on dm.hrpai.in |
| Google (YouTube, Business Profile) | `…/oauth/google/callback` | same on dm.hrpai.in |
| Pinterest | `…/oauth/pinterest/callback` | same on dm.hrpai.in |
| X | `…/oauth/x/callback` | same on dm.hrpai.in |
| Canva | `…/oauth/canva/callback` | same on dm.hrpai.in |
| Reddit (after approval) | `…/oauth/reddit/callback` | same on dm.hrpai.in |

### 10. Production
When UAT looks right: GitHub → the repo → Releases → Draft a new release → tag **`dm-v1.0.0`** on `main` → Publish.
Approve the `production` deployment when GitHub asks. Then open https://dm.hrpai.in and sign in as
`admin@hrpai.in` (to use another address, set the GitHub variable `PLATFORM_ADMIN_EMAIL` before the first production deploy). Read the first-sign-in password once in Cloud Shell:
`gcloud secrets versions access latest --secret dm-prod-admin-password`. You'll be asked to choose your own
password. Then: Platform → All organizations → create the first organization.

### 11. Old public links (optional)
Short links, QR codes and "follow us" pages made on the laptop point at `studio.hrpai.in`. To keep them working once
the laptop is off, give that name to UAT:
1. Cloudflare Zero Trust → Tunnels → your tunnel: remove the public hostname `studio.hrpai.in`.
2. Workers & Pages → `dm-frontend-uat` → Settings → Domains & Routes → Add → Custom domain `studio.hrpai.in`.

---

## Day to day
- **UAT**: push to `main` and it deploys.
- **Production**: tag `dm-vX.Y.Z` and approve.
- **Database changes** are Alembic migrations. Each deploy applies them before the new code takes traffic, so keep
  them backward-compatible.
- **Logs**: Cloud Run → `dm-api-uat` → Logs. Jobs and errors also show in the app (Activity, the jobs indicator).
- **Pause all automatic work** in one environment: Cloud Scheduler → `dm-tick-uat` → Pause, and Cloud Tasks →
  `dm-tick-uat` → Pause queue.
- **Backups**: there is no nightly job in the cloud for now; Neon keeps a short restore window. To take a copy
  yourself, use Platform → Backups → **Back up now**. It stores a `pg_dump` in the environment's R2 bucket under
  `backups/`, and you can download it there. To switch the nightly backup on later, add `CLOUD_NIGHTLY_BACKUP=true`
  to the Cloud Run settings in `.github/workflows/deploy.yml`. Restore with `pg_restore --clean --no-owner -d "<owner
  connection string>" <file>` using a PostgreSQL 17 client.
- **Back to the laptop** (emergency only): first pause UAT as above, then set `AUTOMATION_ENABLED=true` in the
  laptop's `.env` and run `start.ps1`. The laptop's data is from the day of the move.
