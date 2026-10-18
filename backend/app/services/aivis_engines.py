"""Answer engines for AI search visibility: ask one prompt, get the answer text and the sources it cited.

Engines come from the text-model integrations the organization already has (Settings -> Integrations).
Web-grounded engines use each provider's own search feature:

  perplexity  sonar models search the web on every request; `citations` / `search_results` list the sources
  openai      Responses API with the `web_search` tool; sources are `url_citation` annotations
  xai         Responses API with the `web_search` tool (same shape as OpenAI, plus a top-level `citations` list)
  gemini      native generateContent with the `google_search` grounding tool; sources in `groundingMetadata`
  anthropic   Messages API with the `web_search_20250305` server tool; sources are text-block citations

Any other configured text model can run as a "plain LLM" engine (no web search: what the model remembers).
The free "Web search" engine needs no key at all: it searches the web with the configured search providers
(DuckDuckGo / SearXNG work without a key) and, when any text model is set up, has that model answer from the
results; otherwise the results themselves are the answer. It approximates what web-grounded assistants cite.

Everything paid is optional. Automatic engine choice uses only free engines (free-tier, local or no-key);
paid engines run only when the user picks them in settings. Each call is one request with a short answer and is
recorded in AI usage under the task "aivis".
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict, dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProviderConfig
from app.services.llm import client as llm
from app.services import search as search_svc
from app.services.llm import health, pricing
from app.services.llm import router as llm_router
from app.services.llm.catalog import BY_ID

TASK = "aivis"
TIMEOUT = httpx.Timeout(90.0, connect=10.0)
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests swap in an httpx.MockTransport
MAX_TOKENS = 700
MAX_ENGINES = 6

WEB_PROVIDERS = ("perplexity", "openai", "gemini", "xai", "anthropic")
ALWAYS_WEB = ("perplexity",)
BRAND = {"perplexity": "Perplexity", "openai": "ChatGPT", "gemini": "Gemini", "xai": "Grok", "anthropic": "Claude"}
SUGGESTED_MODEL = {"perplexity": "sonar"}
WEBSEARCH = "websearch"  # provider id of the free web-search engine
WEBSEARCH_MODEL = "web results"
# Paid engines worth adding for richer data (shown as hints when not configured).
PAID_OPTIONAL = [
    {"provider": "perplexity", "label": "Perplexity", "note": "Answers with live web search and lists its sources."},
    {"provider": "openai", "label": "ChatGPT (OpenAI)", "note": "ChatGPT with web search, the most used assistant."},
    {"provider": "anthropic", "label": "Claude (Anthropic)", "note": "Claude with web search and cited sources."},
    {"provider": "xai", "label": "Grok (xAI)", "note": "Grok with web search."},
]

SYSTEM = ("You are a helpful AI assistant answering a question from a business buyer. Answer the way you normally "
          "would, in under 200 words. When the question asks for options or recommendations, name the specific "
          "companies or products you would suggest.")


class EngineError(Exception):
    pass


@dataclass
class Engine:
    key: str
    provider_id: str
    provider: str
    model: str
    web: bool
    label: str
    cost: str = "free"  # free | paid

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EngineAnswer:
    text: str
    citations: list[dict] = field(default_factory=list)  # [{url, title}]
    input_tokens: int = 0
    output_tokens: int = 0
    searches: int = 0
    latency_ms: int = 0


def engine_key(provider_id: str, model: str, web: bool) -> str:
    return f"{provider_id}:{model}:{'web' if web else 'plain'}"


def engine_label(cfg_name: str, provider: str, model: str, web: bool) -> str:
    base = BRAND.get(provider) or cfg_name
    return f"{base} · {model}" + (" (web)" if web and provider not in ALWAYS_WEB else "" if web else " (no web)")


def supports_web(provider: str) -> bool:
    return provider in WEB_PROVIDERS


def cost_of(cfg: ProviderConfig) -> str:
    """"free" for local models and integrations on a free tier (as set on the integration), else "paid"."""
    return "free" if pricing.billing_of(cfg.provider, cfg.settings or {}) == "free" else "paid"


def make_engine(cfg: ProviderConfig, model: str, web: bool) -> Engine:
    web = (web and supports_web(cfg.provider)) or cfg.provider in ALWAYS_WEB
    return Engine(engine_key(str(cfg.id), model, web), str(cfg.id), cfg.provider, model, web,
                  engine_label(cfg.name, cfg.provider, model, web)[:160], cost_of(cfg))


def websearch_engine() -> Engine:
    return Engine(engine_key(WEBSEARCH, WEBSEARCH_MODEL, True), WEBSEARCH, WEBSEARCH, WEBSEARCH_MODEL, True,
                  "Web search (free)", "free")


def _chat_models(cfg: ProviderConfig) -> list[str]:
    s = cfg.settings or {}
    types = s.get("model_types") or {}
    models = [m for m in (s.get("models") or []) if types.get(m, "chat") == "chat"]
    spec = BY_ID.get(cfg.provider)
    if not models and spec:
        models = [m for m in spec.examples if "embed" not in m]
    first = [m for m in (s.get("default_model"), SUGGESTED_MODEL.get(cfg.provider)) if m]
    return list(dict.fromkeys(first + models))[:200]


async def text_providers(session: AsyncSession) -> list[ProviderConfig]:
    rows = (await session.scalars(select(ProviderConfig).where(
        ProviderConfig.kind == "llm", ProviderConfig.enabled.is_(True)).order_by(ProviderConfig.created_at))).all()
    return list(rows)


async def available(session: AsyncSession) -> list[dict]:
    """Every engine the user can pick, grouped by configured integration."""
    out = []
    for cfg in await text_providers(session):
        models = _chat_models(cfg)
        if not models:
            continue
        out.append({
            "provider_id": str(cfg.id), "provider": cfg.provider, "name": cfg.name,
            "brand": BRAND.get(cfg.provider) or cfg.name, "models": models,
            "suggested_model": models[0], "web": supports_web(cfg.provider),
            "always_web": cfg.provider in ALWAYS_WEB, "local": bool(BY_ID.get(cfg.provider) and BY_ID[cfg.provider].local),
            "cost": cost_of(cfg),
        })
    out.append({"provider_id": WEBSEARCH, "provider": WEBSEARCH, "name": "Web search", "brand": "Web search",
                "models": [WEBSEARCH_MODEL], "suggested_model": WEBSEARCH_MODEL, "web": True, "always_web": True,
                "local": False, "cost": "free"})
    return out


async def paid_hints(session: AsyncSession) -> list[dict]:
    """Paid engines the organization hasn't added: "add X for richer data" hints (all optional)."""
    have = {c.provider for c in await text_providers(session)}
    return [h for h in PAID_OPTIONAL if h["provider"] not in have]


