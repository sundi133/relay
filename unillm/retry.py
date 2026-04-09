"""
Retry with exponential backoff + jitter.
Retries on: network errors, 429 (rate limit), 500/502/503/504 (server errors).
Does NOT retry on: 400 (bad request), 401 (auth), 404 (not found).
"""
from __future__ import annotations
import asyncio
import logging
import random
from typing import Callable, Awaitable, TypeVar

import httpx

log = logging.getLogger("unillm.retry")

T = TypeVar("T")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
) -> T:
    """
    Run `fn` up to `max_attempts` times with exponential backoff + jitter.

    Args:
        fn:               Zero-arg async callable to retry.
        max_attempts:     Maximum number of total attempts (default 3).
        base_delay:       Initial wait in seconds (default 1.0).
        max_delay:        Cap on wait time (default 60.0).
        exponential_base: Multiplier per retry (default 2.0 → 1s, 2s, 4s…).
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in RETRYABLE_STATUS:
                # Non-retryable HTTP error — re-raise immediately
                raise
            last_exc = exc
            msg = f"HTTP {status}"

        except (httpx.ConnectError, httpx.ReadTimeout,
                httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            msg = type(exc).__name__

        if attempt == max_attempts:
            break

        # Exponential backoff with full jitter
        delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)
        delay = random.uniform(0, delay)          # full jitter

        log.warning(
            "unillm: attempt %d/%d failed (%s). Retrying in %.2fs…",
            attempt, max_attempts, msg, delay,
        )
        await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]
