"""
UniLLM — a minimal, secure, supply-chain-safe LLM router.

Quick start:
    import unillm

    resp = await unillm.completion("qwen/qwen-turbo", [
        {"role": "user", "content": "Hello!"}
    ])
    print(resp.content)
"""
from __future__ import annotations
from typing import AsyncIterator

from .handlers import call
from .retry import with_retry
from .fallback import FallbackRouter
from .providers import (
    register as register_provider,
    list_providers,
    get as _get_provider,
)

__version__ = "0.1.0"
__all__ = [
    "completion",
    "stream",
    "register_provider",
    "list_providers",
    "FallbackRouter",
]


async def completion(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    max_attempts: int = 3,
    **kwargs,
) -> dict:
    """
    Single-turn completion with automatic retry.

    Args:
        model:        "provider/model-id"  e.g. "qwen/qwen-turbo"
        messages:     OpenAI-style message list
        temperature:  Sampling temperature (default 0.7)
        max_tokens:   Max output tokens (default 1024)
        max_attempts: Retry attempts on transient errors (default 3)

    Returns:
        Response dict with keys: content, model, usage, raw
        Accessible as resp["content"] or resp.content

    Supported providers:
        openai, anthropic, gemini,
        qwen, glm, kimi, deepseek, yi, mistral, baichuan, minimax,
        ollama, vllm, llamacpp, tgi,
        + any custom provider added with register_provider()

    Examples:
        await unillm.completion("glm/glm-4", messages)
        await unillm.completion("ollama/qwen2.5:7b", messages)
        await unillm.completion("vllm/Qwen2.5-72B-Instruct", messages)
    """
    provider, model_id = _parse_model(model)

    async def _call():
        return await call(provider, model_id, messages,
                          stream=False,
                          temperature=temperature,
                          max_tokens=max_tokens,
                          **kwargs)

    resp = await with_retry(_call, max_attempts=max_attempts)
    return resp


async def stream(
    model: str,
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    **kwargs,
) -> AsyncIterator[str]:
    """
    Streaming completion. Yields text chunks as they arrive.

    Example:
        async for chunk in unillm.stream("deepseek/deepseek-chat", messages):
            print(chunk, end="", flush=True)
    """
    provider, model_id = _parse_model(model)
    return await call(provider, model_id, messages,
                      stream=True,
                      temperature=temperature,
                      max_tokens=max_tokens,
                      **kwargs)


def _parse_model(model: str) -> tuple[str, str]:
    if "/" not in model:
        raise ValueError(
            f"Model must be in 'provider/model-id' format, got: '{model}'\n"
            f"Example: 'qwen/qwen-turbo', 'ollama/llama3.2'"
        )
    provider, model_id = model.split("/", 1)
    return provider, model_id
