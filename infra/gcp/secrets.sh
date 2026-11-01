#!/usr/bin/env bash
# Fill the Secret Manager values for one environment:  ./infra/gcp/secrets.sh uat|prod
# Generated values (signing keys, cron + origin secrets, admin password) are created here and never printed.
# Values you paste (Neon connection strings, R2 keys) are read silently. Existing values are kept unless you choose to
# replace them. For UAT, run the laptop's infra\move-to-uat.ps1 at the same time: it copies each value it knows
# (connection strings, R2 keys, the laptop's encryption key) to the clipboard in the order this script asks for them.
set -euo pipefail
ENV="${1:?usage: secrets.sh uat|prod}"; [[ "$ENV" == uat || "$ENV" == prod ]] || { echo "uat or prod"; exit 1; }
P="dm-$ENV"

has() { gcloud secrets versions list "$P-$1" --filter=state=enabled --limit=1 --format='value(name)' 2>/dev/null | grep -q .; }
put() { printf '%s' "$2" | gcloud secrets versions add "$P-$1" --data-file=- >/dev/null; echo "  set $P-$1"; }
ask() {  # ask <secret> <prompt>
  local v; if has "$1"; then read -r -p "  $P-$1 already set. Replace it? [y/N] " a; [[ "$a" =~ ^[yY] ]] || return 0; fi
  read -r -s -p "  $2: " v; echo; [[ -n "$v" ]] || { echo "  (skipped)"; return 0; }; put "$1" "$v"
}
# Connection strings: Neon's direct host (no -pooler; its pooler runs in transaction mode), sslmode=require.
# (Supabase works too: Session pooler, port 5432.)
# pgurl <url> [app]: with "app", the same server signed in as dm_app.<project ref> with a new random password.
pgurl() { URL="$1" MODE="${2:-}" python3 -c '
import os, secrets, sys, urllib.parse as u
raw = os.environ["URL"].strip().strip("\"\x27")
if "[YOUR-PASSWORD]" in raw: sys.exit("Put the database password in place of [YOUR-PASSWORD] first.")
p = u.urlsplit(raw); host = p.netloc.rsplit("@", 1)[-1]; user = p.netloc.rsplit("@", 1)[0]
if (p.hostname or "").startswith("db.") and (p.hostname or "").endswith(".supabase.co"):
    sys.exit("That is the Direct connection (IPv6 only). Use Connect -> Session pooler.")
if host.endswith(":6543"): host = host[:-5] + ":5432"
if ".neon.tech" in host: host = host.replace("-pooler.", ".")
q = dict(u.parse_qsl(p.query)); q["sslmode"] = "require"; q.pop("ssl", None)
if os.environ["MODE"] == "app":
    name = user.split(":", 1)[0]; ref = ("." + name.split(".", 1)[1]) if "." in name else ""
    user = "dm_app" + ref + ":" + secrets.token_urlsafe(30)
print(u.urlunsplit(("postgresql", user + "@" + host, p.path if p.path not in ("", "/") else "/postgres", u.urlencode(q), "")))'; }
ask_db() {  # ask_db <secret> <prompt>
  local v; if has "$1"; then read -r -p "  $P-$1 already set. Replace it? [y/N] " a; [[ "$a" =~ ^[yY] ]] || return 0; fi
  read -r -s -p "  $2: " v; echo; [[ -n "$v" ]] || { echo "  (skipped)"; return 0; }
  v="$(pgurl "$v")" || return 1; put "$1" "$v"
}
gen() {  # gen <secret> <bytes>
  if has "$1"; then echo "  $P-$1 already set (kept)"; else put "$1" "$(openssl rand -base64 "$2" | tr -d '\n=' | tr '+/' '-_')"; fi
}

echo "Neon project dm-$ENV: Connect -> role dm_owner, database digital_marketing, Connection pooling OFF"
ask_db migrator-url "Owner connection string (dm_owner)"
if [[ "$ENV" == uat ]]; then
  ask database-url "App connection string (dm_app, from the laptop move script)"
elif ! has database-url; then
  # the runtime role dm_app is created by the deploy (python -m app.cli ensure-app-role) with this password
  put database-url "$(pgurl "$(gcloud secrets versions access latest --secret "$P-migrator-url")" app)"
fi
echo "Cloudflare R2 (bucket dm-$ENV-media): an R2 API token with Object Read & Write on that bucket"
ask r2-access-key-id "Access Key ID"
ask r2-secret-access-key "Secret Access Key"
if [[ "$ENV" == uat ]]; then
  echo "Encryption key: the laptop's, so the copied connections still open (the laptop move script copies it)"
  ask encryption-key "Encryption key"
fi
echo "Generated values"
gen secret-key 48
gen cron-secret 32
gen origin-verify-secret 32
if [[ "$ENV" == prod ]]; then
  if ! has encryption-key; then put encryption-key "$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"; fi
fi
if ! has admin-password; then
  PW="$(openssl rand -base64 18 | tr -d '\n=/+')Aa1!"; put admin-password "$PW"
  echo "  First-sign-in password for the platform admin is stored in $P-admin-password. To read it once:"
  echo "    gcloud secrets versions access latest --secret $P-admin-password"
fi
echo "Done for $ENV."
