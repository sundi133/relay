"""
Ultra-fast relay server with optimizations:
- Connection pooling
- No database tracking
- Minimal allocations
- Optimized response generation
"""
from __future__ import annotations

import time
import uuid
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .config import RelayConfig, ModelEntry
from .handlers_optimized import call_optimized, OptimizedClient
from .retry import with_retry
from . import providers as _providers


# ---------------------------------------------------------------------------
# Lightweight Request schemas
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
# Pre-compiled response templates for maximum speed
# ---------------------------------------------------------------------------
ERROR_TEMPLATE = {"error": {"message": None, "type": "auth_error"}}

SUCCESS_TEMPLATE = {
    "id": None,
    "object": "chat.completion",
    "created": None,
    "model": None,
    "choices": [{
        "index": 0,
        "message": {"role": "assistant", "content": None},
        "finish_reason": "stop",
    }],
    "usage": None,
    "relay": {"target": None},
}


# ---------------------------------------------------------------------------
# Ultra-fast app factory
# ---------------------------------------------------------------------------
def create_fast_app(config: RelayConfig) -> FastAPI:
    """Create ultra-fast app with no database overhead"""
    app = FastAPI(
        title="Relay LLM Proxy (Ultra-Fast)",
        description="Maximum performance OpenAI-compatible gateway",
        version="0.2.0",
        docs_url=None,      # Disable docs
        redoc_url=None,     # Disable redocs
        openapi_url=None,   # Disable OpenAPI schema
    )

    # Pre-compile auth values for speed
    master_key = config.master_key
    protected_paths = {"/health", "/"}

    # ── Ultra-fast auth middleware ────────────────────────────────────────
    @app.middleware("http")
    async def fast_auth_middleware(request: Request, call_next):
        if master_key and request.url.path not in protected_paths:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return JSONResponse(status_code=401, content=ERROR_TEMPLATE)

            token = auth_header[7:].strip()  # Remove "Bearer " prefix
            if token != master_key:
                error_resp = ERROR_TEMPLATE.copy()
                error_resp["error"]["message"] = "Invalid API key"
                return JSONResponse(status_code=401, content=error_resp)

        return await call_next(request)

    # ── Cached model resolution ──────────────────────────────────────────
    _model_cache = {}

    def resolve_fast(model_name: str) -> tuple[str, str, Optional[str]]:
        """Ultra-fast model resolution with caching"""
        if model_name in _model_cache:
            return _model_cache[model_name]

        entry: Optional[ModelEntry] = config.model_for(model_name)
        if entry:
            relay_model = entry.model
            key_override = entry.api_key
        elif "/" in model_name:
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

        # Cache the result for next time
        result = (provider, model_id, key_override)
        _model_cache[model_name] = result

        # Set key override if needed
        if key_override:
            _providers._REGISTRY.setdefault(provider, {})
            _providers._REGISTRY[provider]["_key"] = key_override

        return result

    # ── Ultra-fast routes with zero overhead ─────────────────────────────
    @app.get("/")
    @app.get("/health")
    async def health():
        return {"status": "ok", "proxy": "relay-fast"}

    @app.get("/v1/models")
    async def list_models():
        """Fast model listing"""
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
    async def ultra_fast_chat_completions(req: ChatCompletionRequest):
        """Ultra-fast chat completions with no database overhead"""
        provider, model_id, _ = resolve_fast(req.model)

        # Convert messages once
        messages = [{"role": m.role, "content": m.content} for m in req.messages]

        async def _call():
            return await call_optimized(
                provider, model_id, messages,
                stream=req.stream,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            )

        if req.stream:
            async def _fast_stream() -> AsyncIterator[bytes]:
                gen = await with_retry(_call, max_attempts=3)
                request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

                async for chunk in gen:
                    # Ultra-fast JSON generation
                    escaped = chunk.replace('"', '\\"').replace('\n', '\\n')
                    data = f'{{"id":"{request_id}","object":"chat.completion.chunk","choices":[{{"delta":{{"content":"{escaped}"}},"index":0,"finish_reason":null}}]}}'
                    yield f"data: {data}\n\n".encode()
                yield b"data: [DONE]\n\n"

            return StreamingResponse(_fast_stream(), media_type="text/event-stream")

        # Ultra-fast non-streaming path
        response = await with_retry(_call, max_attempts=3)

        # No database tracking - just return response immediately
        result = SUCCESS_TEMPLATE.copy()
        result["id"] = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        result["created"] = int(time.time())
        result["model"] = req.model
        result["choices"][0]["message"]["content"] = response["content"]
        result["usage"] = response.get("usage", {})
        result["relay"]["target"] = f"{provider}/{model_id}"

        return result

    @app.get("/v1/usage")
    async def fast_usage():
        """Lightweight usage endpoint (no tracking)"""
        return {
            "message": "Usage tracking disabled for maximum performance",
            "total_calls": "not_tracked",
            "total_tokens": "not_tracked",
            "total_cost_usd": "not_tracked",
            "tracking_enabled": False
        }

    # Cleanup handler
    @app.on_event("shutdown")
    async def shutdown_event():
        await OptimizedClient.close()

    return app