#!/usr/bin/env python3
"""
Votal AI Guardrails Integration for Relay
=========================================

Relay-compatible version of Votal AI guardrails integration.
Delegates to RunPod server for:
- Input/Output guardrails via /guardrails/input and /guardrails/output
- Agentic tool call authorization and validation
- Role-based data sanitization
- Multi-tenant policy management (policies stored on RunPod server)

Version: 2.0 (Relay Compatible)
"""

import json
import time
import asyncio
import os
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass
import httpx

from .guardrails_base import BaseGuardrail, GuardrailContext


@dataclass
class VotalConfig:
    """Configuration for Votal guardrails integration"""
    api_base: str
    api_token: str = None  # RunPod token for authentication (can be loaded from env)

    # Default tenant/user context - can be overridden per request
    default_tenant_api_key: str = "basic-api-key-12345"
    default_user_role: str = "user"
    default_agent_id: Optional[str] = None

    # Endpoint configuration
    input_endpoint: str = "/guardrails/input"
    output_endpoint: str = "/guardrails/output"

    # Conditional guardrail activation
    conditional_activation: bool = False
    required_headers: List[str] = None
    pass_through_on_missing: bool = True

    # Timeouts and behavior
    timeout: int = 30
    max_retries: int = 2
    block_on_failure: bool = True


