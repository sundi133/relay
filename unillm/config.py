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
from typing import Optional

import yaml


@dataclass
class ModelEntry:
    model_name: str          # alias exposed in the API  (e.g. "gpt-4o")
    model: str               # relay provider/model      (e.g. "openai/gpt-4o")
    api_key: Optional[str] = None


@dataclass
class RelayConfig:
    models: list[ModelEntry] = field(default_factory=list)
    master_key: Optional[str] = None
    request_timeout: int = 120

    def model_for(self, name: str) -> Optional[ModelEntry]:
        """Look up a ModelEntry by its public alias."""
        for m in self.models:
            if m.model_name == name:
                return m
        return None

    @property
    def model_names(self) -> list[str]:
        return [m.model_name for m in self.models]


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

        models.append(ModelEntry(
            model_name=entry["model_name"],
            model=model_str,
            api_key=api_key,
        ))

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
    )
