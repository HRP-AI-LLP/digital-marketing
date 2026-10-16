"""Low-level clients for text models: OpenAI-compatible APIs and Anthropic."""

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.services.llm.catalog import BY_ID

# Local GPU models run one request at a time (8 GB VRAM).
LOCAL_GPU_LOCK = asyncio.Semaphore(1)
TIMEOUT = httpx.Timeout(180.0, connect=10.0)


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class ChatResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model: str


@dataclass
class Conn:
    """Resolved connection details for one configured provider."""

    provider: str
    base_url: str
    api_key: str | None
    extra: dict

    @property
    def style(self) -> str:
        spec = BY_ID.get(self.provider)
        return spec.style if spec else "openai"

    @property
    def local(self) -> bool:
        spec = BY_ID.get(self.provider)
        return bool(spec and spec.local)


def _err(resp: httpx.Response) -> ProviderError:
    try:
        body = resp.json()
        msg = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body.get("error") or body
    except Exception:  # noqa: BLE001
        msg = resp.text[:300]
    retry = resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500
    return ProviderError(f"{resp.status_code}: {msg}", resp.status_code, retryable=retry)


def _headers(c: Conn) -> dict:
    h = {"Content-Type": "application/json"}
    if c.api_key:
        h["Authorization"] = f"Bearer {c.api_key}"
    if c.provider == "openrouter":
        h["HTTP-Referer"] = "https://hrpai.in"
        h["X-Title"] = "HRP Digital Marketing Studio"
    return h


async def chat(
    c: Conn, model: str, messages: list[dict], *, system: str | None = None, json_mode: bool = False,
    temperature: float = 0.7, max_tokens: int = 1500,
) -> ChatResult:
    if c.local:
        async with LOCAL_GPU_LOCK:
            return await _chat(c, model, messages, system, json_mode, temperature, max_tokens)
    return await _chat(c, model, messages, system, json_mode, temperature, max_tokens)


async def _chat(c, model, messages, system, json_mode, temperature, max_tokens) -> ChatResult:
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        if c.style == "anthropic":
            body = {"model": model, "max_tokens": max_tokens, "temperature": temperature, "messages": messages}
            sys = system or ""
            if json_mode:
                sys = (sys + "\n\nRespond with a single valid JSON object only, no prose.").strip()
            if sys:
                body["system"] = sys
            r = await http.post(
                f"{c.base_url.rstrip('/')}/v1/messages", json=body,
                headers={"x-api-key": c.api_key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"},
            )
            if r.status_code >= 400:
                raise _err(r)
            data = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            u = data.get("usage", {})
            return ChatResult(text, u.get("input_tokens", 0), u.get("output_tokens", 0),
                              int((time.perf_counter() - t0) * 1000), data.get("model", model))

        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": model, "messages": msgs, "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        url = f"{c.base_url.rstrip('/')}/chat/completions"
        r = await http.post(url, json=body, headers=_headers(c))
        if r.status_code == 400 and json_mode:  # some models reject response_format; retry without it
            body.pop("response_format", None)
            body["messages"] = msgs[:-1] + [{**msgs[-1], "content": msgs[-1]["content"] + "\n\nReturn only valid JSON."}]
            r = await http.post(url, json=body, headers=_headers(c))
        if r.status_code >= 400:
            raise _err(r)
        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            raise ProviderError(f"Unexpected response: {str(data)[:300]}")
        u = data.get("usage") or {}
        return ChatResult(text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                          int((time.perf_counter() - t0) * 1000), data.get("model", model))


async def embed(c: Conn, model: str, texts: list[str]) -> tuple[list[list[float]], int]:
    if c.style == "anthropic":
        raise ProviderError("Anthropic has no embeddings API; route 'embed' to another provider.", retryable=False)

    async def go():
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            r = await http.post(f"{c.base_url.rstrip('/')}/embeddings", json={"model": model, "input": texts}, headers=_headers(c))
            if r.status_code >= 400:
                raise _err(r)
            data = r.json()
            vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d.get("index", 0))]
            return vecs, (data.get("usage") or {}).get("prompt_tokens", 0)

    if c.local:
        async with LOCAL_GPU_LOCK:
            return await go()
    return await go()


async def list_models(c: Conn) -> list[str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as http:
        if c.style == "anthropic":
            r = await http.get(f"{c.base_url.rstrip('/')}/v1/models?limit=100",
                               headers={"x-api-key": c.api_key or "", "anthropic-version": "2023-06-01"})
        else:
            r = await http.get(f"{c.base_url.rstrip('/')}/models", headers=_headers(c))
        if r.status_code >= 400:
            raise _err(r)
        data = r.json()
        items = data.get("data", data.get("models", []))
        ids = []
        for m in items:
            mid = m.get("id") or m.get("name") if isinstance(m, dict) else m
            if mid:
                ids.append(mid.removeprefix("models/"))
        return sorted(set(ids))
