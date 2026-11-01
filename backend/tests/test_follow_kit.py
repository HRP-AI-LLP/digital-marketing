"""Follow kit: profiles from connected accounts and saved profile links, permanent short links, QR codes, barcodes,
the zip pack and the public /f/<slug> page (also through the tunnel)."""

import io
import uuid
import zipfile

import pytest

from app.core.config import get_settings
from app.core.db import SessionLocal, set_tenant
from app.models import Organization, SocialAccount
from app.services import barcode, qr
from tests.test_content import add, new_org

ROOT = "http://test"
BASE = "https://studio.example.in"


@pytest.fixture(autouse=True)
def public_base():
    s = get_settings()
    old = s.public_base_url
    s.public_base_url = BASE
    yield
    s.public_base_url = old


@pytest.fixture(scope="module")
async def org(client):
    o = await new_org(client)
    oid = o["id"]
    await add(oid, SocialAccount(id=uuid.uuid4(), organization_id=oid, platform="instagram", name="Acme", handle="acme.ai",
                                 status="connected", settings={}, profile_url="https://www.instagram.com/acme.ai/"))
    await add(oid, SocialAccount(id=uuid.uuid4(), organization_id=oid, platform="linkedin_person", name="Asha",
                                 handle="asha@example.com", status="connected", settings={}))
    await add(oid, SocialAccount(id=uuid.uuid4(), organization_id=oid, platform="discord", name="Acme #news",
                                 status="connected", settings={}, profile_url="https://discord.com/channels/1/2"))
    async with SessionLocal() as s:
        set_tenant(s, oid)
        row = await s.get(Organization, oid)
        row.social_links = {"instagram": "https://instagram.com/acme.ai", "github": "https://github.com/acme-ai",
                            "other": "https://mastodon.social/@acme, https://bsky.app/profile/acme.bsky.social"}
        await s.commit()
    return o


async def kit(client, o):
    r = await client.get("/follow", headers=o["h"])
    assert r.status_code == 200, r.text
    return r.json()


async def test_profiles_links_and_hints(client, org):
    k = await kit(client, org)
    by = {i["platform"]: i for i in k["items"]}
    # connected Instagram is listed once (the saved profile link is the same page)
    assert sum(1 for i in k["items"] if i["platform"] == "instagram") == 1
    ig = by["instagram"]
    assert ig["short_url"].startswith(f"{BASE}/r/") and ig["include"] and ig["source"] == "account"
    # sign-in email is never shown; a person profile and a members-only Discord channel need an address
    assert by["linkedin_person"]["handle"] is None and by["linkedin_person"]["url"] is None and by["linkedin_person"]["hint"]
    assert by["discord"]["url"] is None and "invite" in by["discord"]["hint"]
    assert {"github", "mastodon", "bluesky"} <= set(by)
    assert k["all"]["page_url"] == f"{BASE}/f/{k['settings']['slug']}" and k["all"]["short_url"].startswith(f"{BASE}/r/")
    # links are permanent: the same codes come back
    again = await kit(client, org)
    assert again["all"]["code"] == k["all"]["code"] and {i["key"]: i["code"] for i in again["items"]} == {i["key"]: i["code"] for i in k["items"]}


async def test_edit_url_keeps_code(client, org):
    k = await kit(client, org)
    dc = next(i for i in k["items"] if i["platform"] == "discord")
    r = await client.put("/follow", headers=org["h"], json={"items": [{"key": dc["key"], "url": "https://discord.gg/abc123", "include": True}],
                                                            "title": "Follow Acme", "slug": "acme-follow"})
    assert r.status_code == 200, r.text
    dc2 = next(i for i in r.json()["items"] if i["key"] == dc["key"])
    assert dc2["url"] == "https://discord.gg/abc123" and dc2["short_url"] and dc2["custom_url"]
    ig = next(i for i in r.json()["items"] if i["platform"] == "instagram")
    r2 = await client.put("/follow", headers=org["h"], json={"items": [{"key": ig["key"], "url": "https://www.instagram.com/acme.new/"}]})
    ig2 = next(i for i in r2.json()["items"] if i["key"] == ig["key"])
    assert ig2["code"] == ig["code"] and ig2["url"].endswith("acme.new/")
    redirect = await client.get(f"{ROOT}/r/{ig['code']}", follow_redirects=False,
                                headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"})
    assert redirect.status_code == 302 and redirect.headers["location"] == "https://www.instagram.com/acme.new/"  # no UTM tags
    bad = await client.put("/follow", headers=org["h"], json={"slug": "Bad Slug!"})
    assert bad.status_code == 400
    bad = await client.put("/follow", headers=org["h"], json={"items": [{"key": ig["key"], "url": "javascript:alert(1)"}]})
    assert bad.status_code == 400


async def test_codes_and_pack(client, org):
    k = await kit(client, org)
    ig = next(i for i in k["items"] if i["platform"] == "instagram")
    svg = await client.get(f"/follow/{ig['key']}/qr.svg", headers=org["h"], params={"style": "dots", "fg": "#075492"})
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg") and "#075492" in svg.text
    png = await client.get(f"/follow/{ig['key']}/qr.png", headers=org["h"], params={"size": 300, "download": True})
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG" and "attachment" in png.headers["content-disposition"]
    bar = await client.get("/follow/all/barcode.svg", headers=org["h"])
    assert bar.status_code == 200 and k["all"]["short_url"] in bar.text
    missing = await client.get("/follow/acc:nope/qr.png", headers=org["h"])
    assert missing.status_code == 404
    z = await client.get("/follow/pack.zip", headers=org["h"], params={"images": False})
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "links.csv" in names and any(n.startswith("all-channels/qr-") for n in names) and any("instagram" in n for n in names)


async def test_public_page_and_tunnel(client, org):
    k = await kit(client, org)
    slug = k["settings"]["slug"]
    r = await client.get(f"{ROOT}/f/{slug}")
    assert r.status_code == 200 and "Follow Acme" in r.text
    ig = next(i for i in k["items"] if i["platform"] == "instagram")
    assert f"/r/{ig['code']}" in r.text
    person = next(i for i in k["items"] if i["platform"] == "linkedin_person")
    assert person["include"] is False
    # switch a profile off: it leaves the page
    await client.put("/follow", headers=org["h"], json={"items": [{"key": ig["key"], "include": False}]})
    r = await client.get(f"{ROOT}/f/{slug}")
    assert f"/r/{ig['code']}" not in r.text
    assert (await client.get(f"{ROOT}/f/no-such-page")).status_code == 404
    tunnel = {"cf-ray": "x", "cf-connecting-ip": "1.2.3.4"}
    assert (await client.get(f"{ROOT}/f/{slug}", headers=tunnel)).status_code == 200
    assert (await client.get("/follow", headers={**org["h"], **tunnel})).status_code == 404


async def test_encoders_roundtrip_shapes():
    q = qr.best("https://studio.example.in/r/Ab12345", "H")
    assert q.ecl == "H"
    w = barcode.encode("Hi")
    assert sum(w) == 11 * 4 + 13  # start + 2 chars + check + stop
    with pytest.raises(ValueError):
        barcode.encode("naïve")
