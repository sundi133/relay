#!/usr/bin/env python3
"""
Votal AI Guardrails Integration for LiteLLM
==========================================

Simple LiteLLM integration that delegates to RunPod server for:
- Input/Output guardrails via /guardrails/input and /guardrails/output
- Agentic tool call authorization and validation
- Role-based data sanitization
- Multi-tenant policy management (policies stored on RunPod server)

Version: 2.0
Compatible with: LiteLLM 1.0+
"""

import json
import time
import asyncio
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass
import httpx
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.proxy._types import UserAPIKeyAuth
from litellm.caching.caching import DualCache


@dataclass
class GuardrailConfig:
    """Simple configuration for Votal guardrails integration"""
    api_base: str
    api_token: str  # RunPod token for authentication

    # Default tenant/user context - can be overridden per request
    default_tenant_api_key: str = "basic-api-key-12345"
    default_user_role: str = "user"
    default_agent_id: Optional[str] = None

    # Endpoint configuration (new guardrail endpoints)
    input_endpoint: str = "/guardrails/input"
    output_endpoint: str = "/guardrails/output"

    # Conditional guardrail activation
    conditional_activation: bool = False  # Enable conditional guardrails
    required_headers: List[str] = None    # Headers required for activation
    pass_through_on_missing: bool = True  # Skip guardrails if headers missing

    # Timeouts and behavior
    timeout: int = 30
    max_retries: int = 2
    block_on_failure: bool = True


