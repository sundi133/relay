"""
Relay Guardrails Base Classes
============================

Base classes and types for implementing guardrails in Relay.
Replaces LiteLLM-specific dependencies with relay-compatible interfaces.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass


@dataclass
class GuardrailContext:
    """Context object passed to guardrails during request processing"""
    headers: Dict[str, str]
    model: str
    messages: List[Dict[str, Any]]
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseGuardrail(ABC):
    """Base class for all relay guardrails"""

    def __init__(self, config: Dict[str, Any] = None, **kwargs):
        self.config = config or {}
        self.name = kwargs.get('name', self.__class__.__name__)

    @abstractmethod
    async def validate_input(
        self,
        context: GuardrailContext,
        messages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Validate input messages before sending to LLM

        Returns:
            {
                "allowed": bool,
                "modified_messages": List[Dict] (optional),
                "reason": str (if blocked),
                "metadata": Dict (optional)
            }
        """
        pass

    @abstractmethod
    async def validate_output(
        self,
        context: GuardrailContext,
        response_content: str,
        full_response: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate output response before sending to client

        Returns:
            {
                "allowed": bool,
                "modified_content": str (optional),
                "reason": str (if blocked),
                "metadata": Dict (optional)
            }
        """
        pass

    async def validate_streaming_chunk(
        self,
        context: GuardrailContext,
        chunk_content: str,
        accumulated_content: str
    ) -> Dict[str, Any]:
        """
        Validate streaming content chunk

        Returns:
            {
                "allowed": bool,
                "stop_stream": bool (optional),
                "reason": str (if blocked),
                "metadata": Dict (optional)
            }
        """
        # Default implementation - allow all chunks
        return {"allowed": True}


class GuardrailManager:
    """Manages multiple guardrails and their execution"""

    def __init__(self):
        self.input_guardrails: List[BaseGuardrail] = []
        self.output_guardrails: List[BaseGuardrail] = []

    def add_input_guardrail(self, guardrail: BaseGuardrail):
        """Add an input guardrail"""
        self.input_guardrails.append(guardrail)

    def add_output_guardrail(self, guardrail: BaseGuardrail):
        """Add an output guardrail"""
        self.output_guardrails.append(guardrail)

    async def validate_input(
        self,
        context: GuardrailContext,
        messages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Run all input guardrails"""
        current_messages = messages.copy()

        for guardrail in self.input_guardrails:
            try:
                result = await guardrail.validate_input(context, current_messages)

                if not result.get("allowed", True):
                    return {
                        "allowed": False,
                        "reason": result.get("reason", "Blocked by input guardrail"),
                        "guardrail": guardrail.name,
                        "metadata": result.get("metadata", {})
                    }

                # Update messages if modified
                if "modified_messages" in result:
                    current_messages = result["modified_messages"]

            except Exception as e:
                return {
                    "allowed": False,
                    "reason": f"Guardrail error: {str(e)}",
                    "guardrail": guardrail.name,
                    "error": True
                }

        return {
            "allowed": True,
            "messages": current_messages
        }

    async def validate_output(
        self,
        context: GuardrailContext,
        response_content: str,
        full_response: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run all output guardrails"""
        current_content = response_content

        for guardrail in self.output_guardrails:
            try:
                result = await guardrail.validate_output(
                    context, current_content, full_response
                )

                if not result.get("allowed", True):
                    return {
                        "allowed": False,
                        "reason": result.get("reason", "Blocked by output guardrail"),
                        "guardrail": guardrail.name,
                        "metadata": result.get("metadata", {})
                    }

                # Update content if modified
                if "modified_content" in result:
                    current_content = result["modified_content"]

            except Exception as e:
                return {
                    "allowed": False,
                    "reason": f"Guardrail error: {str(e)}",
                    "guardrail": guardrail.name,
                    "error": True
                }

        return {
            "allowed": True,
            "content": current_content
        }