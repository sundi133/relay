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
import json
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .config import RelayConfig, ModelEntry
from .handlers import call
from .retry import with_retry
from .tracker import tracker as global_tracker
from . import providers as _providers
from .guardrails_manager import RelayGuardrailManager
from .guardrails_base import GuardrailContext


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
        description="OpenAI-compatible LLM gateway with guardrails",
        version="0.1.0",
    )

    # Initialize guardrails manager
    guardrails_manager = RelayGuardrailManager(config)
    app.state.guardrails_manager = guardrails_manager

    # Initialize guardrails on startup
    @app.on_event("startup")
    async def startup_event():
        await guardrails_manager.initialize()

    @app.on_event("shutdown")
    async def shutdown_event():
        await guardrails_manager.shutdown()

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
    async def chat_completions(req: ChatCompletionRequest, request: Request):
        provider, model_id, _ = resolve(req.model)
        messages = [m.model_dump() for m in req.messages]
        t0 = time.monotonic()

        # Create guardrail context
        context = GuardrailContext(
            headers=dict(request.headers),
            model=req.model,
            messages=messages,
            metadata={}
        )

        # Input validation with guardrails
        guardrails_manager = app.state.guardrails_manager

        # Skip guardrail processing if no guardrails are configured (performance optimization)
        if guardrails_manager.has_guardrails_for_model(req.model):
            input_result = await guardrails_manager.validate_for_model(
                model_name=req.model,
                context=context,
                messages=messages,
                mode="input"
            )
        else:
            input_result = {"allowed": True}  # Fast path when no guardrails

        if not input_result.get("allowed", True):
            # Format response to match LiteLLM exactly
            metadata = input_result.get("metadata", {})
            blocked_guardrails = metadata.get("blocked_guardrails", [])

            # Create LiteLLM-compatible error message
            if blocked_guardrails:
                # Format like LiteLLM: include full guardrail details
                error_info = {
                    "info": "Request blocked by Votal guardrails",
                    "blocked_guardrails": blocked_guardrails,
                    "total_blocked": len(blocked_guardrails),
                    "inference_time_ms": metadata.get("inference_time_ms", 0)
                }
                error_message = str(error_info)  # Convert to string like LiteLLM
            else:
                error_message = input_result.get("reason", "Request blocked by guardrails")

            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": error_message,
                        "type": "None",
                        "param": "None",
                        "code": "400"
                    }
                }
            )

        # Use modified messages if available
        validated_messages = input_result.get("messages", messages)

        async def _call():
            return await call(
                provider, model_id, validated_messages,
                stream=req.stream,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            )

        if req.stream:
            async def _event_stream_with_guardrails() -> AsyncIterator[bytes]:
                gen = await with_retry(_call, max_attempts=3)
                request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
                accumulated_content = ""

                async for chunk in gen:
                    accumulated_content += chunk

                    # Validate streaming chunk (performance optimized)
                    if guardrails_manager.has_guardrails_for_model(req.model):
                        chunk_result = await guardrails_manager.validate_for_model(
                            model_name=req.model,
                            context=context,
                            response_content=accumulated_content,
                            full_response={"choices": [{"message": {"content": accumulated_content}}]},
                            mode="output"
                        )
                    else:
                        chunk_result = {"allowed": True}  # Fast path when no guardrails

                    if not chunk_result.get("allowed", True):
                        # Stream was blocked - send error and stop
                        error_data = {
                            "id": request_id,
                            "object": "chat.completion.chunk",
                            "choices": [{
                                "delta": {"content": f"[BLOCKED: {chunk_result.get('reason', 'Content blocked by guardrails')}]"},
                                "index": 0,
                                "finish_reason": "stop"
                            }]
                        }
                        yield f"data: {json.dumps(error_data)}\n\n".encode()
                        yield b"data: [DONE]\n\n"
                        return

                    # Continue streaming
                    data = (
                        '{"id":"' + request_id + '",'
                        '"object":"chat.completion.chunk",'
                        '"choices":[{"delta":{"content":' + _json_str(chunk) + '},'
                        '"index":0,"finish_reason":null}]}'
                    )
                    yield f"data: {data}\n\n".encode()

                yield b"data: [DONE]\n\n"

            return StreamingResponse(_event_stream_with_guardrails(), media_type="text/event-stream")

        # Non-streaming
        resp = await with_retry(_call, max_attempts=3)
        latency_ms = (time.monotonic() - t0) * 1000

        # Output validation with guardrails
        request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        full_response = {
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

        # Output validation with guardrails (performance optimized)
        if guardrails_manager.has_guardrails_for_model(req.model):
            output_result = await guardrails_manager.validate_for_model(
                model_name=req.model,
                context=context,
                response_content=resp["content"],
                full_response=full_response,
                mode="output"
            )
        else:
            output_result = {"allowed": True}  # Fast path when no guardrails

        if not output_result.get("allowed", True):
            # Format response to match LiteLLM exactly
            metadata = output_result.get("metadata", {})
            blocked_guardrails = metadata.get("blocked_guardrails", [])

            # Create LiteLLM-compatible error message
            if blocked_guardrails:
                error_info = {
                    "info": "Output blocked by Votal guardrails",
                    "blocked_guardrails": blocked_guardrails,
                    "total_blocked": len(blocked_guardrails),
                    "inference_time_ms": metadata.get("inference_time_ms", 0)
                }
                error_message = str(error_info)  # Convert to string like LiteLLM
            else:
                error_message = output_result.get("reason", "Response blocked by guardrails")

            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": error_message,
                        "type": "None",
                        "param": "None",
                        "code": "400"
                    }
                }
            )

        # Apply content modifications if any
        if "content" in output_result:
            full_response["choices"][0]["message"]["content"] = output_result["content"]

        global_tracker.record(f"{provider}/{model_id}", resp.get("usage", {}), latency_ms)
        return full_response

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