class VotalGuardrail(CustomGuardrail):
    """Simple Votal AI Guardrails integration for LiteLLM - delegates to RunPod server"""

    def __init__(self, config: Union[Dict, GuardrailConfig] = None, **kwargs):
        """Initialize the Votal guardrail integration

        Args:
            config: Guardrail configuration as dict or GuardrailConfig object
            **kwargs: Additional parameters from LiteLLM (guardrail_name, mode, etc.)
        """
        super().__init__(**kwargs)

        if config is None:
            # Default configuration for LiteLLM guardrails system
            import os

            # Try to get RunPod token from environment
            runpod_token = os.getenv("RUNPOD_TOKEN")

            if not runpod_token:
                # Try to load from .env file
                try:
                    with open('/Users/jyotirmoysundi/git/llm-shield/.env', 'r') as f:
                        for line in f:
                            if line.startswith('export RUNPOD_TOKEN='):
                                runpod_token = line.split('=', 1)[1].strip().strip('"')
                                os.environ['RUNPOD_TOKEN'] = runpod_token
                                break
                except:
                    pass

            if not runpod_token:
                raise ValueError("RUNPOD_TOKEN not found in environment")

            # Try to read config from config.yaml file (LiteLLM doesn't pass this automatically)
            yaml_config = self._load_config_from_yaml()

            config = {
                "api_base": yaml_config.get("api_base", "https://kk5losqxwr2ui7.api.runpod.ai"),
                "api_token": runpod_token,
                "default_tenant_api_key": yaml_config.get("default_tenant_api_key", "basic-api-key-12345"),
                "default_user_role": yaml_config.get("default_user_role", "user"),
                "conditional_activation": yaml_config.get("conditional_activation", False),
                "required_headers": yaml_config.get("required_headers", []),
                "pass_through_on_missing": yaml_config.get("pass_through_on_missing", True),
                "timeout": yaml_config.get("timeout", 30),
                "block_on_failure": yaml_config.get("block_on_failure", True)
            }

        if isinstance(config, dict):
            self.config = GuardrailConfig(**config)
        else:
            self.config = config

        # HTTP client for RunPod API calls
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_token}",
                "User-Agent": "LiteLLM-Votal-Guardrails/2.0"
            }
        )

        print(f"🔒 VotalGuardrail started: {self.config.api_base}")
        if self.config.conditional_activation:
            print(f"🔀 Conditional activation: {self.config.required_headers}")

    def _load_config_from_yaml(self) -> dict:
        """Load votal_guardrail config from config.yaml file"""
        try:
            import yaml
            import os

            # Look for config.yaml in current directory and common locations
            possible_paths = ["config.yaml", "./config.yaml", "litellm_config.yaml"]

            for config_path in possible_paths:
                if os.path.exists(config_path):
                    with open(config_path, 'r') as f:
                        config = yaml.safe_load(f)

                    votal_config = config.get("votal_guardrail", {})
                    if votal_config:
                        print(f"🔧 Loaded config from {config_path}")
                        return votal_config

        except Exception as e:
            print(f"⚠️  Could not load config from YAML: {e}")

        return {}  # Return empty dict if loading fails

    def _should_activate_guardrails(self, data: dict) -> bool:
        """Check if guardrails should be activated based on headers/conditions"""

        if not self.config.conditional_activation:
            return True  # Always activate if conditional mode is disabled

        if not self.config.required_headers:
            return True  # No specific headers required

        # DEBUG: Log the entire data structure to see where headers are
        print(f"🔍 DEBUG: data keys = {list(data.keys())}")
        print(f"🔍 DEBUG: extra_headers = {data.get('extra_headers', {})}")
        print(f"🔍 DEBUG: proxy_server_request = {data.get('proxy_server_request', {})}")
        metadata = data.get('metadata', {})
        print(f"🔍 DEBUG: metadata headers = {metadata.get('headers', {})}")
        print(f"🔍 DEBUG: user_api_key_auth_metadata = {metadata.get('user_api_key_auth_metadata', {})}")
        print(f"🔍 DEBUG: requester_metadata = {metadata.get('requester_metadata', {})}")

        # Check for required headers in various locations
        headers_found = []

        # Check extra_headers
        extra_headers = data.get("extra_headers", {})

        # Check metadata headers
        metadata = data.get("metadata", {})
        metadata_headers = metadata.get("headers", {})

        # Check proxy_server_request headers (this is where HTTP headers are stored!)
        proxy_request = data.get("proxy_server_request", {})
        proxy_headers = proxy_request.get("headers", {})

        for required_header in self.config.required_headers:
            # Check variations of header names (case-insensitive)
            header_variations = [
                required_header,
                required_header.lower(),
                required_header.upper(),
                required_header.replace("-", "_").lower(),
                required_header.replace("_", "-").lower()
            ]

            found = False
            for variation in header_variations:
                if (extra_headers.get(variation) or
                    metadata_headers.get(variation) or  # Check metadata headers correctly!
                    proxy_headers.get(variation) or
                    data.get(variation)):
                    headers_found.append(required_header)
                    found = True
                    break

            if not found and not self.config.pass_through_on_missing:
                return False  # Strict mode: all headers required

        # Log activation decision
        if headers_found:
            print(f"🔒 Guardrails activated: Found headers {headers_found}")
            return True
        else:
            if self.config.pass_through_on_missing:
                print(f"🔀 Guardrails skipped: Missing required headers {self.config.required_headers}")
                return False  # Skip guardrails
            else:
                print(f"🔒 Guardrails activated: Strict mode, headers required but missing {self.config.required_headers}")
                return True   # Activate guardrails (will likely block)

    async def async_pre_call_hook(self, user_api_key_dict: UserAPIKeyAuth, cache: DualCache, data: dict, call_type):
        """Pre-API call hook - validate input with guardrails"""
        try:
            # DEBUG: Check if user_api_key_dict contains headers
            print(f"🔍 DEBUG: user_api_key_dict type = {type(user_api_key_dict)}")
            if hasattr(user_api_key_dict, '__dict__'):
                print(f"🔍 DEBUG: user_api_key_dict attrs = {list(user_api_key_dict.__dict__.keys())}")
            if hasattr(user_api_key_dict, 'metadata'):
                print(f"🔍 DEBUG: user_api_key_dict.metadata = {user_api_key_dict.metadata}")

            # Check if guardrails should be activated
            if not self._should_activate_guardrails(data):
                return data  # Skip guardrails, pass through

            # Extract tenant and user context from request
            tenant_api_key = self._extract_tenant_api_key(data)
            user_role = self._extract_user_role(data)
            agent_id = self._extract_agent_id(data)

            # Get the user message content
            messages = data.get("messages", [])
            user_message = self._extract_user_message(messages)
            if not user_message:
                return data

            # Validate input through RunPod server (server handles all policy logic)
            validation_result = await self._validate_input_with_server(
                message=user_message,
                tenant_api_key=tenant_api_key,
                user_role=user_role,
                agent_id=agent_id
            )

            # Handle result - return clean guardrail info without excessive logging
            if not validation_result.get("safe", False) and self.config.block_on_failure:
                from fastapi import HTTPException

                # Extract blocked guardrails from guardrail_results
                blocked_guardrails = []
                guardrail_results = validation_result.get("guardrail_results", [])

                for result in guardrail_results:
                    if not result.get("passed", True):  # Failed guardrails
                        blocked_guardrails.append({
                            "guardrail": result.get("guardrail", "unknown"),
                            "message": result.get("message", "blocked"),
                            "details": result.get("details", {}),
                            "action": result.get("action", "block")
                        })

                # Create clean response info (not error to reduce logging)
                response_info = {
                    "info": "Request blocked by Votal guardrails",
                    "blocked_guardrails": blocked_guardrails,
                    "total_blocked": len(blocked_guardrails),
                    "inference_time_ms": validation_result.get("inference_time_ms", 0)
                }

                print(f"🔒 BLOCKED by {len(blocked_guardrails)} guardrails:")
                for g in blocked_guardrails:
                    print(f"   - {g['guardrail']}: {g['message']}")

                raise HTTPException(
                    status_code=400,
                    detail=response_info  # Clean response without nested errors
                )

            return data

        except Exception as e:
            if self.config.block_on_failure:
                raise
            return data

    async def async_post_call_success_hook(self, data: dict, user_api_key_dict: UserAPIKeyAuth, response):
        """Post-API call hook - validate output with guardrails and agentic controls"""
        try:
            # Check if guardrails should be activated
            if not self._should_activate_guardrails(data):
                return response  # Skip guardrails, pass through

            # Check if this is a streaming response
            if self._is_streaming_response(response):
                # Handle streaming with circuit breaker pattern
                print("🌊 Streaming response detected - applying circuit breaker guardrails")
                # Return the async generator for streaming
                async def stream_with_guardrails():
                    async for chunk in self._handle_streaming_response(response, data, user_api_key_dict):
                        yield chunk
                return stream_with_guardrails()

            # Handle non-streaming response (original logic)
            response_content = self._extract_response_content(response)
            if not response_content:
                return response

            # Extract tenant and user context
            tenant_api_key = self._extract_tenant_api_key(data)
            user_role = self._extract_user_role(data)
            agent_id = self._extract_agent_id(data)

            # Check for tool calls (agentic workflow)
            tool_calls = self._extract_tool_calls(response)

            # Validate output through RunPod server (includes agentic validation if tool calls present)
            result = await self._validate_output_with_server(
                content=response_content,
                tenant_api_key=tenant_api_key,
                user_role=user_role,
                agent_id=agent_id,
                tool_calls=tool_calls
            )

            # Handle data sanitization if server provided sanitized content
            if result.get("sanitized_output"):
                self._update_response_content(response, result["sanitized_output"])

            # Handle blocking - return clean guardrail info without excessive logging
            if not result.get("safe", True) and self.config.block_on_failure:
                from fastapi import HTTPException

                # Extract blocked guardrails from guardrail_results
                blocked_guardrails = []
                guardrail_results = result.get("guardrail_results", [])

                for guardrail_result in guardrail_results:
                    if not guardrail_result.get("passed", True):  # Failed guardrails
                        blocked_guardrails.append({
                            "guardrail": guardrail_result.get("guardrail", "unknown"),
                            "message": guardrail_result.get("message", "blocked"),
                            "details": guardrail_result.get("details", {}),
                            "action": guardrail_result.get("action", "block")
                        })

                # Create clean response info (not error to reduce logging)
                response_info = {
                    "info": "Output blocked by Votal guardrails",
                    "blocked_guardrails": blocked_guardrails,
                    "total_blocked": len(blocked_guardrails),
                    "inference_time_ms": result.get("inference_time_ms", 0)
                }

                print(f"🔒 OUTPUT BLOCKED by {len(blocked_guardrails)} guardrails:")
                for g in blocked_guardrails:
                    print(f"   - {g['guardrail']}: {g['message']}")

                raise HTTPException(
                    status_code=400,
                    detail=response_info  # Clean response without nested errors
                )

            return response

        except Exception as e:
            if self.config.block_on_failure:
                raise
            return response

    async def _validate_input_with_server(self, message: str, tenant_api_key: str, user_role: str, agent_id: Optional[str]) -> bool:
        """Validate input message through RunPod server"""
        url = f"{self.config.api_base.rstrip('/')}{self.config.input_endpoint}"

        payload = {"message": message}

        headers = {
            "X-API-Key": tenant_api_key,  # Server uses this to identify tenant and policies
            "X-User-Role": user_role,     # Server uses this for role-based decisions
        }

        if agent_id:
            headers["X-Agent-ID"] = agent_id

        try:
            response = await self.client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                # Get raw response text first
                response_text = response.text

                # Parse only to check safety - avoid re-parsing later
                try:
                    data = response.json()
                    # Store raw response for zero-overhead return if blocked
                    data["_raw_response"] = response_text
                    return data
                except:
                    # If JSON parsing fails, treat as unsafe
                    return {"safe": False, "action": "block", "reason": "Invalid JSON response from server"}
            else:
                return {"safe": False, "action": "block", "reason": f"Server error: {response.status_code}"}

        except Exception as e:
            return {"safe": False, "action": "block", "reason": f"Request failed: {str(e)}"}

    async def _validate_output_with_server(self, content: str, tenant_api_key: str, user_role: str, agent_id: Optional[str], tool_calls: List[Dict] = None) -> Dict:
        """Validate output content through RunPod server (handles agentic validation if tool calls present)"""
        url = f"{self.config.api_base.rstrip('/')}{self.config.output_endpoint}"

        payload = {"output": content}

        # Add agentic context if tool calls are present
        if tool_calls:
            payload["agent_call"] = {
                "agent_id": agent_id or "unknown",
                "tools_called": tool_calls,
                "user_role": user_role
            }

        headers = {
            "X-API-Key": tenant_api_key,  # Server looks up tenant policies
            "X-User-Role": user_role,     # Server applies role-based data sanitization
        }

        if agent_id:
            headers["X-Agent-ID"] = agent_id

        try:
            response = await self.client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                # Get raw response text first for zero-overhead passthrough
                response_text = response.text

                # Parse only to check safety - avoid re-parsing later
                try:
                    data = response.json()
                    # Store raw response for zero-overhead return if blocked
                    data["_raw_response"] = response_text
                    return data
                except:
                    # If JSON parsing fails, treat as unsafe
                    return {"safe": False, "reason": "Invalid JSON response from server"}
            else:
                return {"safe": False, "reason": "Server validation failed"}

        except Exception as e:
            return {"safe": False, "reason": f"Validation error: {str(e)}"}

    def _extract_tenant_api_key(self, data: Dict) -> str:
        """Extract tenant API key from request context"""
        # Look for tenant API key in various locations
        tenant_key = (
            data.get("tenant_api_key") or
            data.get("api_key") or
            data.get("metadata", {}).get("tenant_api_key") or
            data.get("extra_headers", {}).get("X-API-Key") or
            self.config.default_tenant_api_key
        )
        return tenant_key

    def _extract_user_role(self, data: Dict) -> str:
        """Extract user role from request context"""
        role = (
            data.get("user_role") or
            data.get("metadata", {}).get("user_role") or
            data.get("extra_headers", {}).get("X-User-Role") or
            self.config.default_user_role
        )
        return role

    def _extract_agent_id(self, data: Dict) -> Optional[str]:
        """Extract agent ID from request context"""
        agent_id = (
            data.get("agent_id") or
            data.get("metadata", {}).get("agent_id") or
            data.get("extra_headers", {}).get("X-Agent-ID") or
            self.config.default_agent_id
        )
        return agent_id

    def _extract_user_message(self, messages: List[Dict]) -> Optional[str]:
        """Extract user message content from messages array"""
        for message in reversed(messages):  # Get latest user message
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
                elif isinstance(content, list):
                    # Handle multi-modal content
                    text_parts = [part.get("text") for part in content if part.get("type") == "text"]
                    return " ".join(filter(None, text_parts))
        return None

    def _is_streaming_response(self, response) -> bool:
        """Check if the response is a streaming generator"""
        import inspect

        # Check for actual generators first
        if inspect.isgenerator(response) or inspect.isasyncgen(response):
            return True

        # Check for streaming response types (be more specific)
        response_type_name = type(response).__name__

        # Common streaming response indicators
        if any(indicator in response_type_name.lower() for indicator in ['stream', 'generator', 'iterator']):
            return True

        # Check if it's a LiteLLM ModelResponse with streaming=True
        if hasattr(response, '_stream') and response._stream:
            return True

        # Check response object for streaming indicators
        if hasattr(response, 'choices') and response.choices:
            # LiteLLM streaming responses often have delta in choices
            first_choice = response.choices[0]
            if hasattr(first_choice, 'delta'):
                return True

        return False

    async def _handle_streaming_response(self, response, data: dict, user_api_key_dict):
        """Handle streaming response with circuit breaker pattern"""
        import asyncio
        from fastapi import HTTPException

        # Extract tenant context for validation
        tenant_api_key = self._extract_tenant_api_key(data)
        user_role = self._extract_user_role(data)
        agent_id = self._extract_agent_id(data)

        # Buffer for content validation
        content_buffer = []
        chunk_count = 0

        print("🌊 Starting streaming guardrail validation...")

        try:
            # Handle different types of streaming responses
            if hasattr(response, '__aiter__'):
                # Async generator
                async for chunk in response:
                    chunk_count += 1

                    # Extract content from chunk
                    chunk_content = self._extract_chunk_content(chunk)
                    if chunk_content:
                        content_buffer.append(chunk_content)

                    # Validate every N chunks or when we have enough content
                    if chunk_count % 5 == 0 or len(''.join(content_buffer)) > 500:
                        current_content = ''.join(content_buffer)
                        is_safe = await self._validate_streaming_content(
                            current_content, tenant_api_key, user_role, agent_id
                        )

                        if not is_safe:
                            print(f"🔒 CIRCUIT BREAKER: Content blocked after {chunk_count} chunks")
                            # Create clean blocked response
                            blocked_info = {
                                "info": "Streaming output blocked by Votal guardrails",
                                "chunks_processed": chunk_count,
                                "content_length": len(current_content),
                                "reason": "Content validation failed during streaming"
                            }
                            raise HTTPException(status_code=400, detail=blocked_info)

                    # Yield the chunk to continue streaming
                    yield chunk

            elif hasattr(response, '__iter__'):
                # Sync generator - convert to async
                for chunk in response:
                    chunk_count += 1

                    chunk_content = self._extract_chunk_content(chunk)
                    if chunk_content:
                        content_buffer.append(chunk_content)

                    # Same validation logic
                    if chunk_count % 5 == 0 or len(''.join(content_buffer)) > 500:
                        current_content = ''.join(content_buffer)
                        is_safe = await self._validate_streaming_content(
                            current_content, tenant_api_key, user_role, agent_id
                        )

                        if not is_safe:
                            print(f"🔒 CIRCUIT BREAKER: Content blocked after {chunk_count} chunks")
                            blocked_info = {
                                "info": "Streaming output blocked by Votal guardrails",
                                "chunks_processed": chunk_count,
                                "content_length": len(current_content),
                                "reason": "Content validation failed during streaming"
                            }
                            raise HTTPException(status_code=400, detail=blocked_info)

                    yield chunk

            # Final validation after streaming completes
            if content_buffer:
                final_content = ''.join(content_buffer)
                print(f"🏁 Final streaming validation: {len(final_content)} chars, {chunk_count} chunks")

                is_safe = await self._validate_streaming_content(
                    final_content, tenant_api_key, user_role, agent_id
                )

                if not is_safe:
                    print(f"🔒 FINAL BLOCK: Content failed final validation")
                    # At this point streaming is complete, so we log the issue
                    # but can't block the already-sent content

        except HTTPException:
            # Re-raise HTTP exceptions (circuit breaker triggers)
            raise
        except Exception as e:
            print(f"⚠️  Streaming validation error: {e}")
            # Don't break the stream for validation errors
            if self.config.block_on_failure:
                raise
            # Continue with original response if validation fails

    def _extract_chunk_content(self, chunk) -> Optional[str]:
        """Extract text content from a streaming chunk"""
        try:
            # Handle different chunk formats
            if hasattr(chunk, 'choices') and chunk.choices:
                choice = chunk.choices[0]
                if hasattr(choice, 'delta') and hasattr(choice.delta, 'content'):
                    return choice.delta.content
            elif isinstance(chunk, dict):
                # Handle dict format chunks
                choices = chunk.get('choices', [])
                if choices:
                    delta = choices[0].get('delta', {})
                    return delta.get('content')
            elif isinstance(chunk, str):
                return chunk
            return None
        except Exception:
            return None

    async def _validate_streaming_content(self, content: str, tenant_api_key: str, user_role: str, agent_id: str) -> bool:
        """Validate streaming content - returns True if safe, False if should block"""
        try:
            if not content.strip():
                return True  # Empty content is safe

            result = await self._validate_output_with_server(
                content=content,
                tenant_api_key=tenant_api_key,
                user_role=user_role,
                agent_id=agent_id,
                tool_calls=None
            )

            return result.get("safe", True)

        except Exception as e:
            print(f"⚠️  Streaming validation error: {e}")
            # If validation fails, default to safe (don't break stream)
            return True

    def _extract_response_content(self, response_obj) -> Optional[str]:
        """Extract response content from LiteLLM response object"""
        try:
            if hasattr(response_obj, "choices") and response_obj.choices:
                choice = response_obj.choices[0]
                if hasattr(choice, "message") and hasattr(choice.message, "content"):
                    return choice.message.content
            return None
        except Exception as e:
            return None

    def _extract_tool_calls(self, response_obj) -> List[Dict]:
        """Extract tool calls from response object for agentic validation"""
        try:
            tool_calls = []
            if hasattr(response_obj, "choices") and response_obj.choices:
                choice = response_obj.choices[0]
                if hasattr(choice, "message") and hasattr(choice.message, "tool_calls"):
                    if choice.message.tool_calls:
                        for tool_call in choice.message.tool_calls:
                            tool_calls.append({
                                "id": getattr(tool_call, "id", None),
                                "type": getattr(tool_call, "type", "function"),
                                "function": {
                                    "name": tool_call.function.name,
                                    "arguments": tool_call.function.arguments
                                }
                            })
            return tool_calls
        except Exception as e:
            return []

    def _update_response_content(self, response_obj, sanitized_content: str):
        """Update response object with sanitized content from server"""
        try:
            if hasattr(response_obj, "choices") and response_obj.choices:
                choice = response_obj.choices[0]
                if hasattr(choice, "message") and hasattr(choice.message, "content"):
                    choice.message.content = sanitized_content
        except Exception as e:
            pass

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.client.aclose()


