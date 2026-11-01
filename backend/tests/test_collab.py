"""Team collaboration: saved inbox replies (CRUD, starter replies, variables), internal post comments with
@mentions and notifications, post version history with restore, permissions, the post PDF report and tenant
isolation. The database is real (RLS enforced); email sending is faked."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import app.modules.content  # noqa: F401  (registers job handlers)
from app.core.db import SessionLocal, platform_session, set_tenant
from app.models import (
    InboxItem, Notification, NotificationPref, Organization, Post, PostComment, PostVersion, SavedReply,
)
from app.services import collab
from app.services import reports as rep
from tests.conftest import auth, login
from tests.test_content import add, fake, generate, mk_account, new_org  # noqa: F401  (fake is a fixture)


# ------------------------------------------------------------------ helpers
async def set_org(org_id, **fields):
    async with platform_session() as s:
        org = await s.get(Organization, org_id)
        for k, v in fields.items():
            setattr(org, k, v)
        await s.commit()


async def add_user(client, o: dict, name: str, *, role: str | None = None, perms: list[str] | None = None) -> dict:
    """Create a member with a default role or a new custom role; returns tokens + ids."""
    h = o["h"]
    roles = {r["name"]: r["id"] for r in (await client.get("/roles", headers=h)).json()}
    if perms is not None:
        r = await client.post("/roles", headers=h, json={"name": f"Custom {uuid.uuid4().hex[:6]}", "permissions": perms})
        assert r.status_code == 201, r.text
        role_id = r.json()["id"]
    else:
        role_id = roles[role]
    email = f"{name.lower().replace(' ', '.')}-{uuid.uuid4().hex[:6]}@collab-test.in"
    r = await client.post("/users", headers=h, json={"email": email, "full_name": name, "role_ids": [role_id]})
    assert r.status_code == 201, r.text
    t = await login(client, email, r.json()["temporary_password"])
    return {"tokens": t, "h": auth(t), "id": uuid.UUID(t["user"]["id"]), "email": email, "name": name}


async def rows(org_id, q):
    async with SessionLocal() as s:
        set_tenant(s, org_id)
        return list((await s.scalars(q)).all())


async def create_post(client, o, acc, text="Our new payroll guide is live.", **kw) -> dict:
    r = await client.post("/content/posts", headers=o["h"], json={"account_id": str(acc.id), "text": text, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ================================================================== saved replies
async def test_saved_replies_seed_crud_and_variables(client):
    o = await new_org(client)
    await set_org(o["id"], website="https://example-co.in", email=None, phone=None)
    h = o["h"]
    r = await client.get("/inbox/saved-replies", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    titles = {i["title"] for i in d["items"]}
    assert titles == {"Thank you", "We'll message you", "Support contact", "Pricing enquiry"}
    assert set(d["variables"]) >= {"author_name", "company", "website", "my_name"} and d["can_manage"]
    pricing = next(i for i in d["items"] if i["title"] == "Pricing enquiry")
    support = next(i for i in d["items"] if i["title"] == "Support contact")
    assert "{website}" in pricing["text"]
    assert "{email}" not in support["text"] and "direct message" in support["text"]  # no email on file: no invented one
    # Seeded only once: second call doesn't duplicate.
    assert len((await client.get("/inbox/saved-replies", headers=h)).json()["items"]) == 4

    # Create / edit with platforms and tags.
    r = await client.post("/inbox/saved-replies", headers=h, json={
        "title": "Event", "text": "Hi {author_name}, {my_name} from {company} here. See {website}. Call {phone}.",
        "tags": ["events", "Events", " "], "platforms": ["linkedin_org"]})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["tags"] == ["events"]
    r = await client.patch(f"/inbox/saved-replies/{rid}", headers=h, json={"title": "Event invite"})
    assert r.status_code == 200 and r.json()["title"] == "Event invite"
    assert (await client.patch(f"/inbox/saved-replies/{rid}", headers=h, json={"text": "   "})).status_code == 400

    # Platform filter: linkedin-only reply hidden for x.
    x_titles = {i["title"] for i in (await client.get("/inbox/saved-replies?platform=x", headers=h)).json()["items"]}
    li_titles = {i["title"] for i in (await client.get("/inbox/saved-replies?platform=linkedin_org", headers=h)).json()["items"]}
    assert "Event invite" not in x_titles and "Event invite" in li_titles
    assert [i["title"] for i in (await client.get("/inbox/saved-replies?q=invite", headers=h)).json()["items"]] == ["Event invite"]

    # Use: variables filled from the inbox item, the organization and the user.
    item = await add(o["id"], InboxItem(id=uuid.uuid4(), organization_id=o["id"], platform="x", kind="comment",
                                        dedupe_key=f"t:{uuid.uuid4()}", author_name="Priya", text="Love it", raw={},
                                        ai_suggestions=[]))
    r = await client.post(f"/inbox/saved-replies/{rid}/use", headers=h, json={"item_id": str(item.id)})
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["text"].startswith("Hi Priya, Owner from Org ") and "See https://example-co.in." in u["text"]
    assert u["missing"] == ["phone"] and "{" not in u["text"] and "Call." in u["text"]
    r = await client.post(f"/inbox/saved-replies/{rid}/use", headers=h, json={})
    assert r.json()["text"].startswith("Hi there,")
    reply = (await rows(o["id"], select(SavedReply).where(SavedReply.id == uuid.UUID(rid))))[0]
    assert reply.usage_count == 2 and reply.last_used_at is not None
    # Most used first.
    assert (await client.get("/inbox/saved-replies", headers=h)).json()["items"][0]["id"] == rid

    # Delete everything: the starters don't come back.
    for i in (await client.get("/inbox/saved-replies", headers=h)).json()["items"]:
        assert (await client.delete(f"/inbox/saved-replies/{i['id']}", headers=h)).status_code == 204
    assert (await client.get("/inbox/saved-replies", headers=h)).json()["items"] == []
    assert (await client.delete(f"/inbox/saved-replies/{rid}", headers=h)).status_code == 404


async def test_fill_and_defaults_unit():
    text, missing = collab.fill("Hi {author_name}, visit {website} or write to {email}!", {
        "author_name": "Ann", "website": "", "email": "hi@x.in"})
    assert text == "Hi Ann, visit or write to hi@x.in!" and missing == ["website"]
    assert collab.fill("Keep {unknown} as is", {})[0] == "Keep {unknown} as is"
    org = Organization(name="Acme", website="https://acme.in", email="help@acme.in")
    d = {r["title"]: r["text"] for r in collab.default_replies(org)}
    assert "{email}" in d["Support contact"] and "{website}" in d["Pricing enquiry"]
    bare = {r["title"]: r["text"] for r in collab.default_replies(Organization(name="Acme"))}
    assert "{website}" not in bare["Pricing enquiry"] and "{email}" not in bare["Support contact"]


async def test_saved_reply_permissions(client):
    o = await new_org(client)
    replier = await add_user(client, o, "Rita Replier", perms=["inbox.view", "inbox.reply"])
    viewer = await add_user(client, o, "Vik Viewer", role="Viewer")
    r = await client.get("/inbox/saved-replies", headers=replier["h"])
    assert r.status_code == 200 and len(r.json()["items"]) == 4 and r.json()["can_manage"] is False
    rid = r.json()["items"][0]["id"]
    assert (await client.post(f"/inbox/saved-replies/{rid}/use", headers=replier["h"], json={})).status_code == 200
    assert (await client.post("/inbox/saved-replies", headers=replier["h"], json={"title": "x", "text": "y"})).status_code == 403
    assert (await client.patch(f"/inbox/saved-replies/{rid}", headers=replier["h"], json={"title": "z"})).status_code == 403
    assert (await client.delete(f"/inbox/saved-replies/{rid}", headers=replier["h"])).status_code == 403
    assert (await client.get("/inbox/saved-replies", headers=viewer["h"])).status_code == 403
    assert (await client.post(f"/inbox/saved-replies/{rid}/use", headers=viewer["h"], json={})).status_code == 403


# ================================================================== comments & mentions
async def test_comments_mentions_notifications_and_permissions(client, monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_sender(session, org):
        return object()

    async def fake_send_email(cfg, to, subject, body, link=None, **kw):
        sent.append((to, subject))

    import app.services.mailer as mailer
    import app.services.notify as notify

    monkeypatch.setattr(mailer, "default_sender", fake_sender)
    monkeypatch.setattr(notify, "send_email", fake_send_email)

    o = await new_org(client)
    acc = await mk_account(o["id"])
    writer = await add_user(client, o, "Wendy Writer", role="Content manager")
    reviewer = await add_user(client, o, "Ravi Reviewer", role="Reviewer")
    viewer = await add_user(client, o, "Vera Viewer", role="Viewer")
    # The writer gets email for mentions by default (no events chosen = defaults).
    await add(o["id"], NotificationPref(organization_id=o["id"], user_id=writer["id"], channel="email", enabled=True,
                                        events=[], target=writer["email"]))
    post = await create_post(client, o, acc, status="in_review",
                             scheduled_at=(datetime.now(UTC) + timedelta(days=2)).isoformat())
    pid = post["id"]

    members = (await client.get("/content/members", headers=o["h"])).json()
    assert {m["name"] for m in members} >= {"Wendy Writer", "Ravi Reviewer", "Vera Viewer", "Owner"}

    # Owner comments and mentions the writer (plus a stranger id that is ignored).
    stranger = uuid.uuid4()
    r = await client.post(f"/content/posts/{pid}/comments", headers=o["h"], json={
        "body": "@Wendy Writer can you make the opening shorter?", "mentions": [str(writer["id"]), str(stranger)]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["total"] == 1 and d["open"] == 1 and d["can_comment"]
    c = d["items"][0]
    assert c["author"]["name"] == "Owner" and [m["name"] for m in c["mentions"]] == ["Wendy Writer"] and c["mine"]
    cid = c["id"]

    notes = await rows(o["id"], select(Notification).where(Notification.organization_id == o["id"]))
    by_user = {(n.user_id, n.event) for n in notes}
    assert (writer["id"], "mention") in by_user
    assert (writer["id"], "post_comment") not in by_user  # mentioned people get only the mention
    assert (reviewer["id"], "post_comment") in by_user  # approvers hear about new comments
    assert (o["user_id"], "post_comment") not in by_user and (o["user_id"], "mention") not in by_user  # not yourself
    assert not any(n.user_id == viewer["id"] for n in notes)
    mention = next(n for n in notes if n.event == "mention")
    assert mention.title == "Owner mentioned you in a comment" and mention.link == f"/posts/{pid}#comments"
    assert [x for x in sent if "mentioned" in x[1] or "commented" in x[1]] == [
        (writer["email"], "Owner mentioned you in a comment")]  # "new comment" is in-app only by default

    # The writer replies; the post's creator (Owner) is told in-app.
    r = await client.post(f"/content/posts/{pid}/comments", headers=writer["h"], json={"body": "Done, have a look."})
    assert r.status_code == 201
    wid = next(i["id"] for i in r.json()["items"] if i["author"]["name"] == "Wendy Writer")
    notes = await rows(o["id"], select(Notification).where(Notification.event == "post_comment",
                                                           Notification.user_id == o["user_id"]))
    assert len(notes) == 1

    # Viewers read but can't comment or resolve; nobody edits or deletes someone else's comment.
    assert (await client.get(f"/content/posts/{pid}/comments", headers=viewer["h"])).json()["can_comment"] is False
    assert (await client.post(f"/content/posts/{pid}/comments", headers=viewer["h"], json={"body": "hi"})).status_code == 403
    assert (await client.post(f"/content/posts/{pid}/comments/{cid}/resolve", headers=viewer["h"], json={})).status_code == 403
    assert (await client.patch(f"/content/posts/{pid}/comments/{cid}", headers=writer["h"], json={"body": "x"})).status_code == 403
    assert (await client.delete(f"/content/posts/{pid}/comments/{cid}", headers=writer["h"])).status_code == 403
    assert (await client.post(f"/content/posts/{pid}/comments", headers=o["h"], json={"body": "   "})).status_code == 400

    # Edit own comment: adding a mention notifies only the newly mentioned person.
    r = await client.patch(f"/content/posts/{pid}/comments/{cid}", headers=o["h"], json={
        "body": "@Wendy Writer @Ravi Reviewer shorter opening please", "mentions": [str(writer["id"]), str(reviewer["id"])]})
    assert r.status_code == 200
    c = next(i for i in r.json()["items"] if i["id"] == cid)
    assert c["edited_at"] and {m["name"] for m in c["mentions"]} == {"Wendy Writer", "Ravi Reviewer"}
    ments = await rows(o["id"], select(Notification).where(Notification.event == "mention"))
    assert sorted(n.user_id for n in ments) == sorted([writer["id"], reviewer["id"]])

    # Resolve (reviewer may), reopen, counts on the posts list and the approvals cards.
    r = await client.post(f"/content/posts/{pid}/comments/{cid}/resolve", headers=reviewer["h"], json={"resolved": True})
    assert r.status_code == 200 and r.json()["open"] == 1
    c = next(i for i in r.json()["items"] if i["id"] == cid)
    assert c["resolved"] and c["resolved_by"]["name"] == "Ravi Reviewer"
    listed = (await client.get("/content/posts", headers=o["h"])).json()["items"]
    row = next(p for p in listed if p["id"] == pid)
    assert row["comment_count"] == 2 and row["open_comment_count"] == 1
    card = next(p for p in (await client.get("/content/approvals", headers=o["h"])).json() if p["id"] == pid)
    assert card["comment_count"] == 2
    detail = (await client.get(f"/content/posts/{pid}", headers=o["h"])).json()
    assert detail["comment_count"] == 2 and any(a["action"] == "commented" for a in detail["activities"])
    r = await client.post(f"/content/posts/{pid}/comments/{cid}/resolve", headers=o["h"], json={"resolved": False})
    assert r.json()["open"] == 2

    # Delete own. Comments never reach the published text.
    r = await client.delete(f"/content/posts/{pid}/comments/{wid}", headers=writer["h"])
    assert r.status_code == 200 and r.json()["total"] == 1
    p = (await rows(o["id"], select(Post).where(Post.id == uuid.UUID(pid))))[0]
    assert "shorter opening" not in (p.text or "")
    assert (await client.get(f"/content/posts/{uuid.uuid4()}/comments", headers=o["h"])).status_code == 404
    assert (await client.delete(f"/content/posts/{pid}/comments/{uuid.uuid4()}", headers=o["h"])).status_code == 404

    # The "mention" event is on by default for email (not Telegram) and listed in preferences.
    assert "mention" in notify.EVENTS and "mention" in notify.default_events("email")
    assert "mention" not in notify.default_events("telegram")


# ================================================================== versions
async def test_versions_snapshot_and_restore(client):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    h = o["h"]
    post = await create_post(client, o, acc, text="First draft about payroll.", hashtags=["#Payroll"])
    pid = post["id"]
    v = (await client.get(f"/content/posts/{pid}/versions", headers=h)).json()
    assert [i["version"] for i in v["items"]] == [1] and v["items"][0]["reason"] == "Written by hand"
    assert v["items"][0]["current"] and v["can_restore"]

    r = await client.patch(f"/content/posts/{pid}", headers=h, json={"text": "Second draft about payroll and tax.",
                                                                     "hashtags": ["#Payroll", "#Tax"]})
    assert r.status_code == 200 and r.json()["version"] == 2
    # A change of time only is not a content version.
    r = await client.patch(f"/content/posts/{pid}", headers=h, json={
        "scheduled_at": (datetime.now(UTC) + timedelta(days=3)).isoformat()})
    assert r.status_code == 200 and r.json()["version"] == 2
    r = await client.patch(f"/content/posts/{pid}", headers=h, json={"link_url": "https://example.in/guide"})
    assert r.json()["version"] == 3

    v = (await client.get(f"/content/posts/{pid}/versions", headers=h)).json()["items"]
    assert [i["version"] for i in v] == [3, 2, 1]
    assert v[1]["reason"] == "Edited text, hashtags" and v[1]["changed"] == ["text", "hashtags"]
    assert v[1]["changed_by"]["name"] == "Owner" and v[1]["fields"]["hashtags"] == ["Payroll", "Tax"]
    assert v[0]["changed"] == ["link_url"] and v[0]["current"] and not v[1]["current"]

    # Restore version 1: content comes back, QA re-runs like an edit, and the restore is itself a version.
    r = await client.post(f"/content/posts/{pid}/versions/1/restore", headers=h)
    assert r.status_code == 200 and r.json()["restored"], r.text
    d = (await client.get(f"/content/posts/{pid}", headers=h)).json()
    assert d["text"] == "First draft about payroll." and d["hashtags"] == ["Payroll"] and d["link_url"] is None
    assert d["qa"].get("edited") is True and d["version"] == 4
    assert any(a["action"] == "restored" for a in d["activities"])
    v = (await client.get(f"/content/posts/{pid}/versions", headers=h)).json()["items"]
    assert v[0]["reason"] == "Restored version 1" and v[0]["current"]
    # Restoring what is already there changes nothing.
    r = await client.post(f"/content/posts/{pid}/versions/4/restore", headers=h)
    assert r.json()["restored"] is False
    assert (await client.post(f"/content/posts/{pid}/versions/99/restore", headers=h)).status_code == 404

    # Viewers can see versions but not restore them.
    viewer = await add_user(client, o, "Vic Viewer", role="Viewer")
    vv = await client.get(f"/content/posts/{pid}/versions", headers=viewer["h"])
    assert vv.status_code == 200 and vv.json()["can_restore"] is False
    assert (await client.post(f"/content/posts/{pid}/versions/2/restore", headers=viewer["h"])).status_code == 403

    # Published posts can't be restored.
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        p = await s.get(Post, uuid.UUID(pid))
        p.status = "published"
        await s.commit()
    assert (await client.post(f"/content/posts/{pid}/versions/2/restore", headers=h)).status_code == 409
    assert (await client.get(f"/content/posts/{pid}/versions", headers=h)).json()["can_restore"] is False


async def test_restore_by_writer_sends_approved_post_back_to_review(client):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    writer = await add_user(client, o, "Walt Writer", perms=["content.view", "content.create"])
    post = await create_post(client, o, acc, text="Approved words.")
    pid = post["id"]
    await client.patch(f"/content/posts/{pid}", headers=o["h"], json={"text": "Approved words, improved."})
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        p = await s.get(Post, uuid.UUID(pid))
        p.status, p.approved_at, p.scheduled_at = "scheduled", datetime.now(UTC), datetime.now(UTC) + timedelta(days=1)
        await s.commit()
    r = await client.post(f"/content/posts/{pid}/versions/1/restore", headers=writer["h"])
    assert r.status_code == 200 and "approving again" in r.json()["message"]
    d = (await client.get(f"/content/posts/{pid}", headers=o["h"])).json()
    assert d["status"] == "in_review" and d["text"] == "Approved words."


async def test_ai_regenerate_stores_versions(client, fake):  # noqa: F811
    o = await new_org(client)
    acc = await mk_account(o["id"])
    post = await create_post(client, o, acc, text="Hand written version.")
    pid = post["id"]
    r = await client.post(f"/content/posts/{pid}/regenerate", headers=o["h"], json={"note": "make it punchier"})
    assert r.status_code == 200, r.text
    await generate(o["id"], uuid.UUID(pid))
    v = (await client.get(f"/content/posts/{pid}/versions", headers=o["h"])).json()["items"]
    assert [i["version"] for i in v] == [2, 1]
    assert v[0]["reason"] == "AI wrote a new version: make it punchier" and v[0]["changed_by_name"] == "AI"
    assert v[1]["fields"]["text"] == "Hand written version."
    assert v[0]["fields"]["text"].startswith("Unique post")


async def test_first_ai_generation_and_old_posts_get_baseline(client, fake):  # noqa: F811
    o = await new_org(client)
    acc = await mk_account(o["id"])
    # An AI-planned post: its first generation is version 1.
    p = await add(o["id"], Post(id=uuid.uuid4(), organization_id=o["id"], social_account_id=acc.id, platform=acc.platform,
                                status="planned", mode="approve_first", post_type="text", hashtags=[], media_ids=[],
                                qa={}, meta={}, scheduled_at=datetime.now(UTC) + timedelta(hours=10)))
    await generate(o["id"], p.id)
    vs = await rows(o["id"], select(PostVersion).where(PostVersion.post_id == p.id))
    assert [(x.version, x.reason) for x in vs] == [(1, "AI wrote the first version")]
    # A post written before version history existed: the first edit keeps the original as version 1.
    old = await add(o["id"], Post(id=uuid.uuid4(), organization_id=o["id"], social_account_id=acc.id, platform=acc.platform,
                                  status="draft", mode="manual", post_type="text", text="Old text", hashtags=[],
                                  media_ids=[], qa={}, meta={}))
    r = await client.patch(f"/content/posts/{old.id}", headers=o["h"], json={"text": "New text"})
    assert r.status_code == 200
    v = (await client.get(f"/content/posts/{old.id}/versions", headers=o["h"])).json()["items"]
    assert [(i["version"], i["reason"], i["fields"]["text"]) for i in v] == [
        (2, "Edited text", "New text"), (1, "Original version", "Old text")]


# ================================================================== report + isolation
async def test_post_report_has_comments_and_versions(client):
    o = await new_org(client)
    acc = await mk_account(o["id"])
    post = await create_post(client, o, acc, text="Report me.")
    pid = post["id"]
    await client.patch(f"/content/posts/{pid}", headers=o["h"], json={"text": "Report me, edited."})
    await client.post(f"/content/posts/{pid}/comments", headers=o["h"], json={"body": "Looks good to me"})
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org = await s.get(Organization, o["id"])
        _, doc, _ = await rep.build(s, org, "post", {"id": pid})
    tables = {b["title"]: b for b in doc.blocks if b["type"] == "table"}
    assert tables["Team comments"]["rows"][0][2] == "Looks good to me"
    assert tables["Team comments"]["rows"][0][4] == "Open"
    assert [r[0] for r in tables["Versions"]["rows"]] == ["2", "1"]
    assert tables["Versions"]["rows"][0][3] == "Edited text"


async def test_tenant_isolation(client):
    one, two = await new_org(client), await new_org(client)
    acc = await mk_account(one["id"])
    post = await create_post(client, one, acc, text="Private post.")
    pid = post["id"]
    await client.post(f"/content/posts/{pid}/comments", headers=one["h"], json={"body": "internal note"})
    replies = (await client.get("/inbox/saved-replies", headers=one["h"])).json()["items"]
    rid = replies[0]["id"]
    h2 = two["h"]
    assert (await client.get(f"/content/posts/{pid}/comments", headers=h2)).status_code == 404
    assert (await client.post(f"/content/posts/{pid}/comments", headers=h2, json={"body": "x"})).status_code == 404
    assert (await client.get(f"/content/posts/{pid}/versions", headers=h2)).status_code == 404
    assert (await client.post(f"/content/posts/{pid}/versions/1/restore", headers=h2)).status_code == 404
    assert (await client.post(f"/inbox/saved-replies/{rid}/use", headers=h2, json={})).status_code == 404
    assert (await client.patch(f"/inbox/saved-replies/{rid}", headers=h2, json={"title": "x"})).status_code == 404
    theirs = (await client.get("/inbox/saved-replies", headers=h2)).json()["items"]
    assert not {i["id"] for i in theirs} & {i["id"] for i in replies}
    # Members of another organization can't be mentioned.
    members_two = (await client.get("/content/members", headers=h2)).json()
    assert {m["id"] for m in members_two} == {str(two["user_id"])}
    acc2 = await mk_account(two["id"])
    p2 = await create_post(client, two, acc2, text="Their post.")
    r = await client.post(f"/content/posts/{p2['id']}/comments", headers=h2,
                          json={"body": "hi", "mentions": [str(one["user_id"])]})
    assert r.status_code == 201 and r.json()["items"][0]["mentions"] == []
    # Row-level security: org two's session sees none of org one's rows.
    for model in (PostComment, PostVersion, SavedReply):
        assert all(x.organization_id == two["id"] for x in await rows(two["id"], select(model)))
