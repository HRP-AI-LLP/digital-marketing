"""PDF reports: every report builds for a real organization, the catalog follows permissions, and the export
returns a PDF."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.db import SessionLocal, set_tenant
from app.models import Organization, PostMetric, SeoArticle
from app.services import reports as rep
from tests.test_content import add, mk_account, mk_post, new_org


async def _seed(org_id):
    acc = await mk_account(org_id)
    now = datetime.now(UTC)
    p1 = await mk_post(org_id, acc, status="published", text="Published post", published_at=now - timedelta(days=1),
                       scheduled_at=now - timedelta(days=1), qa={"checks": [{"name": "length", "ok": True, "detail": "ok", "hard": True}],
                                                                "passed": True, "score": 8})
    await mk_post(org_id, acc, status="in_review", text="Waiting post", scheduled_at=now + timedelta(days=1))
    await mk_post(org_id, acc, status="failed", text="Failed post", error="Boom", scheduled_at=now - timedelta(hours=3))
    await add(org_id, PostMetric(organization_id=org_id, post_id=p1.id, impressions=120, likes=5, comments=2, raw={}))
    art = SeoArticle(id=uuid.uuid4(), organization_id=org_id, title="HR onboarding guide", slug="hr-onboarding",
                     keyword="hr onboarding", status="draft", body_md="# Guide\n\nSome **text**.", word_count=4, seo_score=70,
                     outline=[], faq=[{"q": "What?", "a": "This."}], secondary_keywords=[])
    await add(org_id, art)
    return p1, art


async def test_every_report_builds(client):
    o = await new_org(client)
    post, art = await _seed(o["id"])
    async with SessionLocal() as s:
        set_tenant(s, o["id"])
        org = await s.get(Organization, o["id"])
        for kind, spec in rep.catalog().items():
            if kind == "analytics":
                continue
            params = {"id": str(post.id) if kind == "post" else str(art.id)} if spec.needs_id else {}
            _, doc, _ = await rep.build(s, org, kind, params)
            assert doc.title and isinstance(doc.blocks, list), kind
            html = rep.render_html(doc, org, "data:image/png;base64,", "now")
            assert "<html" in html and doc.title.split()[0] in html, kind
        _, doc, _ = await rep.build(s, org, "post", {"id": str(post.id)})
        assert any(b["type"] == "table" and b["title"] == "History" for b in doc.blocks)
        _, doc, _ = await rep.build(s, org, "full", {})
        assert sum(b["type"] == "heading" for b in doc.blocks) >= 10
        with pytest.raises(rep.ReportInputError):
            await rep.build(s, org, "post", {})
        with pytest.raises(rep.ReportInputError):
            await rep.build(s, org, "posts", {"start": "bad"})


async def test_catalog_and_pdf_export(client, monkeypatch):
    o = await new_org(client)
    await _seed(o["id"])
    r = await client.get("/reports/catalog", headers=o["h"])
    kinds = {i["kind"] for i in r.json()["items"]}
    assert {"dashboard", "posts", "full", "analytics", "article", "activity"} <= kinds

    async def fake_pdf(html, title):
        assert "Posts report" in html
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(rep, "render_pdf", fake_pdf)
    r = await client.post("/reports/pdf", headers=o["h"], json={"kind": "posts", "params": {"status": "published"}})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert r.headers["content-type"] == "application/pdf" and "posts-report" in r.headers["content-disposition"]
    assert (await client.post("/reports/pdf", headers=o["h"], json={"kind": "nope"})).status_code == 404
    assert (await client.post("/reports/pdf", headers=o["h"], json={"kind": "post", "params": {}})).status_code == 400


async def test_real_pdf_when_chromium_is_available(client):
    from app.services import media_render

    try:
        await media_render.get_browser()
    except Exception:  # noqa: BLE001
        pytest.skip("Chromium isn't available here")
    o = await new_org(client)
    await _seed(o["id"])
    r = await client.post("/reports/pdf", headers=o["h"], json={"kind": "dashboard", "params": {}})
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF" and len(r.content) > 5000