# Factory function for easy integration
def create_votal_guardrail(
    api_base: str,
    api_token: str,
    default_tenant_api_key: str = "basic-api-key-12345",
    default_user_role: str = "user",
    default_agent_id: Optional[str] = None,
    **kwargs
) -> VotalGuardrail:
    """Factory function to create Votal guardrail integration

    Args:
        api_base: Base URL for RunPod server (e.g., "https://kk5losqxwr2ui7.api.runpod.ai")
        api_token: RunPod token for API authentication
        default_tenant_api_key: Default tenant API key (policies are stored on server per tenant)
        default_user_role: Default user role for requests
        default_agent_id: Default agent identifier for agentic workflows
        **kwargs: Additional configuration options

    Returns:
        VotalGuardrail instance ready for use with LiteLLM

    Example:
        # Simple setup - server handles all policies
        guardrail = create_votal_guardrail(
            api_base="https://kk5losqxwr2ui7.api.runpod.ai",
            api_token=os.getenv("RUNPOD_TOKEN"),
            default_tenant_api_key="healthcare-tenant-key-123",
            default_user_role="nurse",
            default_agent_id="healthcare-assistant"
        )

        # LiteLLM will extract actual tenant info from each request context
    """
    config = GuardrailConfig(
        api_base=api_base,
        api_token=api_token,
        default_tenant_api_key=default_tenant_api_key,
        default_user_role=default_user_role,
        default_agent_id=default_agent_id,
        **kwargs
    )

    return VotalGuardrail(config)


# Export main classes and functions
__all__ = [
    "VotalGuardrail",
    "GuardrailConfig",
    "create_votal_guardrail"
]