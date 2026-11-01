"""Dashboard insights (filters, KPIs vs previous period, breakdowns) and the system map."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models import Job, PostMetric
from app.models.analytics_ext import AccountMetric
from app.services.jobs import record_periodic
from tests.test_content import add, mk_account, mk_post, new_org


async def _seed(oid):
    now = datetime.now(UTC)
    li = await mk_account(oid, platform="linkedin_org", name="HRP page")
    pin = await mk_account(oid, platform="pinterest", name="HRP pins")
    posts = []
    for i, (acc, typ, eng) in enumerate([(li, "text", 10), (li, "image", 30), (pin, "image", 5), (pin, "video", 0)]):
        p = await mk_post(oid, acc, status="published", post_type=typ, text=f"Post {i}", scheduled_at=now - timedelta(days=i + 1),
                          published_at=now - timedelta(days=i + 1))
        await add(oid, PostMetric(organization_id=oid, post_id=p.id, impressions=100 * (i + 1), likes=eng, clicks=2))
        posts.append(p)
    old = await mk_post(oid, li, status="published", text="Older", scheduled_at=now - timedelta(days=40),
                        published_at=now - timedelta(days=40))
    await add(oid, PostMetric(organization_id=oid, post_id=old.id, impressions=50, likes=4))
    for d, f in ((45, 900), (20, 1000), (1, 1100)):
        await add(oid, AccountMetric(organization_id=oid, social_account_id=li.id, platform="linkedin_org",
                                     day=(now - timedelta(days=d)).date(), followers=f))
    await mk_post(oid, pin, status="scheduled", text="Soon", scheduled_at=now + timedelta(days=2))
    return li, pin, posts


async def test_insights_kpis_breakdowns_and_filters(client):
    o = await new_org(client)
    li, pin, _ = await _seed(o["id"])
    r = await client.get("/dashboard/insights?days=30", headers=o["h"])
    assert r.status_code == 200, r.text
    d = r.json()
    k = {x["key"]: x for x in d["kpis"]}
    assert k["published"]["value"] == 4 and k["published"]["previous"] == 1 and k["published"]["delta"] == 300.0
    assert k["impressions"]["value"] == 1000 and k["engagements"]["value"] == 45 + 8  # likes + clicks
    assert k["followers"]["value"] == 1100 and k["followers"]["previous"] == 900
    assert len(d["daily"]) == 30 and len(k["published"]["spark"]) == 30
    assert {p["key"] for p in d["by_platform"]} == {"linkedin_org", "pinterest"}
    assert {t["key"] for t in d["by_post_type"]} == {"text", "image", "video"}
    assert [f["key"] for f in d["funnel"]][:4] == ["planned", "written", "approved", "published"]
    assert sum(u["total"] for u in d["upcoming"]) == 1
    assert d["top_posts"][0]["engagements"] == 32 and len(d["heatmap"]) >= 1
    assert {c["name"] for c in d["channels"]} == {"HRP page", "HRP pins"}
    assert "pinterest" in d["filters"]["platforms"]
    cube = d["heat_cube"]
    assert len(cube["weeks"]) == 5 and sum(c["posts"] for c in cube["cells"]) == 4
    assert all(0 <= c["w"] < 5 and 0 <= c["d"] < 7 and 0 <= c["h"] < 24 for c in cube["cells"])
    assert sum(sum(c["platforms"].values()) for c in cube["cells"]) == 4

    f = (await client.get("/dashboard/insights?days=30&platforms=pinterest", headers=o["h"])).json()
    assert {x["key"]: x for x in f["kpis"]}["published"]["value"] == 2 and [c["name"] for c in f["channels"]] == ["HRP pins"]
    f = (await client.get(f"/dashboard/insights?days=30&account_id={li.id}&post_type=image", headers=o["h"])).json()
    assert {x["key"]: x for x in f["kpis"]}["published"]["value"] == 1
    today = datetime.now(UTC).date()
    f = (await client.get(f"/dashboard/insights?start={today - timedelta(days=2)}&end={today}", headers=o["h"])).json()
    assert f["period"]["days"] == 3 and len(f["daily"]) == 3


async def test_system_map_states(client):
    o = await new_org(client)
    oid = o["id"]
    await _seed(oid)
    await add(oid, Job(organization_id=oid, kind="post.generate", status="done", finished_at=datetime.now(UTC)),
              Job(organization_id=oid, kind="post.publish", status="failed", error="Boom", finished_at=datetime.now(UTC)))
    await record_periodic("content_plan_and_dispatch", 60, True, None)
    d = (await client.get("/system/map", headers=o["h"])).json()
    agents = {a["id"]: a for a in d["agents"]}
    assert set(agents) >= {"planner", "writer", "publisher", "blog", "tracker"}
    # the worker isn't running in tests, so everything shows as stopped with a reason
    assert agents["writer"]["state"] == "stopped" and agents["writer"]["done_24h"] == 1
    assert agents["publisher"]["failed_24h"] == 1
    assert {a["platform"] for a in d["accounts"]} == {"linkedin_org", "pinterest"}
    assert any(s["id"] == "ai" for s in d["services"]) and d["flows"]
    assert uuid.UUID(d["accounts"][0]["id"])
