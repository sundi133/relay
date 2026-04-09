"""
Fallback router.

Tries each model in a priority list until one succeeds.
Useful for:
  - Redundancy: primary cloud → backup cloud → local
  - Cost optimization: cheap model first, expensive if it fails
  - Rate-limit handling: round-robin across keys/regions

Example:
    from unillm.fallback import FallbackRouter

    router = FallbackRouter([
        "deepseek/deepseek-chat",       # cheapest first
        "qwen/qwen-plus",               # mid tier
        "openai/gpt-4o-mini",           # reliable fallback
        "ollama/llama3.2",              # local last resort
    ])

    resp = await router.completion(messages)
    print(resp.content, resp._used_model)
"""
from __future__ import annotations
import logging
from typing import AsyncIterator

import httpx

from .handlers import call
from .retry import with_retry
from .tracker import tracker

log = logging.getLogger("unillm.fallback")


class FallbackRouter:
    def __init__(
        self,
        models: list[str],
        *,
        max_attempts_per_model: int = 2,
        skip_on: tuple[type, ...] = (httpx.HTTPStatusError,),
    ) -> None:
        """
        Args:
            models:                  Ordered list of "provider/model" strings.
            max_attempts_per_model:  Retries per model before moving to next.
            skip_on:                 Exception types that immediately try the next model.
        """
        if not models:
            raise ValueError("FallbackRouter requires at least one model.")
        self.models = models
        self.max_attempts = max_attempts_per_model
        self.skip_on = skip_on

    async def completion(
        self,
        messages: list[dict],
        *,
        stream: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ):
        """
        Try each model in order, returning the first successful response.
        Attaches `._used_model` to the response so callers know which won.
        """
        import time

        last_exc: Exception | None = None

        for model_str in self.models:
            provider, model_id = model_str.split("/", 1)

            async def _attempt():
                return await call(
                    provider, model_id, messages,
                    stream=stream,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )

            t0 = time.monotonic()
            try:
                resp = await with_retry(_attempt, max_attempts=self.max_attempts)
                latency_ms = (time.monotonic() - t0) * 1000
                if hasattr(resp, "usage"):
                    tracker.record(f"{provider}/{model_id}", resp.usage, latency_ms)
                resp["_used_model"] = model_str
                return resp

            except Exception as exc:
                latency_ms = (time.monotonic() - t0) * 1000
                tracker.record(f"{provider}/{model_id}", {}, latency_ms, error=True)
                last_exc = exc
                log.warning(
                    "unillm fallback: '%s' failed (%s: %s). Trying next model…",
                    model_str, type(exc).__name__, exc,
                )
                continue

        raise RuntimeError(
            f"All models in FallbackRouter failed. Last error: {last_exc}"
        ) from last_exc
