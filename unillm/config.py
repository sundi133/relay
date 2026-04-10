"""
Config loader — reads config.yaml in litellm-compatible format.

Example config.yaml:
    model_list:
      - model_name: gpt-4o            # alias callers use in API requests
        litellm_params:
          model: openai/gpt-4o        # relay provider/model string
          api_key: sk-...             # optional, falls back to env var

      - model_name: qwen              # caller uses "qwen" as the model name
        litellm_params:
          model: qwen/qwen-turbo
          api_key: sk-dashscope-...

      - model_name: local-llama
        litellm_params:
          model: ollama/llama3.2      # no api_key needed for local

    general_settings:
      master_key: sk-relay-secret     # optional: require Bearer auth on all requests
      request_timeout: 120
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

import yaml


@dataclass
class GuardrailEntry:
    guardrail_name: str
    guardrail_class: str     # e.g. "unillm.votal_guardrail_relay.VotalGuardrail"
    mode: str                # "pre_call", "post_call", or "both"
    enabled: bool = True
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelEntry:
    model_name: str          # alias exposed in the API  (e.g. "gpt-4o")
    model: str               # relay provider/model      (e.g. "openai/gpt-4o")
    api_key: Optional[str] = None
    guardrails: Optional[List[str]] = None  # List of guardrail names to apply


@dataclass
class RelayConfig:
    models: list[ModelEntry] = field(default_factory=list)
    master_key: Optional[str] = None
    request_timeout: int = 120
    guardrails: list[GuardrailEntry] = field(default_factory=list)
    guardrails_config: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Build lookup caches for performance (O(1) instead of O(n))"""
        self._model_cache = {m.model_name: m for m in self.models}
        self._guardrail_cache = {g.guardrail_name: g for g in self.guardrails}

    def model_for(self, name: str) -> Optional[ModelEntry]:
        """Look up a ModelEntry by its public alias (cached O(1) lookup)."""
        return self._model_cache.get(name)

    def guardrail_for(self, name: str) -> Optional[GuardrailEntry]:
        """Look up a GuardrailEntry by its name (cached O(1) lookup)."""
        return self._guardrail_cache.get(name)

    @property
    def model_names(self) -> list[str]:
        return [m.model_name for m in self.models]

    @property
    def guardrail_names(self) -> list[str]:
        return [g.guardrail_name for g in self.guardrails]


def load(path: str | Path) -> RelayConfig:
    """Parse a config.yaml and return a RelayConfig."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError(f"Config file is empty: {path}")

    models: list[ModelEntry] = []
    for entry in raw.get("model_list", []):
        params = entry.get("litellm_params", {})
        model_str = params.get("model", "")
        if not model_str:
            raise ValueError(f"Missing 'model' in litellm_params for: {entry}")

        # Expand env vars in api_key  (e.g. "os.environ/OPENAI_API_KEY")
        api_key = params.get("api_key")
        if isinstance(api_key, str) and api_key.startswith("os.environ/"):
            env_var = api_key.split("/", 1)[1]
            api_key = os.environ.get(env_var)

        # Parse guardrails for this model (optional)
        model_guardrails = entry.get("guardrails")
        if isinstance(model_guardrails, str):
            model_guardrails = [model_guardrails]

        models.append(ModelEntry(
            model_name=entry["model_name"],
            model=model_str,
            api_key=api_key,
            guardrails=model_guardrails,
        ))

    # Parse guardrails configuration
    guardrails: list[GuardrailEntry] = []
    guardrails_section = raw.get("guardrails", [])

    for guardrail_entry in guardrails_section:
        guardrail_name = guardrail_entry.get("guardrail_name")
        if not guardrail_name:
            continue

        params = guardrail_entry.get("litellm_params", {})  # Keep litellm_params for compatibility

        # Extract guardrail configuration
        guardrail_class = params.get("guardrail", "")
        mode = params.get("mode", "both")
        enabled = params.get("default_on", True)

        # Get additional config from the params
        config = {k: v for k, v in params.items()
                 if k not in ["guardrail", "mode", "default_on"]}

        guardrails.append(GuardrailEntry(
            guardrail_name=guardrail_name,
            guardrail_class=guardrail_class,
            mode=mode,
            enabled=enabled,
            config=config,
        ))

    # Parse guardrails global configuration (e.g., votal_guardrail section)
    guardrails_config = {}
    for key, value in raw.items():
        if key.endswith("_guardrail") or key == "votal_guardrail":
            guardrails_config[key] = value

    gs = raw.get("general_settings", {})

    # Use RELAY_KEY or RELAY_BEARER_TOKEN environment variable if set, otherwise fall back to config
    master_key = (
        os.environ.get("RELAY_KEY") or
        os.environ.get("RELAY_BEARER_TOKEN") or
        gs.get("master_key")
    )

    return RelayConfig(
        models=models,
        master_key=master_key,
        request_timeout=int(gs.get("request_timeout", 120)),
        guardrails=guardrails,
        guardrails_config=guardrails_config,
    )
