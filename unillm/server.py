"""
Relay proxy server — exposes an OpenAI-compatible HTTP API.

Endpoints:
    GET  /health
    GET  /v1/models
    POST /v1/chat/completions
    GET  /v1/usage            (relay extension)

All endpoints mirror the LiteLLM proxy interface so existing clients
(OpenAI SDK, LangChain, anything pointing at an OpenAI-compatible URL)
work without changes — just set:

    client = OpenAI(base_url="http://localhost:4000/v1", api_key="any")
"""
from __future__ import annotations

import time
import uuid
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .config import RelayConfig, ModelEntry
from .handlers import call
from .retry import with_retry
from .tracker import tracker as global_tracker
from . import providers as _providers


# ---------------------------------------------------------------------------
# Request / Response schemas (OpenAI-compatible)
# ---------------------------------------------------------------------------
class Message(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    temperature: float = 0.7
    max_tokens: int = 1024
    stream: bool = False
    n: int = 1


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(config: RelayConfig) -> FastAPI:
    app = FastAPI(
        title="Relay LLM Proxy",
        description="OpenAI-compatible LLM gateway",
        version="0.1.0",
    )

    # ── Auth middleware ──────────────────────────────────────────────────────
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if config.master_key and request.url.path not in ("/health", "/"):
            auth = request.headers.get("Authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            if token != config.master_key:
                return JSONResponse(
                    status_code=401,
                    content={"error": {"message": "Invalid API key", "type": "auth_error"}},
                )
        return await call_next(request)

    # ── Helper: resolve model alias → relay provider/model string ───────────
    def resolve(model_name: str) -> tuple[str, str, Optional[str]]:
        """
        Returns (provider, model_id, api_key_override).
        Accepts either a config alias ("my-gpt4") or a direct
        relay string ("openai/gpt-4o").
        """
        entry: Optional[ModelEntry] = config.model_for(model_name)
        if entry:
            relay_model = entry.model
            key_override = entry.api_key
        elif "/" in model_name:
            # Direct provider/model string — passthrough
            relay_model = model_name
            key_override = None
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Model '{model_name}' not found. "
                       f"Available: {config.model_names} "
                       f"or use 'provider/model' format directly.",
            )

        provider, model_id = relay_model.split("/", 1)

        # Inject api_key override into providers registry for this call
        if key_override:
            _providers._REGISTRY.setdefault(provider, {})
            _providers._REGISTRY[provider]["_key"] = key_override

        return provider, model_id, key_override

    # ── Routes ───────────────────────────────────────────────────────────────

    @app.get("/")
    @app.get("/health")
    async def health():
        return {"status": "ok", "proxy": "relay"}

    @app.get("/v1/models")
    async def list_models():
        """List all models defined in config.yaml."""
        data = [
            {
                "id": m.model_name,
                "object": "model",
                "created": 0,
                "owned_by": "relay",
                "relay_target": m.model,
            }
            for m in config.models
        ]
        return {"object": "list", "data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest):
        provider, model_id, _ = resolve(req.model)
        messages = [m.model_dump() for m in req.messages]
        t0 = time.monotonic()

        async def _call():
            return await call(
                provider, model_id, messages,
                stream=req.stream,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            )

        if req.stream:
            async def _event_stream() -> AsyncIterator[bytes]:
                gen = await with_retry(_call, max_attempts=3)
                request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
                async for chunk in gen:
                    data = (
                        '{"id":"' + request_id + '",'
                        '"object":"chat.completion.chunk",'
                        '"choices":[{"delta":{"content":' + _json_str(chunk) + '},'
                        '"index":0,"finish_reason":null}]}'
                    )
                    yield f"data: {data}\n\n".encode()
                yield b"data: [DONE]\n\n"

            return StreamingResponse(_event_stream(), media_type="text/event-stream")

        # Non-streaming
        resp = await with_retry(_call, max_attempts=3)
        latency_ms = (time.monotonic() - t0) * 1000
        global_tracker.record(f"{provider}/{model_id}", resp.get("usage", {}), latency_ms)

        # Return in OpenAI wire format
        request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        return {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": resp["content"]},
                    "finish_reason": "stop",
                }
            ],
            "usage": resp.get("usage", {}),
            "relay": {"target": f"{provider}/{model_id}"},
        }

    @app.get("/v1/usage")
    async def usage():
        """Relay extension: live cost + usage stats."""
        per_model = {
            name: {
                "calls": s.calls,
                "total_tokens": s.total_tokens,
                "cost_usd": round(s.cost_usd, 8),
                "errors": s.errors,
                "avg_latency_ms": round(s.avg_latency_ms, 1),
            }
            for name, s in global_tracker.per_model().items()
        }
        return {
            "total_calls": global_tracker.total_calls,
            "total_tokens": global_tracker.total_tokens,
            "total_cost_usd": round(global_tracker.total_cost_usd, 8),
            "db_path": str(global_tracker.db_path),
            "per_model": per_model,
        }

    return app


def _json_str(s: str) -> str:
    """Minimal JSON string escaping for SSE chunks."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