class VotalGuardrail(BaseGuardrail):
    """Votal AI Guardrails integration for Relay"""

    def __init__(self, config: Union[Dict, VotalConfig] = None, **kwargs):
        super().__init__(config, **kwargs)

        if config is None:
            # Load from environment and config
            config = self._load_default_config()

        if isinstance(config, dict):
            self.votal_config = VotalConfig(**config)
        else:
            self.votal_config = config

        # Ensure api_token is loaded from environment if not provided or is env reference
        if not self.votal_config.api_token or self.votal_config.api_token.startswith("os.environ/"):
            if self.votal_config.api_token and self.votal_config.api_token.startswith("os.environ/"):
                # Handle "os.environ/RUNPOD_TOKEN" format
                env_var = self.votal_config.api_token.split("/", 1)[1]
                runpod_token = os.getenv(env_var)
            else:
                # Default to RUNPOD_TOKEN
                runpod_token = os.getenv("RUNPOD_TOKEN")

            if not runpod_token:
                raise ValueError("RUNPOD_TOKEN not found in environment")
            self.votal_config.api_token = runpod_token

        # HTTP client for RunPod API calls
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.votal_config.timeout),
            headers={
                "User-Agent": "Relay-Votal-Guardrails/2.0"
            }
        )

        print(f"🔒 VotalGuardrail initialized: {self.votal_config.api_base}")

    def _load_default_config(self) -> dict:
        """Load default configuration from environment and config files"""

        # Try to get RunPod token from environment
        runpod_token = os.getenv("RUNPOD_TOKEN")
        if not runpod_token:
            try:
                # Try to load from .env file in relay directory
                with open('.env', 'r') as f:
                    for line in f:
                        if line.startswith('RUNPOD_TOKEN='):
                            runpod_token = line.split('=', 1)[1].strip().strip('"')
                            break
            except FileNotFoundError:
                pass

        if not runpod_token:
            raise ValueError("RUNPOD_TOKEN not found in environment or .env file")

        return {
            "api_base": os.getenv("VOTAL_API_BASE", "https://kk5losqxwr2ui7.api.runpod.ai"),
            "api_token": runpod_token,
            "default_tenant_api_key": os.getenv("VOTAL_DEFAULT_TENANT_KEY", "basic-api-key-12345"),
            "default_user_role": os.getenv("VOTAL_DEFAULT_USER_ROLE", "user"),
            "timeout": int(os.getenv("VOTAL_TIMEOUT", "30")),
            "block_on_failure": os.getenv("VOTAL_BLOCK_ON_FAILURE", "true").lower() == "true"
        }

    def _should_activate_guardrails(self, context: GuardrailContext) -> bool:
        """Check if guardrails should be activated based on headers/conditions"""

        if not self.votal_config.conditional_activation:
            return True

        if not self.votal_config.required_headers:
            return True

        # Check for required headers
        headers_found = []
        for required_header in self.votal_config.required_headers:
            header_variations = [
                required_header,
                required_header.lower(),
                required_header.upper(),
                required_header.replace("-", "_").lower(),
                required_header.replace("_", "-").lower()
            ]

            for variation in header_variations:
                if context.headers.get(variation):
                    headers_found.append(required_header)
                    break

        if headers_found:
            print(f"🔒 Guardrails activated: Found headers {headers_found}")
            return True
        else:
            if self.votal_config.pass_through_on_missing:
                print(f"🔀 Guardrails skipped: Missing required headers {self.votal_config.required_headers}")
                return False
            else:
                print(f"🔒 Guardrails activated: Strict mode")
                return True

    def _extract_tenant_context(self, context: GuardrailContext) -> Dict[str, str]:
        """Extract tenant and user context from request headers"""

        tenant_api_key = (
            context.headers.get("X-API-Key") or
            context.headers.get("X-Shield-Key") or
            context.headers.get("X-Tenant-ID") or
            context.headers.get("X-Votal-Key") or
            context.metadata.get("tenant_api_key") or
            self.votal_config.default_tenant_api_key
        )

        user_role = (
            context.headers.get("X-User-Role") or
            context.metadata.get("user_role") or
            self.votal_config.default_user_role
        )

        agent_id = (
            context.headers.get("X-Agent-ID") or
            context.metadata.get("agent_id") or
            self.votal_config.default_agent_id
        )

        return {
            "tenant_api_key": tenant_api_key,
            "user_role": user_role,
            "agent_id": agent_id
        }

    def _extract_user_message(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Extract user message content from messages array"""
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    # Handle multi-modal content
                    text_parts = [part.get("text") for part in content if part.get("type") == "text"]
                    return " ".join(filter(None, text_parts))
        return None

    async def _validate_input_with_server(
        self,
        message: str,
        tenant_api_key: str,
        user_role: str,
        agent_id: Optional[str]
    ) -> Dict[str, Any]:
        """Validate input message through RunPod server"""
        url = f"{self.votal_config.api_base.rstrip('/')}{self.votal_config.input_endpoint}"

        payload = {"message": message}
        headers = {
            "Authorization": f"Bearer {self.votal_config.api_token}",
            "X-API-Key": tenant_api_key,
            "X-User-Role": user_role,
            "Content-Type": "application/json"
        }

        if agent_id:
            headers["X-Agent-ID"] = agent_id

        try:
            print(f"🔍 DEBUG: Calling Votal API: {url}")
            print(f"🔍 DEBUG: Payload: {payload}")
            print(f"🔍 DEBUG: Headers: {headers}")

            response = await self.client.post(url, json=payload, headers=headers)

            print(f"🔍 DEBUG: Response status: {response.status_code}")
            print(f"🔍 DEBUG: Response text: {response.text}")

            if response.status_code == 200:
                data = response.json()
                print(f"🔍 DEBUG: Parsed response: {data}")
                return data
            else:
                print(f"🔍 DEBUG: API Error {response.status_code}: {response.text}")
                return {
                    "safe": False,
                    "reason": f"Server error: {response.status_code}",
                    "guardrail_results": []
                }

        except Exception as e:
            return {
                "safe": False,
                "reason": f"Request failed: {str(e)}",
                "guardrail_results": []
            }

    async def _validate_output_with_server(
        self,
        content: str,
        tenant_api_key: str,
        user_role: str,
        agent_id: Optional[str],
        tool_calls: List[Dict] = None
    ) -> Dict[str, Any]:
        """Validate output content through RunPod server"""
        url = f"{self.votal_config.api_base.rstrip('/')}{self.votal_config.output_endpoint}"

        payload = {"output": content}

        # Add agentic context if tool calls are present
        if tool_calls:
            payload["agent_call"] = {
                "agent_id": agent_id or "unknown",
                "tools_called": tool_calls,
                "user_role": user_role
            }

        headers = {
            "Authorization": f"Bearer {self.votal_config.api_token}",
            "X-API-Key": tenant_api_key,
            "X-User-Role": user_role,
            "Content-Type": "application/json"
        }

        if agent_id:
            headers["X-Agent-ID"] = agent_id

        try:
            response = await self.client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                data = response.json()
                return data
            else:
                return {
                    "safe": False,
                    "reason": f"Server validation failed: {response.status_code}",
                    "guardrail_results": []
                }

        except Exception as e:
            return {
                "safe": False,
                "reason": f"Validation error: {str(e)}",
                "guardrail_results": []
            }

    # Implement BaseGuardrail abstract methods

    async def validate_input(
        self,
        context: GuardrailContext,
        messages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Validate input messages before sending to LLM"""

        # Check if guardrails should be activated
        if not self._should_activate_guardrails(context):
            return {"allowed": True}

        # Extract tenant and user context
        tenant_context = self._extract_tenant_context(context)

        # Get the user message content
        user_message = self._extract_user_message(messages)
        if not user_message:
            return {"allowed": True}

        # Validate with server
        result = await self._validate_input_with_server(
            message=user_message,
            tenant_api_key=tenant_context["tenant_api_key"],
            user_role=tenant_context["user_role"],
            agent_id=tenant_context["agent_id"]
        )

        if not result.get("safe", False):
            blocked_guardrails = []
            for guardrail_result in result.get("guardrail_results", []):
                if not guardrail_result.get("passed", True):
                    blocked_guardrails.append({
                        "guardrail": guardrail_result.get("guardrail", "unknown"),
                        "message": guardrail_result.get("message", "blocked"),
                        "action": guardrail_result.get("action", "block")
                    })

            return {
                "allowed": False,
                "reason": f"Blocked by {len(blocked_guardrails)} guardrails: {', '.join([g['guardrail'] for g in blocked_guardrails])}",
                "metadata": {
                    "blocked_guardrails": blocked_guardrails,
                    "inference_time_ms": result.get("inference_time_ms", 0)
                }
            }

        return {"allowed": True}

    async def validate_output(
        self,
        context: GuardrailContext,
        response_content: str,
        full_response: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate output response before sending to client"""

        # Check if guardrails should be activated
        if not self._should_activate_guardrails(context):
            return {"allowed": True}

        # Extract tenant and user context
        tenant_context = self._extract_tenant_context(context)

        # Extract tool calls if present (for agentic validation)
        tool_calls = []
        if "choices" in full_response:
            for choice in full_response["choices"]:
                if "message" in choice and "tool_calls" in choice["message"]:
                    if choice["message"]["tool_calls"]:
                        for tool_call in choice["message"]["tool_calls"]:
                            tool_calls.append({
                                "id": tool_call.get("id"),
                                "type": tool_call.get("type", "function"),
                                "function": tool_call.get("function", {})
                            })

        # Validate with server
        result = await self._validate_output_with_server(
            content=response_content,
            tenant_api_key=tenant_context["tenant_api_key"],
            user_role=tenant_context["user_role"],
            agent_id=tenant_context["agent_id"],
            tool_calls=tool_calls
        )

        if not result.get("safe", True):
            blocked_guardrails = []
            for guardrail_result in result.get("guardrail_results", []):
                if not guardrail_result.get("passed", True):
                    blocked_guardrails.append({
                        "guardrail": guardrail_result.get("guardrail", "unknown"),
                        "message": guardrail_result.get("message", "blocked"),
                        "action": guardrail_result.get("action", "block")
                    })

            return {
                "allowed": False,
                "reason": f"Output blocked by {len(blocked_guardrails)} guardrails",
                "metadata": {
                    "blocked_guardrails": blocked_guardrails,
                    "inference_time_ms": result.get("inference_time_ms", 0)
                }
            }

        # Handle content sanitization if server provided sanitized output
        if "sanitized_output" in result:
            return {
                "allowed": True,
                "modified_content": result["sanitized_output"]
            }

        return {"allowed": True}

    async def validate_streaming_chunk(
        self,
        context: GuardrailContext,
        chunk_content: str,
        accumulated_content: str
    ) -> Dict[str, Any]:
        """Validate streaming content chunk"""

        # Check if guardrails should be activated
        if not self._should_activate_guardrails(context):
            return {"allowed": True}

        # For streaming, we validate accumulated content periodically
        if len(accumulated_content) % 500 == 0 and len(accumulated_content) > 0:
            # Extract tenant context
            tenant_context = self._extract_tenant_context(context)

            # Create a mock full response for validation
            mock_response = {
                "choices": [{"message": {"content": accumulated_content}}]
            }

            result = await self.validate_output(context, accumulated_content, mock_response)

            if not result.get("allowed", True):
                return {
                    "allowed": False,
                    "stop_stream": True,
                    "reason": result.get("reason", "Streaming content blocked"),
                    "metadata": result.get("metadata", {})
                }

        return {"allowed": True}

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.client.aclose()


# Factory function for easy integration
def create_votal_guardrail(
    api_base: str = None,
    api_token: str = None,
    **kwargs
) -> VotalGuardrail:
    """Factory function to create Votal guardrail integration

    Args:
        api_base: Base URL for RunPod server
        api_token: RunPod token for API authentication
        **kwargs: Additional configuration options

    Returns:
        VotalGuardrail instance ready for use with Relay
    """
    config = {}
    if api_base:
        config["api_base"] = api_base
    if api_token:
        config["api_token"] = api_token
    config.update(kwargs)

    return VotalGuardrail(config)