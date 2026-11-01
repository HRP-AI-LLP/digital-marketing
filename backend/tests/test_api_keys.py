"""Several API keys (accounts) on one connection, with automatic switching when one runs out."""

from app.services.llm import client as llm
from tests.conftest import auth
from tests.test_ai_models import a, no_discovery  # noqa: F401 - fixtures


async def test_backup_keys_take_over_and_rest(client, a, monkeypatch, no_discovery):
    p = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "groq", "name": "Groq accounts",
         "secrets": {"api_key": "key-one-aaaa"}, "settings": {"default_model": "good"}})).json()
    pid = p["id"]
    assert p["key_count"] == 1
    r = await client.post(f"/integrations/providers/{pid}/keys", headers=auth(a), json={"key": "key-two-bbbb"})
    assert r.status_code == 201, r.text
    assert [k["label"] for k in r.json()["keys"]] == ["Main key", "Backup key 1"]
    assert (await client.post(f"/integrations/providers/{pid}/keys", headers=auth(a), json={"key": "key-two-bbbb"})).status_code == 409
    listed = (await client.get("/integrations/providers", headers=auth(a))).json()
    me = next(x for x in listed if x["id"] == pid)
    assert me["key_count"] == 2 and "extra_keys" not in me["fields"] and "key-two" not in str(me)

    used: list[str] = []

    async def fake_chat(conn, model, messages, **kw):
        used.append(conn.api_key)
        if conn.api_key == "key-one-aaaa":
            raise llm.ProviderError("402: Your credit balance is too low", 402)
        return llm.ChatResult("ok", 1, 1, 5, model)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = await client.post("/integrations/playground", headers=auth(a), json={"task": "write", "prompt": "hi"})
    assert r.status_code == 200 and used == ["key-one-aaaa", "key-two-bbbb"]
    ks = (await client.get(f"/integrations/providers/{pid}/keys", headers=auth(a))).json()["keys"]
    assert ks[0]["state"] == "billing" and ks[1]["state"] == "ok"
    # the model itself is not rested, and the next call goes straight to the working key
    st = (await client.get("/integrations/model-status", headers=auth(a))).json()["statuses"]
    assert not any(x["model"].startswith("key:") for x in st)
    used.clear()
    await client.post("/integrations/playground", headers=auth(a), json={"task": "write", "prompt": "hi"})
    assert used == ["key-two-bbbb"]

    # topped up: try it again now; make it the main key; remove it
    fp1 = ks[0]["fingerprint"]
    ks = (await client.post(f"/integrations/providers/{pid}/keys/{fp1}/reset", headers=auth(a))).json()["keys"]
    assert ks[0]["state"] == "ok"
    ks = (await client.post(f"/integrations/providers/{pid}/keys/{ks[1]['fingerprint']}/main", headers=auth(a))).json()["keys"]
    assert ks[0]["masked"] == "…bbbb"
    ks = (await client.delete(f"/integrations/providers/{pid}/keys/{fp1}", headers=auth(a))).json()["keys"]
    assert len(ks) == 1
    assert (await client.delete(f"/integrations/providers/{pid}/keys/{ks[0]['fingerprint']}", headers=auth(a))).status_code == 400
    await client.delete(f"/integrations/providers/{pid}", headers=auth(a))


async def test_free_connections_back_up_a_routed_task(client, a, monkeypatch, no_discovery):
    paid = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "openai", "name": "paid",
            "secrets": {"api_key": "sk-x"}, "settings": {"default_model": "bad"}})).json()["id"]
    free = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "sambanova", "name": "free",
            "secrets": {"api_key": "s-y"}, "settings": {"default_model": "good"}})).json()["id"]
    other_paid = (await client.post("/integrations/providers", headers=auth(a), json={"provider": "anthropic", "name": "paid2",
                  "secrets": {"api_key": "a-z"}, "settings": {"default_model": "good"}})).json()["id"]
    await client.put("/integrations/routes", headers=auth(a), json={"routes": {"write": [{"provider_id": paid, "model": "bad"}]}})
    seen: list[str] = []

    async def fake_chat(conn, model, messages, **kw):
        seen.append(conn.provider)
        if model == "bad":
            raise llm.ProviderError("402: insufficient_quota", 402)
        return llm.ChatResult("ok", 1, 1, 5, model)

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = await client.post("/integrations/playground", headers=auth(a), json={"task": "write", "prompt": "hi"})
    assert r.status_code == 200 and seen == ["openai", "sambanova"]  # the unrouted paid one is never used
    await client.put("/integrations/routes", headers=auth(a), json={"routes": {"write": []}})
    for p in (paid, free, other_paid):
        await client.delete(f"/integrations/providers/{p}", headers=auth(a))
