"""Personal appearance/language preferences and AI translation of the interface."""

from app.api import preferences as prefs
from tests.test_content import new_org


async def test_preferences_round_trip(client):
    o = await new_org(client)
    assert (await client.get("/me/preferences", headers=o["h"])).json() == {}
    r = await client.put("/me/preferences", headers=o["h"], json={"theme": "dark", "accent": "teal", "font": "poppins",
                                                                   "text_size": "l", "language": "hi", "language_name": "Hindi"})
    assert r.status_code == 200 and r.json()["theme"] == "dark"
    r = await client.put("/me/preferences", headers=o["h"], json={"language": "Bhojpuri"})
    got = (await client.get("/me/preferences", headers=o["h"])).json()
    assert got["language"] == "Bhojpuri" and got["font"] == "poppins"
    assert (await client.put("/me/preferences", headers=o["h"], json={"theme": "neon"})).status_code == 422
    assert (await client.put("/me/preferences", headers=o["h"], json={"language": "<script>"})).status_code == 400


async def test_translate_caches_and_keeps_spaces(client, monkeypatch):
    o = await new_org(client)
    calls = []

    async def fake(session, task, prompt, **kw):
        calls.append(prompt)
        assert task == "translate" and "Tamil" in prompt
        return {"t": ["அனுப்பு", "வரைவுகள்"]}

    monkeypatch.setattr(prefs.llm_router, "complete_json", fake)
    body = {"language": "ta", "language_name": "Tamil", "texts": ["Send", " Drafts ", "42", "Send"]}
    r = (await client.post("/i18n/translate", headers=o["h"], json=body)).json()
    assert r["translations"] == {"Send": "அனுப்பு", " Drafts ": " வரைவுகள் "} and r["missing"] == 0
    # second time: from the cache, no AI call; the sign-in page can read the cache without signing in
    await client.post("/i18n/translate", headers=o["h"], json=body)
    assert len(calls) == 1
    r = (await client.post("/i18n/lookup", json={"language": "TA", "texts": ["Send", "Other"]})).json()
    assert r["translations"] == {"Send": "அனுப்பு"}
    assert (await client.post("/i18n/translate", headers=o["h"], json={"language": "en", "texts": ["Send"]})).json()["translations"] == {}


async def test_translate_bad_model_answer_is_skipped(client, monkeypatch):
    o = await new_org(client)

    async def short(session, task, prompt, **kw):
        return {"t": ["only one"]}

    monkeypatch.setattr(prefs.llm_router, "complete_json", short)
    r = (await client.post("/i18n/translate", headers=o["h"], json={"language": "Marathi", "texts": ["A b", "C d"]})).json()
    assert r["translations"] == {} and r["missing"] == 2