async def default_engines(session: AsyncSession) -> list[Engine]:
    """Without a choice in settings: only free engines. Each free integration with a default model (web-grounded
    where the provider supports it, e.g. Gemini's free tier), plus the free web-search engine. Paid engines are
    never picked automatically."""
    engines = []
    for cfg in await text_providers(session):
        model = (cfg.settings or {}).get("default_model")
        if model and cost_of(cfg) == "free":
            engines.append(make_engine(cfg, model, supports_web(cfg.provider)))
    return [*engines[:MAX_ENGINES - 1], websearch_engine()]


async def resolve(session: AsyncSession, configured: list[dict]) -> list[Engine]:
    """Turn saved engine choices into engines whose integration still exists and is enabled."""
    if not configured:
        return await default_engines(session)
    cfgs = {str(c.id): c for c in await text_providers(session)}
    out, seen = [], set()
    for e in configured:
        if str(e.get("provider_id")) == WEBSEARCH:
            eng = websearch_engine()
            if eng.key not in seen:
                seen.add(eng.key)
                out.append(eng)
            continue
        cfg = cfgs.get(str(e.get("provider_id")))
        model = str(e.get("model") or "").strip()
        if cfg is None or not model:
            continue
        eng = make_engine(cfg, model, bool(e.get("web")))
        if eng.key not in seen:
            seen.add(eng.key)
            out.append(eng)
    return out[:MAX_ENGINES]


