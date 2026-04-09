"""
HTTP handlers for each provider style.
"""
from __future__ import annotations
import json
from typing import AsyncIterator
import httpx

from .providers import get as get_provider, _cost
import os


# ---------------------------------------------------------------------------
# Unified response dataclass (dict-compatible)
# ---------------------------------------------------------------------------
class Response(dict):
    """
    A dict subclass so callers can do either:
        r["content"]
        r.content
    """
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def _make_response(content: str, model: str, usage: dict, raw: dict) -> Response:
    prompt_tok = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    compl_tok  = usage.get("completion_tokens", usage.get("output_tokens", 0))
    cost_usd   = _cost(model, prompt_tok, compl_tok)
    return Response(
        content=content,
        model=model,
        usage=dict(
            prompt_tokens=prompt_tok,
            completion_tokens=compl_tok,
            total_tokens=prompt_tok + compl_tok,
            cost_usd=round(cost_usd, 8),
        ),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible  (covers 90 % of providers)
# ---------------------------------------------------------------------------
async def _openai(
    base: str, model: str, messages: list, key: str | None,
    stream: bool, temperature: float, max_tokens: int, **kwargs
):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload = dict(
        model=model, messages=messages,
        temperature=temperature, max_tokens=max_tokens,
        stream=stream, **kwargs
    )

    async with httpx.AsyncClient(timeout=120) as client:
        if stream:
            return _openai_stream(client, f"{base}/chat/completions", headers, payload)
        r = await client.post(f"{base}/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        raw = r.json()
        return _make_response(
            content=raw["choices"][0]["message"]["content"],
            model=raw.get("model", model),
            usage=raw.get("usage", {}),
            raw=raw,
        )


async def _openai_stream(client, url, headers, payload) -> AsyncIterator[str]:
    async with client.stream("POST", url, json=payload, headers=headers) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
async def _anthropic(
    base: str, model: str, messages: list, key: str,
    stream: bool, temperature: float, max_tokens: int, **kwargs
):
    system = next((m["content"] for m in messages if m["role"] == "system"), None)
    user_msgs = [m for m in messages if m["role"] != "system"]

    payload = dict(model=model, messages=user_msgs,
                   max_tokens=max_tokens, temperature=temperature, **kwargs)
    if system:
        payload["system"] = system

    headers = {
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    if key:
        headers["x-api-key"] = key

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{base}/messages", json=payload, headers=headers)
        r.raise_for_status()
        raw = r.json()
        return _make_response(
            content=raw["content"][0]["text"],
            model=raw.get("model", model),
            usage=raw.get("usage", {}),
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
async def _gemini(
    base: str, model: str, messages: list, key: str,
    stream: bool, temperature: float, max_tokens: int, **kwargs
):
    contents = [
        {
            "role": "user" if m["role"] == "user" else "model",
            "parts": [{"text": m["content"]}],
        }
        for m in messages if m["role"] != "system"
    ]
    system_text = next((m["content"] for m in messages if m["role"] == "system"), None)

    payload: dict = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    url = f"{base}/models/{model}:generateContent?key={key}"
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        raw = r.json()
        return _make_response(
            content=raw["candidates"][0]["content"]["parts"][0]["text"],
            model=model,
            usage=raw.get("usageMetadata", {}),
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
async def call(
    provider_name: str,
    model_id: str,
    messages: list,
    stream: bool,
    temperature: float,
    max_tokens: int,
    **kwargs,
):
    cfg = get_provider(provider_name)
    key = cfg.get("_key") or (os.environ.get(cfg["key_env"]) if cfg.get("key_env") else None)
    base = cfg["base"]
    style = cfg["style"]

    if style == "openai":
        return await _openai(base, model_id, messages, key, stream, temperature, max_tokens, **kwargs)
    elif style == "anthropic":
        return await _anthropic(base, model_id, messages, key, stream, temperature, max_tokens, **kwargs)
    elif style == "gemini":
        return await _gemini(base, model_id, messages, key, stream, temperature, max_tokens, **kwargs)
    else:
        raise ValueError(f"Unknown style '{style}'")
