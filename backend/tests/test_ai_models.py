"""Model lists, automatic fallback with cooldowns, and per-model usage."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.services.llm import client as llm
from app.services.llm import discovery, health
from tests.conftest import auth, login

ADMIN_PW = "Str0ngPassw0rd!"


@pytest.fixture
async def a(client, admin):
    return await login(client, "admin@hrpai.in", ADMIN_PW)


@pytest.fixture
def no_discovery(monkeypatch):
    """Adding a provider lists its models; keep tests offline."""
    async def fake(cfg):
        return [("good", {}), ("bad", {}), ("nomic-embed-text", {}), ("gemini-embedding-2-preview", {})]
    monkeypatch.setattr(discovery, "_openai_style", fake)


# ----------------------------------------------------------------------------- classification
async def test_classify_errors():
    now = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)
    v = health.classify(llm.ProviderError('402: Your prepayment credits are depleted.', 402), now=now)
    assert v.state == "billing"
    v = health.classify(llm.ProviderError("429: Quota exceeded for metric GenerateRequestsPerDay", 429), now=now)
    assert v.state == "cooldown" and v.reason == "Daily limit reached"
    assert v.seconds == int((datetime(2026, 10, 17, 8, 0, tzinfo=UTC) - now).total_seconds()) + 1
    v = health.classify(llm.ProviderError("429: Rate limit reached. Please try again in 20s.", 429), now=now)
    assert v.state == "cooldown" and v.seconds == 30  # at least 30 s
    v = health.classify(llm.ProviderError("429: rate limit, try again in 5m", 429), now=now)
    assert v.seconds == 300
    assert health.classify(llm.ProviderError("401: Incorrect API key provided", 401)).state == "auth"
    assert health.classify(llm.ProviderError("404: model 'llama9' not found", 404)).state == "unavailable"
    assert health.classify(Exception("The model `x` has been decommissioned")).state == "unavailable"
    v = health.classify(Exception("All connection attempts failed"), local=True)
    assert v.state == "cooldown" and v.seconds == 120
    assert health.classify(Exception("Replicate: 402 Insufficient credit")).state == "billing"
    assert health.classify(Exception("400: bad request")).state == "none"


async def test_classify_model_types():
    c = discovery.classify_model
    assert c("nomic-embed-text") == "embed"
    assert c("text-embedding-3-small") == "embed"
    assert c("gemini-3.8-flash") == "chat"
    assert c("gpt-image-1") == "image"
    assert c("gpt-4o-mini-tts") == "tts_model"
    assert c("whisper-1") == "other"
    assert c("qwen2.5-vl-7b") == "chat"
    assert c("models/x", {"supportedGenerationMethods": ["embedContent"]}) == "embed"


# ----------------------------------------------------------------------------- fallback + cooldown
async def test_fallback_rests_limited_model(client, a, monkeypatch, no_discovery):
    p1 = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "groq", "name": "first",
          "secrets": {"api_key": "k1"}, "settings": {"default_model": "bad"}})).json()["id"]
    p2 = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "lmstudio", "name": "second",
          "settings": {"default_model": "good"}})).json()["id"]
    await client.put("/integrations/routes", headers=auth(a), json={"routes": {"write": [
        {"provider_id": p1, "model": "bad"}, {"provider_id": p2, "model": "good"}]}})
    calls: list[str] = []

    async def fake_chat(conn, model, messages, **kw):
        calls.append(model)
        if model == "bad":
            raise llm.ProviderError("429: Quota exceeded for metric GenerateRequestsPerDay", 429)
        return llm.ChatResult("hello", 5, 3, 10, model)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = await client.post("/integrations/playground", headers=auth(a), json={"task": "write", "prompt": "hi"})
    assert r.json()["model"] == "good" and calls == ["bad", "good"]

    st = (await client.get("/integrations/model-status", headers=auth(a))).json()["statuses"]
    bad = next(x for x in st if x["model"] == "bad")
    assert bad["state"] == "cooldown" and bad["reason"] == "Daily limit reached" and bad["until"]

    # Next call goes straight to the backup.
    calls.clear()
    r = await client.post("/integrations/playground", headers=auth(a), json={"task": "write", "prompt": "hi"})
    assert r.json()["model"] == "good" and calls == ["good"]

    # 'Try again now' clears the rest period.
    assert (await client.post("/integrations/model-status/reset", headers=auth(a),
                              json={"provider_id": p1, "model": "bad"})).status_code == 204
    calls.clear()
    await client.post("/integrations/playground", headers=auth(a), json={"task": "write", "prompt": "hi"})
    assert calls == ["bad", "good"]

    # Usage keeps per-model totals, all time.
    u = (await client.get("/integrations/usage?days=0", headers=auth(a))).json()
    good = next(m for m in u["models"] if m["model"] == "good")
    assert good["calls"] == 3 and good["input_tokens"] == 15 and good["output_tokens"] == 9 and good["integration"] == "second"
    badrow = next(m for m in u["models"] if m["model"] == "bad")
    assert badrow["failures"] == 2 and u["totals"]["calls"] >= 5

    await client.put("/integrations/routes", headers=auth(a), json={"routes": {"write": []}})
    for p in (p1, p2):
        await client.delete(f"/integrations/providers/{p}", headers=auth(a))


async def test_resting_models_are_last_resort(client, a, monkeypatch, no_discovery):
    p1 = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "lmstudio", "name": "only",
          "settings": {"default_model": "good"}})).json()["id"]
    from app.core.db import SessionLocal, set_tenant
    from app.models import ModelStatus, ProviderConfig

    async with SessionLocal() as s:
        set_tenant(s, a["organization"]["id"])
        s.add(ModelStatus(organization_id=a["organization"]["id"], provider_id=p1, model="good", state="cooldown",
                          reason="Rate limit reached", cooldown_until=datetime.now(UTC) + timedelta(hours=1)))
        await s.commit()

    async def fake_chat(conn, model, messages, **kw):
        return llm.ChatResult("ok", 1, 1, 1, model)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = await client.post("/integrations/playground", headers=auth(a), json={"task": "write", "prompt": "hi"})
    assert r.status_code == 200
    async with SessionLocal() as s:
        set_tenant(s, a["organization"]["id"])
        row = await s.scalar(select(ModelStatus).where(ModelStatus.provider_id == p1))
        assert row.state == "ok" and row.cooldown_until is None  # success clears the rest
        assert await s.get(ProviderConfig, p1)
    await client.delete(f"/integrations/providers/{p1}", headers=auth(a))


async def test_embedding_fallback(client, a, monkeypatch, no_discovery):
    p1 = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "groq", "name": "e1",
          "secrets": {"api_key": "k"}, "settings": {"embed_model": "bad-embed"}})).json()["id"]
    p2 = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "lmstudio", "name": "e2",
          "settings": {"embed_model": "nomic-embed-text"}})).json()["id"]
    await client.put("/integrations/routes", headers=auth(a), json={"routes": {"embed": [
        {"provider_id": p1, "model": "bad-embed"}, {"provider_id": p2, "model": "nomic-embed-text"}]}})

    async def fake_embed(conn, model, texts):
        if model == "bad-embed":
            raise llm.ProviderError("402: credits depleted", 402)
        return [[0.1, 0.2, 0.3] for _ in texts], 7

    monkeypatch.setattr(llm, "embed", fake_embed)
    from app.core.db import SessionLocal, set_tenant
    from app.services.llm.router import embed_texts

    async with SessionLocal() as s:
        set_tenant(s, a["organization"]["id"])
        vecs, tag = await embed_texts(s, ["one", "two"])
        await s.commit()
    assert tag == "lmstudio:nomic-embed-text" and len(vecs) == 2
    st = {x["model"]: x for x in (await client.get("/integrations/model-status", headers=auth(a))).json()["statuses"]}
    assert st["bad-embed"]["state"] == "billing"
    # A billing problem notifies the admin once.
    n = (await client.get("/notifications", headers=auth(a))).json()
    assert any("credits" in i["title"] for i in n["items"])
    await client.put("/integrations/routes", headers=auth(a), json={"routes": {"embed": []}})
    for p in (p1, p2):
        await client.delete(f"/integrations/providers/{p}", headers=auth(a))


# ----------------------------------------------------------------------------- model lists
async def test_model_list_sync_and_missing(client, a, monkeypatch):
    offered = [("gemini-3.8-flash", {}), ("gemini-embedding-2-preview", {}), ("whisper-1", {})]

    async def fake(cfg):
        return list(offered)

    monkeypatch.setattr(discovery, "_openai_style", fake)
    r = await client.post("/integrations/providers", headers=auth(a), json={
        "provider": "mistral", "secrets": {"api_key": "k"},
        "settings": {"default_model": "gemini-3.8-flash", "embed_model": "text-embedding-004"}})
    pid = r.json()["id"]
    m = (await client.get(f"/integrations/providers/{pid}/models", headers=auth(a))).json()
    assert m["source"] == "live" and m["models"][0] == "gemini-3.8-flash"
    assert m["types"]["gemini-embedding-2-preview"] == "embed" and m["types"]["whisper-1"] == "other"
    assert m["missing"] == ["text-embedding-004"]

    # The provider drops the writing model: a refresh flags it and notifies once.
    offered[:] = [("gemini-4-flash", {}), ("gemini-embedding-2-preview", {})]
    m = (await client.get(f"/integrations/providers/{pid}/models?refresh=true", headers=auth(a))).json()
    assert "gemini-4-flash" in m["models"] and set(m["missing"]) == {"gemini-3.8-flash", "text-embedding-004"}
    st = {x["model"]: x for x in (await client.get("/integrations/model-status", headers=auth(a))).json()["statuses"]}
    assert st["gemini-3.8-flash"]["state"] == "unavailable"
    titles = [i["title"] for i in (await client.get("/notifications", headers=auth(a))).json()["items"]]
    assert sum("gemini-3.8-flash" in t for t in titles) == 1

    # Refresh everything (the worker does this every 6 hours).
    r = (await client.post("/integrations/models/refresh", headers=auth(a))).json()
    assert any(p["id"] == pid and p["ok"] for p in r["providers"])
    titles = [i["title"] for i in (await client.get("/notifications", headers=auth(a))).json()["items"]]
    assert sum("gemini-3.8-flash" in t for t in titles) == 1  # no repeat notification
    await client.delete(f"/integrations/providers/{pid}", headers=auth(a))


async def test_discover_before_saving(client, a, monkeypatch):
    async def fake(cfg):
        assert cfg.provider == "groq"
        return [("llama-3.3-70b-versatile", {}), ("llama-3.1-8b-instant", {})]

    monkeypatch.setattr(discovery, "_openai_style", fake)
    r = await client.post("/integrations/discover", headers=auth(a), json={"provider": "groq", "secrets": {"api_key": "k"}})
    assert r.status_code == 200 and "llama-3.1-8b-instant" in r.json()["models"]


async def test_suggested_lists_for_media_providers(client, a):
    r = await client.post("/integrations/providers", headers=auth(a), json={"provider": "stability", "secrets": {"api_key": "k"}})
    m = (await client.get(f"/integrations/providers/{r.json()['id']}/models", headers=auth(a))).json()
    assert "core" in m["models"] and m["types"]["core"] == "image"
    await client.delete(f"/integrations/providers/{r.json()['id']}", headers=auth(a))


async def test_media_routes_check_kind(client, a, no_discovery):
    img = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "stability", "secrets": {"api_key": "k"}})).json()["id"]
    txt = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "lmstudio"})).json()["id"]
    r = await client.put("/integrations/routes", headers=auth(a), json={"routes": {"image": [{"provider_id": txt, "model": "x"}]}})
    assert r.status_code == 400
    r = await client.put("/integrations/routes", headers=auth(a), json={"routes": {"image": [{"provider_id": img, "model": "core"}]}})
    assert r.status_code == 200 and r.json()["routes"]["image"][0]["model"] == "core"
    assert "tts" in r.json()["route_tasks"]
    await client.put("/integrations/routes", headers=auth(a), json={"routes": {"image": []}})
    for p in (img, txt):
        await client.delete(f"/integrations/providers/{p}", headers=auth(a))


async def test_voice_falls_back_to_edge(client, a, monkeypatch, tmp_path):
    from app.core.db import SessionLocal, set_tenant
    from app.services import media_render, tts

    vo = (await client.post("/integrations/providers", headers=auth(a), json={
        "provider": "openai_tts", "secrets": {"api_key": "k"}, "settings": {"voice": "nova"}})).json()["id"]

    async def bad_openai(cfg, text, voice, out):
        raise tts.TtsError("OpenAI text-to-speech: 429 rate limit reached")

    async def fake_edge(text, voice, out, rate="+0%"):
        out.write_bytes(b"ID3")
        return []

    async def fake_probe(path):
        return {"duration": 2.4}

    monkeypatch.setattr(tts, "_openai", bad_openai)
    monkeypatch.setattr(tts, "_edge", fake_edge)
    monkeypatch.setattr(media_render, "probe", fake_probe)
    async with SessionLocal() as s:
        set_tenant(s, a["organization"]["id"])
        res = await tts.synthesize(s, "Hello there", tmp_path / "vo.mp3")
        await s.commit()
    assert res.provider == "edge_tts" and res.voice == tts.DEFAULT_VOICE
    u = (await client.get("/integrations/usage?days=0", headers=auth(a))).json()
    rows = {(m["provider"], m["kind"]): m for m in u["models"]}
    assert rows[("openai_tts", "tts")]["failures"] >= 1
    assert rows[("edge_tts", "tts")]["units"] >= 2
    await client.delete(f"/integrations/providers/{vo}", headers=auth(a))


async def test_more_providers_list_their_models(client, a, monkeypatch):
    calls = []

    async def fake_get(url, headers=None, params=None):
        calls.append(url)
        if "huggingface.co/api/models" in url:
            assert params["pipeline_tag"] == "text-to-image"
            return [{"id": "black-forest-labs/FLUX.1-schnell"}, {"id": "stabilityai/stable-diffusion-xl-base-1.0"}]
        if url.endswith("/collections/text-to-video"):
            return {"models": [{"owner": "minimax", "name": "video-01"}, {"owner": "kwaivgi", "name": "kling-v2.1"}]}
        raise AssertionError(url)

    monkeypatch.setattr(discovery, "_get_json", fake_get)
    ids = {}
    for prov in ("hf_images", "replicate_video", "stability"):
        r = await client.post("/integrations/providers", headers=auth(a), json={"provider": prov, "secrets": {"api_key": "k"}})
        ids[prov] = r.json()["id"]
    hf = (await client.get(f"/integrations/providers/{ids['hf_images']}/models", headers=auth(a))).json()
    assert hf["source"] == "live" and "black-forest-labs/FLUX.1-schnell" in hf["models"]
    rep = (await client.get(f"/integrations/providers/{ids['replicate_video']}/models", headers=auth(a))).json()
    assert rep["models"] == ["kwaivgi/kling-v2.1", "minimax/video-01"] and rep["types"]["minimax/video-01"] == "video"
    st = (await client.get(f"/integrations/providers/{ids['stability']}/models", headers=auth(a))).json()
    assert "sd3.5-large" in st["models"]
    for pid in ids.values():
        await client.delete(f"/integrations/providers/{pid}", headers=auth(a))


async def test_timeouts_get_a_readable_reason():
    import httpx

    e = httpx.ReadTimeout("")
    assert "took too long" in health.describe(e)
    v = health.classify(e, local=True)
    assert v.state == "cooldown" and v.reason.startswith("Took too long")


async def test_pricing_rules():
    from app.services.llm import pricing

    paid = pricing.cost("openai", {}, "gpt-4o-mini", "chat", 1_000_000, 1_000_000)
    assert paid.source == "list" and paid.cost > 0 and paid.cost == paid.list_value
    free = pricing.cost("gemini", {}, "gemini-2.5-flash", "chat", 1_000_000, 0)
    assert free.source == "free" and free.cost == 0 and free.list_value > 0
    assert pricing.cost("gemini", {"billing": "paid"}, "gemini-2.5-flash", "chat", 1_000_000, 0).cost == free.list_value
    mine = pricing.cost("openai", {"prices": {"*": [2, 4]}}, "gpt-4o-mini", "chat", 1_000_000, 500_000)
    assert mine.source == "custom" and mine.cost == 4.0
    assert pricing.cost("ollama", {}, "llama3.1:8b", "chat", 10**6, 10**6).source == "local"
    assert pricing.cost("openrouter", {}, "meta-llama/llama-3.3-70b-instruct:free", "chat", 10**6, 10**6).cost == 0
    assert pricing.cost("openai", {}, "no-such-model-xyz", "chat", 10, 10).source == "unknown"
    assert pricing.cost("openai_tts", {}, "tts-1", "tts", units=60).cost > 0
    assert pricing.cost("openai_images", {"prices": {"image": 0.05}}, "x", "image", units=2).cost == 0.1
    assert pricing.default_billing("groq") == "free" and pricing.default_billing("anthropic") == "paid"