# ------------------------------------------------------------------ HTTP
def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT, transport=TRANSPORT)


async def _post(url: str, body: dict, headers: dict, *, params: dict | None = None) -> dict:
    async with _client() as http:
        r = await http.post(url, json=body, headers=headers, params=params)
    if r.status_code >= 400:
        raise llm._err(r)
    try:
        return r.json()
    except ValueError:
        raise llm.ProviderError(f"Unexpected response: {r.text[:200]}")


def _bearer(c: llm.Conn) -> dict:
    return llm._headers(c)


def _anthropic_headers(c: llm.Conn) -> dict:
    return {"x-api-key": c.api_key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"}


def _cite(url: str | None, title: str | None = None) -> dict | None:
    if not url or not str(url).startswith(("http://", "https://")):
        return None
    return {"url": str(url)[:1000], "title": (str(title)[:300] if title else None)}


def _dedupe(cites: list[dict | None]) -> list[dict]:
    out, seen = [], set()
    for c in cites:
        if c and c["url"] not in seen:
            seen.add(c["url"])
            out.append(c)
    return out[:40]


async def _openai_chat(c: llm.Conn, model: str, prompt: str, extra: dict | None = None) -> EngineAnswer:
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS, "temperature": 0.3, **(extra or {})}
    data = await _post(f"{c.base_url.rstrip('/')}/chat/completions", body, _bearer(c))
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise llm.ProviderError(f"Unexpected response: {str(data)[:200]}")
    u = data.get("usage") or {}
    cites = [_cite(x) if isinstance(x, str) else _cite(x.get("url"), x.get("title")) for x in data.get("citations") or []]
    cites += [_cite(x.get("url"), x.get("title")) for x in data.get("search_results") or [] if isinstance(x, dict)]
    for a in msg.get("annotations") or []:  # OpenAI search-preview models
        uc = a.get("url_citation") if isinstance(a, dict) else None
        if uc:
            cites.append(_cite(uc.get("url"), uc.get("title")))
    return EngineAnswer(msg.get("content") or "", _dedupe(cites), u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                        1 if c.provider in ALWAYS_WEB else 0)


async def _responses(c: llm.Conn, model: str, prompt: str) -> EngineAnswer:
    """OpenAI / xAI Responses API with the web_search tool."""
    body = {"model": model, "instructions": SYSTEM, "input": prompt, "tools": [{"type": "web_search"}],
            "max_output_tokens": MAX_TOKENS}
    data = await _post(f"{c.base_url.rstrip('/')}/responses", body, _bearer(c))
    texts, cites, searches = [], [], 0
    for item in data.get("output") or []:
        if item.get("type") == "web_search_call":
            searches += 1
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") in ("output_text", "text"):
                texts.append(part.get("text") or "")
                for a in part.get("annotations") or []:
                    if a.get("type") == "url_citation":
                        cites.append(_cite(a.get("url"), a.get("title")))
    if not texts and isinstance(data.get("output_text"), str):
        texts.append(data["output_text"])
    cites += [_cite(x) if isinstance(x, str) else _cite(x.get("url"), x.get("title")) for x in data.get("citations") or []]
    u = data.get("usage") or {}
    return EngineAnswer("\n".join(t for t in texts if t).strip(), _dedupe(cites), u.get("input_tokens", 0),
                        u.get("output_tokens", 0), searches)


def _gemini_native_base(base: str) -> str:
    base = base.rstrip("/")
    return base[: -len("/openai")] if base.endswith("/openai") else base


