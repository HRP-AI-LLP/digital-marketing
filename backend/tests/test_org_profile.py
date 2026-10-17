"""Organization profile: details, validation, logos."""

import io

import pytest
from PIL import Image

from tests.conftest import auth, login


@pytest.fixture
async def a(client, admin):
    return await login(client, "admin@hrpai.in", "Str0ngPassw0rd!")


def _png(color=(7, 84, 146)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (64, 32), color + (255,)).save(buf, "PNG")
    return buf.getvalue()


async def test_org_details_update_and_clear(client, a):
    h = auth(a)
    r = await client.patch("/organization", headers=h, json={
        "legal_name": "HRP AI LLP", "tagline": " AI for HR ", "industry": "Software", "company_size": "2-10",
        "founded_year": 2024, "email": "hello@hrpai.in", "phone": "+91 98765 43210", "country": "India",
        "address": {"line1": "MG Road", "city": "Bengaluru", "state": "Karnataka", "postal_code": "560001", "junk": "x"},
        "registration": {"gstin": "29abcde1234f1z5", "pan": "abcde1234f"},
        "social_links": {"linkedin": "linkedin.com/company/hrpai", "nope": "x"},
        "contacts": [{"name": "Prashant", "role": "Founder", "email": "p@hrpai.in"}],
    })
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["tagline"] == "AI for HR" and o["address"] == {"line1": "MG Road", "city": "Bengaluru", "state": "Karnataka", "postal_code": "560001"}
    assert o["registration"] == {"gstin": "29ABCDE1234F1Z5", "pan": "ABCDE1234F"}
    assert o["social_links"] == {"linkedin": "https://linkedin.com/company/hrpai"} and o["contacts"][0]["name"] == "Prashant"
    assert o["currency"] == "INR"
    # bad values are rejected
    for bad in ({"registration": {"gstin": "123"}}, {"company_size": "lots"}, {"phone": "call me"}, {"email": "nope"},
                {"founded_year": 1500}):
        assert (await client.patch("/organization", headers=h, json=bad)).status_code == 422, bad
    # clearing optional fields works; required ones are kept
    r = await client.patch("/organization", headers=h, json={"tagline": "", "address": None, "name": None})
    o = r.json()
    assert o["tagline"] is None and o["address"] == {} and o["name"]
    me = (await client.get("/auth/me", headers=h)).json()
    assert me["organization"]["legal_name"] == "HRP AI LLP"


async def test_org_logo_upload(client, a):
    h = auth(a)
    files = {"files": ("logo.png", _png(), "image/png")}
    r = await client.post("/organization/logo", headers=h, files=files)
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["logo_asset_id"] and o["logo_url"].startswith("/api/v1/media/file/")
    img = await client.get(o["logo_url"].removeprefix("/api/v1"))
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/")
    brand = (await client.get("/content/brand", headers=h)).json()
    assert brand["logo_asset_id"] == o["logo_asset_id"]  # becomes the brand logo when there was none
    r = await client.post("/organization/logo?variant=dark", headers=h, files={"files": ("d.png", _png((255, 255, 255)), "image/png")})
    assert r.json()["logo_dark_url"]
    bad = await client.post("/organization/logo", headers=h, files={"files": ("x.png", b"not an image", "image/png")})
    assert bad.status_code == 400
    r = await client.delete("/organization/logo?variant=dark", headers=h)
    assert r.json()["logo_dark_asset_id"] is None and r.json()["logo_asset_id"]
