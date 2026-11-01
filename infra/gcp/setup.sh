#!/usr/bin/env bash
# One-time Google Cloud setup for the Digital Marketing Studio API (UAT + Production).
# Run in Google Cloud Shell (or anywhere with gcloud signed in as a project owner):
#   PROJECT_ID=hrp-hrms ./infra/gcp/setup.sh
# Creates (all idempotent, safe to re-run): Artifact Registry repo "dm", runtime service accounts dm-api-uat / dm-api-prod,
# deployer dm-deployer used by GitHub Actions through Workload Identity (no JSON keys), Secret Manager secrets dm-{env}-*
# (empty: values are added by infra/gcp/secrets.sh, typed in, never shown), Cloud Tasks queues dm-tick-uat / dm-tick-prod
# and an optional budget alert.
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID (e.g. hrp-hrms, the project HRMS already uses)}"
GITHUB_REPO="${GITHUB_REPO:-HRP-AI-LLP/digital-marketing}"
REGION="${REGION:-asia-southeast1}"          # Singapore: same city as Neon aws-ap-southeast-1
BILLING_ACCOUNT="${BILLING_ACCOUNT:-}"        # optional, for a budget alert

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com cloudscheduler.googleapis.com cloudtasks.googleapis.com \
  billingbudgets.googleapis.com

gcloud artifacts repositories create dm --repository-format=docker --location="$REGION" \
  --description="Digital Marketing Studio images" 2>/dev/null || true
gcloud artifacts repositories set-cleanup-policies dm --location="$REGION" --policy=- <<'JSON' >/dev/null || true
[{"name":"keep-recent","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":6}},
 {"name":"delete-old","action":{"type":"Delete"},"condition":{"olderThan":"1209600s"}}]
JSON

for ENV in uat prod; do
  gcloud iam service-accounts create "dm-api-$ENV" --display-name="Digital Marketing API ($ENV)" 2>/dev/null || true
done

# Exact-time wake-ups (scheduled posts, work queued in the app): one Cloud Tasks queue per environment.
for ENV in uat prod; do
  gcloud tasks queues create "dm-tick-$ENV" --location="$REGION" --max-attempts=2 --max-concurrent-dispatches=4 \
    --max-dispatches-per-second=1 2>/dev/null || true
  gcloud tasks queues add-iam-policy-binding "dm-tick-$ENV" --location="$REGION" --role=roles/cloudtasks.enqueuer \
    --member="serviceAccount:dm-api-${ENV}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null
done

gcloud iam service-accounts create dm-deployer --display-name="Digital Marketing GitHub deployer" 2>/dev/null || true
DEPLOYER="dm-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser roles/cloudscheduler.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$DEPLOYER" --role="$ROLE" \
    --condition=None >/dev/null
done

# GitHub sign-in: the pool "github" may already exist (HRMS); this repo gets its own provider "github-dm".
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub" 2>/dev/null || true
gcloud iam workload-identity-pools providers create-oidc github-dm --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${GITHUB_REPO}'" 2>/dev/null || true
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/${GITHUB_REPO}" >/dev/null

# Secrets. Runtime secrets are read by the API; the deployer reads the four it needs at deploy time.
# migrator-url: the nightly backup (pg_dump needs the owner, since row-level security applies to dm_app)
RUNTIME=(database-url migrator-url secret-key encryption-key origin-verify-secret cron-secret r2-access-key-id r2-secret-access-key admin-password)
DEPLOY=(migrator-url database-url origin-verify-secret cron-secret)
for ENV in uat prod; do
  for S in "${RUNTIME[@]}"; do
    gcloud secrets create "dm-$ENV-$S" --replication-policy=user-managed --locations="$REGION" 2>/dev/null || true
  done
  for S in "${RUNTIME[@]}"; do
    gcloud secrets add-iam-policy-binding "dm-$ENV-$S" --role=roles/secretmanager.secretAccessor \
      --member="serviceAccount:dm-api-${ENV}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null
  done
  for S in "${DEPLOY[@]}"; do
    gcloud secrets add-iam-policy-binding "dm-$ENV-$S" --role=roles/secretmanager.secretAccessor \
      --member="serviceAccount:$DEPLOYER" >/dev/null
  done
done

if [[ -n "$BILLING_ACCOUNT" ]]; then
  gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" --display-name="Digital Marketing monthly" \
    --budget-amount=15USD --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0 || true
fi

cat <<MSG

Google Cloud is ready. In GitHub (HRP-AI-LLP/digital-marketing → Settings → Secrets and variables → Actions):
  Variables:  GCP_PROJECT_ID=$PROJECT_ID
              GCP_REGION=$REGION
              GCP_WIF_PROVIDER=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-dm
              GCP_DEPLOYER_SA=$DEPLOYER
              R2_ENDPOINT=https://<cloudflare account id>.r2.cloudflarestorage.com
  Secrets:    CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID
  Environments: uat, production (production: required reviewer = you)
Next: ./infra/gcp/secrets.sh uat   and   ./infra/gcp/secrets.sh prod
MSG
