"""
Tests for UniLLM — run with: pytest tests/ -v
Uses respx to mock HTTP calls so no real API keys are needed.
"""
import pytest
import respx
import httpx
import json

import unillm
from unillm.fallback import FallbackRouter
from unillm.tracker import UsageTracker
from unillm.retry import with_retry


# ── Fixtures ─────────────────────────────────────────────────────────────────

OPENAI_RESP = {
    "id": "chatcmpl-test",
    "choices": [{"message": {"role": "assistant", "content": "Hello from OpenAI!"}}],
    "model": "gpt-4o-mini",
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

ANTHROPIC_RESP = {
    "id": "msg-test",
    "content": [{"type": "text", "text": "Hello from Claude!"}],
    "model": "claude-haiku-4-5-20251001",
    "usage": {"input_tokens": 10, "output_tokens": 5},
}

QWEN_RESP = {
    "choices": [{"message": {"role": "assistant", "content": "你好！我是通义千问。"}}],
    "model": "qwen-turbo",
    "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
}

MESSAGES = [{"role": "user", "content": "Hello"}]


# ── OpenAI-compatible providers ───────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_openai_completion():
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OPENAI_RESP)
    )
    resp = await unillm.completion("openai/gpt-4o-mini", MESSAGES)
    assert resp.content == "Hello from OpenAI!"
    assert resp.usage["total_tokens"] == 15


@pytest.mark.asyncio
@respx.mock
async def test_qwen_completion():
    respx.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    ).mock(return_value=httpx.Response(200, json=QWEN_RESP))
    resp = await unillm.completion("qwen/qwen-turbo", MESSAGES)
    assert "千问" in resp.content


@pytest.mark.asyncio
@respx.mock
async def test_glm_completion():
    respx.post("https://open.bigmodel.cn/api/paas/v4/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": "Hello from GLM!"}}],
            "model": "glm-4",
            "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
        })
    )
    resp = await unillm.completion("glm/glm-4", MESSAGES)
    assert resp.content == "Hello from GLM!"


@pytest.mark.asyncio
@respx.mock
async def test_ollama_local():
    respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": "Hi from local Llama!"}}],
            "model": "llama3.2",
            "usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9},
        })
    )
    resp = await unillm.completion("ollama/llama3.2", MESSAGES)
    assert "Llama" in resp.content


# ── Anthropic ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_anthropic_completion():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json=ANTHROPIC_RESP)
    )
    resp = await unillm.completion(
        "anthropic/claude-haiku-4-5-20251001", MESSAGES
    )
    assert resp.content == "Hello from Claude!"


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_system_message():
    captured = {}

    async def capture(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=ANTHROPIC_RESP)

    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=capture)

    msgs = [
        {"role": "system", "content": "You are a pirate."},
        {"role": "user", "content": "Hello"},
    ]
    await unillm.completion("anthropic/claude-haiku-4-5-20251001", msgs)
    # system message should be extracted to top-level "system" field
    assert captured["body"]["system"] == "You are a pirate."
    assert all(m["role"] != "system" for m in captured["body"]["messages"])


# ── Retry logic ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_on_429():
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.HTTPStatusError(
                "rate limited",
                request=httpx.Request("POST", "http://x"),
                response=httpx.Response(429),
            )
        return "ok"

    result = await with_retry(flaky, max_attempts=3, base_delay=0)
    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_no_retry_on_400():
    call_count = 0

    async def bad_request():
        nonlocal call_count
        call_count += 1
        raise httpx.HTTPStatusError(
            "bad request",
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(400),
        )

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(bad_request, max_attempts=3, base_delay=0)

    assert call_count == 1   # should NOT retry 400s


# ── Fallback router ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_fallback_uses_second_on_failure():
    # First provider always fails
    respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "overloaded"})
    )
    # Second provider succeeds
    respx.post("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=QWEN_RESP)
    )

    router = FallbackRouter(
        ["deepseek/deepseek-chat", "qwen/qwen-turbo"],
        max_attempts_per_model=1,
    )
    resp = await router.completion(MESSAGES)
    assert resp["_used_model"] == "qwen/qwen-turbo"
    assert "千问" in resp.content


# ── Cost tracker ──────────────────────────────────────────────────────────────

# ── Custom provider registration ──────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_custom_provider():
    unillm.register_provider(
        "myserver",
        "http://my-custom-server:9000/v1",
        api_key="sk-custom-key",
    )
    respx.post("http://my-custom-server:9000/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": "Custom model response"}}],
            "model": "my-fine-tune",
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        })
    )
    resp = await unillm.completion("myserver/my-fine-tune", MESSAGES)
    assert resp.content == "Custom model response"


# ── Bad model format ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bad_model_format():
    with pytest.raises(ValueError, match="provider/model-id"):
        await unillm.completion("gpt-4o-mini", MESSAGES)  # missing provider prefix
