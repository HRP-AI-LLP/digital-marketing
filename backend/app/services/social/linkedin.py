"""LinkedIn: OAuth 2.0 (member + optional company pages), Posts API with Images / Videos / Documents
upload, token refresh and engagement counts."""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode

import httpx

from app.models import MediaAsset, Post, SocialAccount
from app.services.social import common, net
from app.services.social.common import PublishError, account_tokens, ok_json

LINKEDIN_VERSION = "202606"  # YYYYMM. LinkedIn keeps versions for about a year: bump it here.
AUTHORIZE = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
INTROSPECT = "https://www.linkedin.com/oauth/v2/introspectToken"
REST = "https://api.linkedin.com/rest"
V2 = "https://api.linkedin.com/v2"

MEMBER_SCOPES = ["openid", "profile", "email", "w_member_social"]
# Community Management API scopes (company pages). Requested only when "company pages" is chosen.
# If LinkedIn rejects one of these for your app, adjust this list.
ORG_SCOPES = ["r_organization_admin", "rw_organization_admin", "w_organization_social", "r_organization_social"]

VIDEO_POLLS = 60
VIDEO_POLL_SECONDS = 5

NOT_APPROVED = (
    "LinkedIn refused the company page permissions. Company pages need the Community Management API product "
    "on your LinkedIn app (request it in the app's Products tab; LinkedIn reviews it and needs a verified "
    "organization). Until it's approved, connect without company pages to post on your own profile."
)


