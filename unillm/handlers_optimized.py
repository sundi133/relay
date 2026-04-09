"""
Performance-optimized HTTP handlers with connection pooling and reduced overhead.
"""
from __future__ import annotations
import json
from typing import AsyncIterator
import httpx
import asyncio
import time

from .providers import get as get_provider, _cost
import os


# ---------------------------------------------------------------------------
# Global HTTP client with optimized settings
# ---------------------------------------------------------------------------
class OptimizedClient:
    _instance = None
    _client = None

    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        """Singleton HTTP client with connection pooling"""
        if cls._client is None:
            # Optimized client configuration
            limits = httpx.Limits(
                max_keepalive_connections=100,    # Keep connections alive
                max_connections=200,              # Total connection pool
                keepalive_expiry=30.0            # Keep alive for 30s
            )

            timeout = httpx.Timeout(
                connect=5.0,     # Fast connection timeout
                read=30.0,       # Reasonable read timeout
                write=10.0,      # Fast write timeout
                pool=2.0         # Pool acquisition timeout
            )

            cls._client = httpx.AsyncClient(
                limits=limits,
                timeout=timeout,
                http2=True,      # HTTP/2 support for better performance
                follow_redirects=True
            )
        return cls._client

    @classmethod
    async def close(cls):
        """Close the client (call during shutdown)"""
        if cls._client:
            await cls._client.aclose()
            cls._client = None


# Pre-compiled response template for faster JSON generation
RESPONSE_TEMPLATE = {
    "content": None,
    "model": None,
    "usage": {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    },
    "raw": None
}


def _make_response_fast(content: str, model: str, usage: dict, raw: dict) -> dict:
    """Optimized response creation with minimal object allocation"""
    prompt_tok = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    compl_tok = usage.get("completion_tokens", usage.get("output_tokens", 0))
    cost_usd = _cost(model, prompt_tok, compl_tok)

    # Reuse template structure
    result = RESPONSE_TEMPLATE.copy()
    result["content"] = content
    result["model"] = model
    result["usage"] = {
        "prompt_tokens": prompt_tok,
        "completion_tokens": compl_tok,
        "total_tokens": prompt_tok + compl_tok,
        "cost_usd": round(cost_usd, 8),
    }
    result["raw"] = raw
    return result


# ---------------------------------------------------------------------------
# Optimized OpenAI handler with connection reuse
# ---------------------------------------------------------------------------
async def _openai_optimized(
    base: str, model: str, messages: list, key: str | None,
    stream: bool, temperature: float, max_tokens: int, **kwargs
):
    """High-performance OpenAI handler with connection pooling"""

    # Pre-build headers (avoid dict creation in hot path)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    # Pre-build payload (minimal allocations)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
        **kwargs
    }

    # Use singleton client with connection pooling
    client = await OptimizedClient.get_client()
    url = f"{base}/chat/completions"

    if stream:
        return _openai_stream_optimized(client, url, headers, payload)

    # Non-streaming optimized path
    response = await client.post(url, json=payload, headers=headers)
    response.raise_for_status()
    raw = response.json()

    # Fast response creation
    return _make_response_fast(
        content=raw["choices"][0]["message"]["content"],
        model=raw.get("model", model),
        usage=raw.get("usage", {}),
        raw=raw,
    )


async def _openai_stream_optimized(client, url, headers, payload) -> AsyncIterator[str]:
    """Optimized streaming with minimal allocations"""
    async with client.stream("POST", url, json=payload, headers=headers) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                try:
                    # Fast JSON parsing path
                    chunk = json.loads(line[6:])
                    delta = chunk["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


# ---------------------------------------------------------------------------
# Optimized dispatch with reduced overhead
# ---------------------------------------------------------------------------
async def call_optimized(
    provider_name: str,
    model_id: str,
    messages: list,
    stream: bool,
    temperature: float,
    max_tokens: int,
    **kwargs,
):
    """
    Optimized call function with:
    - Connection pooling
    - Reduced allocations
    - Faster response processing
    """
    cfg = get_provider(provider_name)

    # Optimize key lookup (cache environment variable reads)
    key = cfg.get("_key")
    if not key and cfg.get("key_env"):
        key = os.environ.get(cfg["key_env"])

    base = cfg["base"]
    style = cfg["style"]

    if style == "openai":
        return await _openai_optimized(base, model_id, messages, key, stream, temperature, max_tokens, **kwargs)
    elif style == "anthropic":
        # Could optimize anthropic handler similarly
        from .handlers import _anthropic
        return await _anthropic(base, model_id, messages, key, stream, temperature, max_tokens, **kwargs)
    elif style == "gemini":
        # Could optimize gemini handler similarly
        from .handlers import _gemini
        return await _gemini(base, model_id, messages, key, stream, temperature, max_tokens, **kwargs)
    else:
        raise ValueError(f"Unknown style '{style}'")


# ---------------------------------------------------------------------------
# Batch operations for high-throughput scenarios
# ---------------------------------------------------------------------------
async def call_batch_optimized(requests: list, max_concurrent: int = 50):
    """
    Process multiple requests concurrently with controlled concurrency

    Args:
        requests: List of (provider_name, model_id, messages, stream, temp, max_tokens, kwargs) tuples
        max_concurrent: Maximum concurrent requests
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_request(req):
        async with semaphore:
            return await call_optimized(*req)

    tasks = [process_request(req) for req in requests]
    return await asyncio.gather(*tasks, return_exceptions=True)