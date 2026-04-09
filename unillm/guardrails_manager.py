"""
Relay Guardrails Manager
=======================

Manages guardrails loading, configuration, and execution for Relay.
"""

import importlib
from typing import Dict, List, Any, Optional
from .guardrails_base import BaseGuardrail, GuardrailContext, GuardrailManager
from .config import RelayConfig, GuardrailEntry


class RelayGuardrailManager(GuardrailManager):
    """Enhanced guardrail manager for Relay with config loading"""

    def __init__(self, config: RelayConfig):
        super().__init__()
        self.config = config
        self.guardrail_instances: Dict[str, BaseGuardrail] = {}
        self.model_guardrails: Dict[str, List[str]] = {}  # model_name -> guardrail_names

    async def initialize(self):
        """Initialize all configured guardrails"""
        for guardrail_entry in self.config.guardrails:
            if not guardrail_entry.enabled:
                continue

            # Load guardrail class
            guardrail_instance = await self._load_guardrail(guardrail_entry)
            if guardrail_instance:
                self.guardrail_instances[guardrail_entry.guardrail_name] = guardrail_instance

                # Register with appropriate hooks
                if guardrail_entry.mode in ["pre_call", "both"]:
                    self.add_input_guardrail(guardrail_instance)

                if guardrail_entry.mode in ["post_call", "both"]:
                    self.add_output_guardrail(guardrail_instance)

                print(f"✅ Loaded guardrail: {guardrail_entry.guardrail_name} ({guardrail_entry.mode})")

        # Build model -> guardrail mapping
        for model_entry in self.config.models:
            if model_entry.guardrails:
                self.model_guardrails[model_entry.model_name] = model_entry.guardrails

        print(f"🔒 Initialized {len(self.guardrail_instances)} guardrails")

    async def _load_guardrail(self, entry: GuardrailEntry) -> Optional[BaseGuardrail]:
        """Load a guardrail class from its module path"""
        try:
            # Parse module and class name
            if "." in entry.guardrail_class:
                module_path, class_name = entry.guardrail_class.rsplit(".", 1)
            else:
                # Assume it's in the current package
                module_path = f"unillm.{entry.guardrail_class.lower()}"
                class_name = entry.guardrail_class

            # Import the module
            module = importlib.import_module(module_path)
            guardrail_class = getattr(module, class_name)

            # Get configuration
            guardrail_config = entry.config.copy()

            # Merge with global config if available
            global_config_key = f"{entry.guardrail_name.split('-')[0]}_guardrail"
            if global_config_key in self.config.guardrails_config:
                global_config = self.config.guardrails_config[global_config_key]
                guardrail_config.update(global_config)

            # Create instance
            return guardrail_class(config=guardrail_config, name=entry.guardrail_name)

        except Exception as e:
            print(f"❌ Failed to load guardrail {entry.guardrail_name}: {e}")
            return None

    def get_model_guardrails(self, model_name: str) -> List[str]:
        """Get guardrail names configured for a specific model"""
        return self.model_guardrails.get(model_name, [])

    async def validate_for_model(
        self,
        model_name: str,
        context: GuardrailContext,
        messages: List[Dict[str, Any]] = None,
        response_content: str = None,
        full_response: Dict[str, Any] = None,
        mode: str = "input"
    ) -> Dict[str, Any]:
        """Validate using only the guardrails configured for a specific model"""

        model_guardrail_names = self.get_model_guardrails(model_name)
        if not model_guardrail_names:
            # No specific guardrails for this model, use all
            if mode == "input" and messages is not None:
                return await self.validate_input(context, messages)
            elif mode == "output" and response_content is not None:
                return await self.validate_output(context, response_content, full_response or {})
            else:
                return {"allowed": True}

        # Create temporary manager with only model-specific guardrails
        temp_manager = GuardrailManager()

        for guardrail_name in model_guardrail_names:
            if guardrail_name in self.guardrail_instances:
                guardrail = self.guardrail_instances[guardrail_name]
                guardrail_entry = self.config.guardrail_for(guardrail_name)

                if guardrail_entry:
                    if mode == "input" and guardrail_entry.mode in ["pre_call", "both"]:
                        temp_manager.add_input_guardrail(guardrail)
                    elif mode == "output" and guardrail_entry.mode in ["post_call", "both"]:
                        temp_manager.add_output_guardrail(guardrail)

        # Run validation with model-specific guardrails
        if mode == "input" and messages is not None:
            return await temp_manager.validate_input(context, messages)
        elif mode == "output" and response_content is not None:
            return await temp_manager.validate_output(context, response_content, full_response or {})
        else:
            return {"allowed": True}

    async def shutdown(self):
        """Clean shutdown of all guardrails"""
        for guardrail in self.guardrail_instances.values():
            if hasattr(guardrail, '__aexit__'):
                try:
                    await guardrail.__aexit__(None, None, None)
                except:
                    pass