def headers(tok: str, json_body: bool = True) -> dict:
    h = {"Authorization": f"Bearer {tok}", "LinkedIn-Version": LINKEDIN_VERSION,
         "X-Restli-Protocol-Version": "2.0.0"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def scopes(options: dict) -> list[str]:
    if options.get("app_variant") == "pages":
        return ORG_SCOPES  # the company-pages app has only the Community Management API
    if options.get("company_pages"):
        base = MEMBER_SCOPES if options.get("include_profile", True) else ["w_member_social"]
        return base + ORG_SCOPES
    return MEMBER_SCOPES


# ------------------------------------------------------------------ OAuth
def authorize_url(creds: dict, redirect_uri: str, state: str, options: dict, code_challenge: str | None) -> str:
    q = {"response_type": "code", "client_id": creds["client_id"], "redirect_uri": redirect_uri, "state": state,
         "scope": " ".join(scopes(options))}
    return f"{AUTHORIZE}?{urlencode(q)}"


def oauth_error_message(error: str, description: str | None) -> str | None:
    """Turn LinkedIn's callback errors into something actionable."""
    if error == "unauthorized_scope_error" or "scope" in (description or "").lower():
        return NOT_APPROVED + (f" (LinkedIn said: {description})" if description else "")
    return None


def _org_id(urn: str) -> str:
    return urn.rsplit(":", 1)[-1]


async def complete(session, org_id, user_id, creds: dict, redirect_uri: str, code: str, data: dict) -> list[SocialAccount]:
    options = data.get("options") or {}
    async with net.client() as c:
        t = ok_json(await c.post(TOKEN, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": creds["client_id"], "client_secret": creds["client_secret"]}), "LinkedIn sign-in")
        tok = t["access_token"]
        tokens = {"access_token": tok, "refresh_token": t.get("refresh_token"), "scope": t.get("scope"),
                  "refresh_expires_at": (e.isoformat() if (e := common.expires_from(t.get("refresh_token_expires_in"))) else None)}
        expires = common.expires_from(t.get("expires_in"))

        variant = options.get("app_variant")
        person = None if variant == "pages" else await _person(c, tok)
        orgs: list[dict] = []
        if options.get("company_pages"):
            orgs = await _organizations(c, tok)

    accounts: list[SocialAccount] = []
    if person:
        accounts.append(await common.upsert_account(
            session, org_id, user_id, "linkedin_person", person["urn"], name=person["name"], handle=person.get("email"),
            avatar_url=person.get("picture"), profile_url=None, tokens=tokens, expires_at=expires,
            settings={"person_urn": person["urn"]},
        ))
    for o in orgs:
        accounts.append(await common.upsert_account(
            session, org_id, user_id, "linkedin_org", o["urn"], name=o["name"], handle=o.get("vanity"),
            avatar_url=None, profile_url=f"https://www.linkedin.com/company/{o.get('vanity') or _org_id(o['urn'])}/",
            tokens=tokens, expires_at=expires, settings={"org_urn": o["urn"], **({"app_variant": variant} if variant else {})},
        ))
    if not accounts:
        raise PublishError("LinkedIn didn't return a profile or any company page you administer.")
    return accounts


async def _person(c: httpx.AsyncClient, tok: str) -> dict | None:
    r = await c.get(f"{V2}/userinfo", headers={"Authorization": f"Bearer {tok}"})
    if r.is_success:
        u = r.json()
        return {"urn": f"urn:li:person:{u['sub']}", "name": u.get("name") or u.get("email") or "LinkedIn member",
                "email": u.get("email"), "picture": u.get("picture")}
    # Apps without OpenID Connect (e.g. a Community-Management-only app): fall back to /v2/me.
    r = await c.get(f"{V2}/me", headers={"Authorization": f"Bearer {tok}"})
    if r.is_success:
        u = r.json()
        name = " ".join(x for x in (u.get("localizedFirstName"), u.get("localizedLastName")) if x) or "LinkedIn member"
        return {"urn": f"urn:li:person:{u['id']}", "name": name}
    return None


async def _organizations(c: httpx.AsyncClient, tok: str) -> list[dict]:
    r = await c.get(f"{REST}/organizationAcls", params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"},
                    headers=headers(tok, False))
    if r.status_code in (401, 403):
        raise PublishError(NOT_APPROVED)
    d = ok_json(r, "LinkedIn company pages")
    out = []
    for el in d.get("elements", []):
        urn = el.get("organization") or el.get("organizationTarget")
        if not urn:
            continue
        info = {"urn": urn, "name": f"Company page {_org_id(urn)}"}
        try:
            od = await c.get(f"{REST}/organizations/{_org_id(urn)}", headers=headers(tok, False))
            if od.is_success:
                j = od.json()
                info["name"] = j.get("localizedName") or info["name"]
                info["vanity"] = j.get("vanityName")
        except httpx.HTTPError:
            pass
        out.append(info)
    return out


async def refresh(session, account: SocialAccount) -> None:
    tokens = account_tokens(account)
    if not tokens.get("access_token"):
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    creds = await common.app_credentials(session, "linkedin", (account.settings or {}).get("app_variant"))
    async with net.client() as c:
        if common.expiring(account) and tokens.get("refresh_token") and creds:
            t = ok_json(await c.post(TOKEN, data={
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                "client_id": creds["client_id"], "client_secret": creds["client_secret"]}), "LinkedIn token refresh")
            tokens.update(access_token=t["access_token"], refresh_token=t.get("refresh_token") or tokens["refresh_token"])
            common.save_tokens(account, tokens, common.expires_from(t.get("expires_in")))
        if creds:
            d = ok_json(await c.post(INTROSPECT, data={
                "client_id": creds["client_id"], "client_secret": creds["client_secret"],
                "token": tokens["access_token"]}), "Checking the LinkedIn token")
            if not d.get("active"):
                raise PublishError("The LinkedIn token is no longer active. Connect the account again.", reconnect=True)
            if d.get("expires_at"):
                account.token_expires_at = datetime.fromtimestamp(int(d["expires_at"]), UTC)
    if common.expiring(account, within=timedelta(0)):
        raise PublishError("The LinkedIn token has expired. Connect the account again.", reconnect=True)


# ------------------------------------------------------------------ text
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")
_TAG = re.compile(r"(?<![\w#])#(\w+)", re.UNICODE)


def little_text(text: str) -> str:
    """Escape LinkedIn's 'little text' commentary format; keep #hashtags as hashtag links."""
    out, last = [], 0
    for m in _TAG.finditer(text):
        out.append(_RESERVED.sub(r"\\\1", text[last:m.start()]))
        out.append("{hashtag|\\#|" + _RESERVED.sub(r"\\\1", m.group(1)) + "}")
        last = m.end()
    out.append(_RESERVED.sub(r"\\\1", text[last:]))
    return "".join(out)


# ------------------------------------------------------------------ uploads
async def _upload_image(c: httpx.AsyncClient, tok: str, owner: str, asset: MediaAsset) -> str:
    d = ok_json(await c.post(f"{REST}/images", params={"action": "initializeUpload"}, headers=headers(tok),
                             json={"initializeUploadRequest": {"owner": owner}}), "LinkedIn image upload")["value"]
    common.raise_for(await c.put(d["uploadUrl"], content=common.read_bytes(asset),
                                 headers={"Authorization": f"Bearer {tok}"}), "LinkedIn image upload")
    return d["image"]


async def _upload_document(c: httpx.AsyncClient, tok: str, owner: str, asset: MediaAsset) -> str:
    d = ok_json(await c.post(f"{REST}/documents", params={"action": "initializeUpload"}, headers=headers(tok),
                             json={"initializeUploadRequest": {"owner": owner}}), "LinkedIn document upload")["value"]
    common.raise_for(await c.put(d["uploadUrl"], content=common.read_bytes(asset),
                                 headers={"Authorization": f"Bearer {tok}"}), "LinkedIn document upload")
    return d["document"]


async def _upload_video(c: httpx.AsyncClient, tok: str, owner: str, asset: MediaAsset) -> str:
    data = common.read_bytes(asset)
    d = ok_json(await c.post(f"{REST}/videos", params={"action": "initializeUpload"}, headers=headers(tok), json={
        "initializeUploadRequest": {"owner": owner, "fileSizeBytes": len(data), "uploadCaptions": False,
                                    "uploadThumbnail": False}}), "LinkedIn video upload")["value"]
    etags = []
    for part in d.get("uploadInstructions", []):
        chunk = data[int(part["firstByte"]): int(part["lastByte"]) + 1]
        r = await c.put(part["uploadUrl"], content=chunk, headers={"Content-Type": "application/octet-stream"})
        common.raise_for(r, "LinkedIn video upload")
        etags.append(r.headers.get("etag", "").strip('"') or r.headers.get("ETag", ""))
    common.raise_for(await c.post(f"{REST}/videos", params={"action": "finalizeUpload"}, headers=headers(tok), json={
        "finalizeUploadRequest": {"video": d["video"], "uploadToken": d.get("uploadToken", ""),
                                  "uploadedPartIds": etags}}), "LinkedIn video upload")
    # Wait until LinkedIn has processed it; posting earlier can fail.
    for _ in range(VIDEO_POLLS):
        v = ok_json(await c.get(f"{REST}/videos/{quote(d['video'], safe='')}", headers=headers(tok, False)),
                    "LinkedIn video status")
        st = v.get("status")
        if st == "AVAILABLE" or st is None:
            break
        if st in ("PROCESSING_FAILED",):
            raise PublishError("LinkedIn couldn't process the video.")
        await common.sleep(VIDEO_POLL_SECONDS)
    return d["video"]


# ------------------------------------------------------------------ publish
def author_urn(account: SocialAccount) -> str:
    return account.external_id or (account.settings or {}).get("org_urn") or (account.settings or {}).get("person_urn")


async def publish(session, post: Post, account: SocialAccount, media: list[MediaAsset]) -> tuple[str | None, str | None]:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise PublishError("No access token stored. Connect the account again.", reconnect=True)
    author = author_urn(account)
    body = {
        "author": author,
        "commentary": little_text(common.compose_text(post)),
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    async with net.client(net.UPLOAD_TIMEOUT) as c:
        docs = [m for m in media if common.is_pdf(m)]
        videos = [m for m in media if common.is_video(m)]
        images = [m for m in media if not common.is_pdf(m) and not common.is_video(m)]
        if docs:
            urn = await _upload_document(c, tok, author, docs[0])
            body["content"] = {"media": {"id": urn, "title": (post.title or post.topic or "Document")[:200]}}
        elif videos:
            urn = await _upload_video(c, tok, author, videos[0])
            body["content"] = {"media": {"id": urn, "title": (post.title or "")[:200]}}
        elif len(images) > 1:
            ids = [await _upload_image(c, tok, author, m) for m in images[:20]]
            body["content"] = {"multiImage": {"images": [
                {"id": i, "altText": (m.alt_text or "")[:4086]} for i, m in zip(ids, images)]}}
        elif images:
            urn = await _upload_image(c, tok, author, images[0])
            body["content"] = {"media": {"id": urn, "altText": (images[0].alt_text or "")[:4086]}}
        elif post.link_url:
            body["content"] = {"article": {"source": post.link_url, "title": (post.title or post.topic or post.link_url)[:400]}}
        r = await c.post(f"{REST}/posts", headers=headers(tok), json=body)
        common.raise_for(r, "LinkedIn post")
    urn = r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id")
    if not urn:
        try:
            urn = r.json().get("id")
        except ValueError:
            urn = None
    return urn, (f"https://www.linkedin.com/feed/update/{urn}/" if urn else None)


# ------------------------------------------------------------------ metrics
async def fetch_metrics(session, post: Post, account: SocialAccount) -> dict | None:
    tok = account_tokens(account).get("access_token")
    if not tok or not post.external_id:
        return None
    urn = post.external_id
    async with net.client() as c:
        if account.platform == "linkedin_org":
            kind = "ugcPosts" if ":ugcPost:" in urn else "shares"
            # Rest.li 2.0 list syntax; the URN inside List(...) must be URL-encoded.
            url = (f"{REST}/organizationalEntityShareStatistics?q=organizationalEntity"
                   f"&organizationalEntity={quote(author_urn(account), safe='')}&{kind}=List({quote(urn, safe='')})")
            d = ok_json(await c.get(url, headers=headers(tok, False)), "LinkedIn statistics")
            s = ((d.get("elements") or [{}])[0]).get("totalShareStatistics") or {}
            return common.metrics(d, impressions=s.get("impressionCount"), reach=s.get("uniqueImpressionsCount"),
                                  clicks=s.get("clickCount"), likes=s.get("likeCount"), comments=s.get("commentCount"),
                                  shares=s.get("shareCount"))
        d = ok_json(await c.get(f"{REST}/socialActions/{quote(urn, safe='')}", headers=headers(tok, False)),
                    "LinkedIn social actions")
        return common.metrics(d, likes=(d.get("likesSummary") or {}).get("totalLikes"),
                              comments=(d.get("commentsSummary") or {}).get("aggregatedTotalComments"))