async def _gemini(c: llm.Conn, model: str, prompt: str) -> EngineAnswer:
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": MAX_TOKENS, "temperature": 0.3}}
    url = f"{_gemini_native_base(c.base_url)}/models/{model.removeprefix('models/')}:generateContent"
    data = await _post(url, body, {"x-goog-api-key": c.api_key or "", "Content-Type": "application/json"})
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [] if isinstance(p, dict))
    gm = cand.get("groundingMetadata") or {}
    cites = []
    for ch in gm.get("groundingChunks") or []:
        web = ch.get("web") or {}
        cites.append(_cite(web.get("uri"), web.get("title")))
    u = data.get("usageMetadata") or {}
    return EngineAnswer(text.strip(), _dedupe(cites), u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0),
                        len(gm.get("webSearchQueries") or []))


async def _anthropic(c: llm.Conn, model: str, prompt: str, web: bool) -> EngineAnswer:
    body = {"model": model, "max_tokens": MAX_TOKENS, "temperature": 0.3, "system": SYSTEM,
            "messages": [{"role": "user", "content": prompt}]}
    if web:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
    data = await _post(f"{c.base_url.rstrip('/')}/v1/messages", body, _anthropic_headers(c))
    blocks = data.get("content") or []
    # Text written before the last search ("I'll look that up") isn't part of the answer.
    last_tool = max((i for i, b in enumerate(blocks) if b.get("type") == "web_search_tool_result"), default=-1)
    texts, cited, results = [], [], []
    for i, b in enumerate(blocks):
        if b.get("type") == "text" and i > last_tool:
            texts.append(b.get("text") or "")
            for ct in b.get("citations") or []:
                cited.append(_cite(ct.get("url"), ct.get("title")))
        elif b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            results += [_cite(r.get("url"), r.get("title")) for r in b["content"] if isinstance(r, dict)]
    u = data.get("usage") or {}
    searches = int(((u.get("server_tool_use") or {}).get("web_search_requests")) or 0)
    return EngineAnswer("".join(texts).strip(), _dedupe(cited or results), u.get("input_tokens", 0),
                        u.get("output_tokens", 0), searches)


async def _call(c: llm.Conn, eng: Engine, prompt: str) -> EngineAnswer:
    if c.style == "anthropic":
        return await _anthropic(c, eng.model, prompt, eng.web)
    if eng.web and eng.provider == "gemini":
        return await _gemini(c, eng.model, prompt)
    if eng.web and eng.provider in ("openai", "xai"):
        return await _responses(c, eng.model, prompt)
    return await _openai_chat(c, eng.model, prompt)


async def ask(cfg: ProviderConfig, eng: Engine, prompt: str) -> EngineAnswer:
    """One request to one engine. Raises EngineError with a plain-language reason."""
    c = llm_router.conn_from(cfg)
    t0 = time.perf_counter()
    try:
        if c.local:
            async with llm.LOCAL_GPU_LOCK:
                r = await _call(c, eng, prompt)
        else:
            r = await _call(c, eng, prompt)
    except httpx.TimeoutException as e:
        raise EngineError("The engine took too long to answer.") from e
    except llm.ProviderError as e:
        raise EngineError(health.describe(e)[:400]) from e
    except httpx.HTTPError as e:
        raise EngineError(f"Could not reach {cfg.name}: {type(e).__name__}") from e
    r.latency_ms = int((time.perf_counter() - t0) * 1000)
    if not r.text.strip():
        raise EngineError("The engine returned an empty answer.")
    return r


def log_usage(session: AsyncSession, cfg: ProviderConfig, eng: Engine, r: EngineAnswer | None, error: str | None) -> None:
    """Record the call in AI usage (costs per token from the price list; web searches counted as units)."""
    if r is None:
        llm_router._log(session, cfg, eng.model, TASK, "chat", ok=False, error=(error or "")[:1000])
        return
    llm_router._log(session, cfg, eng.model, TASK, "chat", tin=r.input_tokens, tout=r.output_tokens, units=r.searches,
                    latency=r.latency_ms)


