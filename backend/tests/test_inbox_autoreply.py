"""LinkedIn company page comments in the inbox, and AI auto-replies (drafts and automatic) with their guard rails."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import SessionLocal, set_tenant
from app.models.inbox import InboxItem
from app.services.inbox import autoreply, suggest
from tests.conftest import auth
from tests.test_inbox import NOW, _new_org, hrp, items, make_account, make_post, mock, no_ai, org_of, run_sync  # noqa: F401

LI = "urn:li:organization:9"
POST = "urn:li:share:1001"


@pytest.fixture(scope="module")
async def org3(client, hrp):  # noqa: F811
    return await _new_org(client, hrp, "Inbox Three", "owner@inbox-three.in")


@pytest.fixture
def ai(monkeypatch):
    async def fake(session, item, thread, post_text, *, emoji=None, extra=None):
        fake.calls.append({"emoji": emoji, "text": item.text})
        return [f"Thank you, {item.author_name}! 🙏", "Here's the answer 💡", "DM us 🚀"]
    fake.calls = []
    monkeypatch.setattr(suggest, "suggest_replies", fake)
    return fake


def li_comment(cid: int, text: str, actor="urn:li:person:abc", replies=0) -> dict:
    return {"commentUrn": f"urn:li:comment:(urn:li:activity:1,{cid})", "id": str(cid), "actor": actor,
            "object": POST, "message": {"text": text}, "created": {"time": int(NOW.timestamp() * 1000)},
            "commentsSummary": {"aggregatedTotalComments": replies}}


async def test_linkedin_page_comments_sync_and_reply(client, org3, mock):  # noqa: F811
    org = org_of(org3)
    acc = await make_account(org, "linkedin_org", LI, {"access_token": "lt"}, name="HRP AI LLP")
    await make_post(org, acc, POST, text="We launched payroll", url=f"https://www.linkedin.com/feed/update/{POST}/")
    mock.on("GET", r"/rest/socialActions/urn%3Ali%3Ashare%3A1001/comments$", (200, {"elements": [
        li_comment(1, "Congrats team!", replies=1), li_comment(2, "Our own note", actor=LI)]}))
    mock.on("GET", r"/rest/socialActions/urn%3Ali%3Acomment%3A.+1%29/comments$", (200, {"elements": [
        {**li_comment(3, "Agreed!"), "commentUrn": "urn:li:comment:(urn:li:activity:1,3)"}]}))
    await run_sync(org, acc.id)
    rows = {i.text: i for i in await items(org, social_account_id=acc.id)}
    assert rows["Congrats team!"].kind == "comment" and rows["Congrats team!"].author_name == "LinkedIn member"
    assert rows["Our own note"].is_own and rows["Agreed!"].kind == "reply"
    assert rows["Congrats team!"].post_id is not None

    mock.on("POST", r"/rest/socialActions/.+/comments$", (201, {"commentUrn": "urn:li:comment:(urn:li:activity:1,9)"}))
    r = await client.post(f"/inbox/items/{rows['Congrats team!'].id}/reply", headers=auth(org3), json={"text": "Thank you! 🙏"})
    assert r.status_code == 200, r.text
    body = json.loads(mock.find("POST", r"/rest/socialActions/")[0].content)
    assert body["actor"] == LI and body["object"] == POST and body["parentComment"].endswith(",1)")
    assert body["message"]["text"] == "Thank you! 🙏"


async def _comment(org, acc, text, *, sentiment="positive", priority=False, minutes_ago=30, created_offset=0):
    async with SessionLocal() as s:
        set_tenant(s, org)
        it = InboxItem(organization_id=org, source="account", platform=acc.platform, kind="comment",
                       dedupe_key=f"{acc.id}:{text}", social_account_id=acc.id, external_id=f"x-{text}",
                       thread_key=f"x-{text}", author_name="Lee", author_handle="@lee", text=text,
                       published_at=NOW - timedelta(minutes=minutes_ago), sentiment=sentiment, priority=priority,
                       status="new", raw={})
        s.add(it)
        await s.commit()
        if created_offset:
            it.created_at = datetime.now(UTC) + timedelta(minutes=created_offset)  # after auto-replies were switched on
            await s.commit()
        return it


async def test_auto_replies_drafts_then_automatic_with_guard_rails(client, org3, mock, ai):  # noqa: F811
    org = org_of(org3)
    acc = await make_account(org, "mastodon", "m.social:1", {"access_token": "t", "instance": "https://m.social"})
    old = await _comment(org, acc, "Before switching on")
    r = await client.put("/inbox/autoreply", headers=auth(org3), json={"enabled": True, "mode": "draft", "emoji": "rich",
                                                                       "delay_minutes": 0})
    assert r.status_code == 200 and r.json()["enabled"] and r.json()["started_at"]
    assert any(p["id"] == "linkedin_org" for p in r.json()["available_platforms"])
    good = await _comment(org, acc, "Love it", created_offset=1)
    angry = await _comment(org, acc, "This is broken", sentiment="negative", created_offset=1)
    q = await _comment(org, acc, "How much is it?", sentiment="question", created_offset=1)

    async with SessionLocal() as s:
        set_tenant(s, org)
        res = await autoreply.process(s, org)
    assert res["drafted"] == 2 and res["sent"] == 0
    rows = {i.text: i for i in await items(org, social_account_id=acc.id)}
    assert rows["Love it"].raw["auto_reply"]["text"] == "Thank you, Lee! 🙏"
    assert rows["How much is it?"].raw["auto_reply"]["text"] == "Here's the answer 💡"
    assert rows["This is broken"].raw["auto_reply"]["state"] == "skipped"  # negative: left for a person
    assert "auto_reply" not in (rows["Before switching on"].raw or {}) or rows["Before switching on"].raw["auto_reply"]["state"] == "skipped"
    assert ai.calls[0]["emoji"] == "rich"
    lst = (await client.get("/inbox/items", headers=auth(org3), params={"drafts": True})).json()
    assert {i["text"] for i in lst["items"]} == {"Love it", "How much is it?"}

    # approving a draft sends it and records the approval
    mock.on("POST", r"/api/v1/statuses$", (200, {"id": "800", "url": "https://m.social/@hrp/800"}))
    r = await client.post(f"/inbox/items/{good.id}/reply", headers=auth(org3), json={"text": "Thank you, Lee! 🙏"})
    assert r.status_code == 200 and r.json()["item"]["auto_reply"]["state"] == "approved"
    r = await client.post(f"/inbox/items/{q.id}/draft/discard", headers=auth(org3))
    assert r.json()["item"]["auto_reply"]["state"] == "skipped"

    # automatic mode sends straight away
    await client.put("/inbox/autoreply", headers=auth(org3), json={"mode": "auto"})
    mock.on("POST", r"/api/v1/statuses$", (200, {"id": "801", "url": "https://m.social/@hrp/801"}))
    nice = await _comment(org, acc, "Great update", created_offset=2)
    async with SessionLocal() as s:
        set_tenant(s, org)
        res = await autoreply.process(s, org)
    assert res["sent"] == 1
    sent = {i.text: i for i in await items(org, social_account_id=acc.id)}
    assert sent["Great update"].status == "replied" and sent["Great update"].raw["auto_reply"]["state"] == "sent"
    own = [i for i in await items(org, social_account_id=acc.id) if i.is_own]
    assert any((i.raw or {}).get("auto_reply") for i in own)
    assert angry.id and old.id and nice.id

    # switched off: nothing happens
    await client.put("/inbox/autoreply", headers=auth(org3), json={"enabled": False})
    await _comment(org, acc, "Another one", created_offset=3)
    async with SessionLocal() as s:
        set_tenant(s, org)
        assert (await autoreply.process(s, org))["sent"] == 0
