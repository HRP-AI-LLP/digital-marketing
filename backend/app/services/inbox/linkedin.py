"""LinkedIn company page comments (Community Management API: r_organization_social to read,
w_organization_social to reply as the page).

Collect: GET /rest/socialActions/{post urn}/comments for each of our recent page posts (and the replies under
each comment). Reply: POST /rest/socialActions/{post urn}/comments with the page as actor and parentComment set.
LinkedIn doesn't let apps look up commenters' names, so people show as "LinkedIn member" with a link to the post.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from app.models import SocialAccount
from app.models.inbox import InboxItem
from app.services.inbox.base import CollectContext, Incoming, InboxPermissionError, parse_time
from app.services.social import common, net
from app.services.social import linkedin as li
from app.services.social.common import account_tokens

MAX_POSTS = 20


def _check(r: httpx.Response, what: str, permission: str) -> dict:
    if r.status_code == 403:
        raise InboxPermissionError("linkedin_org", permission, what)
    return common.ok_json(r, what)


def _tok(account: SocialAccount) -> str:
    tok = account_tokens(account).get("access_token")
    if not tok:
        raise common.PublishError("No access token stored. Connect the account again.", reconnect=True)
    return tok


def _comment_urn(el: dict) -> str | None:
    return el.get("commentUrn") or el.get("$URN") or (
        f"urn:li:comment:({el['object']},{el['id']})" if el.get("object") and el.get("id") else None)


def _to_incoming(el: dict, *, post_urn: str, post_url: str | None, org_urn: str, parent: str | None) -> Incoming | None:
    urn = _comment_urn(el)
    if not urn:
        return None
    actor = el.get("actor") or ""
    own = actor == org_urn
    text = ((el.get("message") or {}).get("text") or "").strip()
    who = "HRP AI" if own else ("A LinkedIn page" if actor.startswith("urn:li:organization") else "LinkedIn member")
    created = (el.get("created") or {}).get("time")
    return Incoming(
        external_id=urn, kind="reply" if parent else "comment", text=text,
        published_at=parse_time(created / 1000 if isinstance(created, (int, float)) else created),
        author_name=who, author_external_id=actor or None, url=post_url, parent_external_id=parent,
        thread_key=parent or urn, external_post_id=post_urn, external_post_url=post_url, is_own=own,
        engagement=((el.get("likesSummary") or {}).get("totalLikes") or None),
        raw={"post_urn": post_urn, "comment_urn": urn, "actor": actor},
    )


async def collect(session, account: SocialAccount, ctx: CollectContext) -> list[Incoming]:
    tok = _tok(account)
    org_urn = li.author_urn(account)
    out: list[Incoming] = []
    async with net.client() as c:
        for post_urn, ref in list(ctx.posts.items())[:MAX_POSTS]:
            d = _check(await c.get(f"{li.REST}/socialActions/{quote(post_urn, safe='')}/comments",
                                   params={"count": 50}, headers=li.headers(tok, False)),
                       "LinkedIn comments", "r_organization_social")
            for el in d.get("elements") or []:
                it = _to_incoming(el, post_urn=post_urn, post_url=ref.url, org_urn=org_urn, parent=None)
                if it is None or (it.published_at and it.published_at < ctx.since):
                    continue
                out.append(it)
                if int(((el.get("commentsSummary") or {}).get("aggregatedTotalComments")) or 0) > 0:
                    rd = _check(await c.get(f"{li.REST}/socialActions/{quote(it.external_id, safe='')}/comments",
                                            params={"count": 50}, headers=li.headers(tok, False)),
                                "LinkedIn replies", "r_organization_social")
                    for rel in rd.get("elements") or []:
                        rit = _to_incoming(rel, post_urn=post_urn, post_url=ref.url, org_urn=org_urn,
                                           parent=it.external_id)
                        if rit is not None:
                            out.append(rit)
    return out


async def reply(session, account: SocialAccount, item: InboxItem, text: str, state: dict) -> tuple[str, str | None]:
    tok = _tok(account)
    raw = item.raw or {}
    post_urn = raw.get("post_urn") or item.external_post_id
    comment_urn = raw.get("comment_urn") or item.external_id
    if not post_urn or not comment_urn:
        raise common.PublishError("This LinkedIn comment is missing its post reference. Open it on LinkedIn to reply.")
    body = {"actor": li.author_urn(account), "object": post_urn, "message": {"text": text},
            "parentComment": comment_urn}
    async with net.client() as c:
        r = await c.post(f"{li.REST}/socialActions/{quote(comment_urn, safe='')}/comments", json=body,
                         headers=li.headers(tok))
    if r.status_code == 403:
        raise InboxPermissionError("linkedin_org", "w_organization_social", "Replying on LinkedIn")
    d = common.ok_json(r, "Replying on LinkedIn") if r.content else {}
    new = (d or {}).get("commentUrn") or (d or {}).get("$URN") or r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id")
    return str(new or ""), item.external_post_url