async def ask_many(session: AsyncSession, cfgs: dict[str, ProviderConfig], engines: list[Engine], prompt: str,
                   ) -> list[tuple[Engine, EngineAnswer | None, str | None]]:
    """Ask every engine the same prompt in parallel (HTTP only), then log usage and model health."""
    async def one(eng: Engine, cfg: ProviderConfig | None, ok: bool):
        if cfg is None:
            return eng, None, "This integration was removed or turned off."
        if not ok:
            return eng, None, f"{cfg.name}: monthly budget reached."
        try:
            return eng, await ask(cfg, eng, prompt), None
        except EngineError as e:
            return eng, None, str(e)

    # The web-search engine and budget checks use the session, so they run one at a time before the parallel calls.
    jobs, early = [], []
    for e in engines:
        if e.provider == WEBSEARCH:
            try:
                early.append((e, await ask_websearch(session, prompt), None))
            except EngineError as err:
                early.append((e, None, str(err)))
            continue
        cfg = cfgs.get(e.provider_id)
        ok = cfg is not None and not await llm_router.over_budget(session, cfg)
        jobs.append(one(e, cfg, ok))
    results = await asyncio.gather(*jobs)
    for eng, r, err in results:
        cfg = cfgs.get(eng.provider_id)
        if cfg is None or (err or "").endswith("monthly budget reached."):
            continue
        log_usage(session, cfg, eng, r, err)
        if r is not None:
            await health.record_success(cfg, eng.model)
        else:
            await health.record_failure(cfg, eng.model, EngineError(err or "failed"))
    await session.flush()
    order = {e.key: i for i, e in enumerate(engines)}
    return sorted([*early, *results], key=lambda x: order.get(x[0].key, 0))


URL_RE = re.compile(r"https?://[^\s<>\"'`)\]]+", re.I)


def urls_in_text(text: str) -> list[dict]:
    out = []
    for m in URL_RE.findall(text or ""):
        out.append(_cite(m.rstrip(".,;:!?*")))
    return _dedupe(out)


# ------------------------------------------------------------------ free web-search engine
GROUNDED_SYSTEM = ("You are a helpful AI assistant answering a business buyer's question using only the web search "
                   "results given. Answer in under 200 words. When the question asks for options, name the specific "
                   "companies or products the results support, and cite the result numbers like [1].")


async def ask_websearch(session: AsyncSession, prompt: str) -> EngineAnswer:
    """Free approximation of a web-grounded assistant: search, then let any configured text model answer from the
    results (usage recorded by the router under "aivis"). Without a text model the results are the answer."""
    t0 = time.perf_counter()
    try:
        rows, _ = await search_svc.search_all(session, prompt, n=8)
    except search_svc.SearchError as e:
        raise EngineError(str(e)[:300]) from e
    rows = [r for r in rows if r.get("url")][:8]
    if not rows:
        raise EngineError("Web search found nothing for this prompt.")
    cites = _dedupe([_cite(r["url"], r.get("title")) for r in rows])
    listing = "\n".join(f"[{i + 1}] {r.get('title') or ''} — {r.get('snippet') or ''} ({r['url']})" for i, r in enumerate(rows))
    try:
        res = await llm_router.complete(session, TASK, f"Question: {prompt}\n\nWeb search results:\n{listing}",
                                        system=GROUNDED_SYSTEM, temperature=0.3, max_tokens=MAX_TOKENS)
        text = res.text.strip()
    except (llm_router.NoProviderError, llm_router.AllProvidersFailed):
        text = ""
    if not text:
        text = "Top web results:\n" + "\n".join(
            f"{i + 1}. {r.get('title') or r['url']} — {r.get('snippet') or ''}" for i, r in enumerate(rows))
    return EngineAnswer(text, cites, 0, 0, 1, int((time.perf_counter() - t0) * 1000))
