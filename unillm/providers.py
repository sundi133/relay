"""
Provider registry for UniLLM.
Add any OpenAI-compatible endpoint in ~3 lines.
"""
from __future__ import annotations
import os

# ---------------------------------------------------------------------------
# Cost table: (input $/1M tokens, output $/1M tokens)
# ---------------------------------------------------------------------------
COST_TABLE: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o":                  (5.00,  15.00),
    "gpt-4o-mini":             (0.15,   0.60),
    "gpt-4-turbo":            (10.00,  30.00),
    "o1":                     (15.00,  60.00),
    "o1-mini":                 (3.00,  12.00),
    # Anthropic
    "claude-opus-4-20250514":  (15.00,  75.00),
    "claude-sonnet-4-20250514":(3.00,  15.00),
    "claude-haiku-4-5-20251001":(0.80,   4.00),
    # Qwen
    "qwen-turbo":              (0.05,   0.20),
    "qwen-plus":               (0.40,   1.20),
    "qwen-max":                (2.40,   9.60),
    "qwen2.5-72b-instruct":    (0.56,   2.24),
    # GLM / Zhipu
    "glm-4":                   (0.10,   0.10),
    "glm-4-flash":             (0.00,   0.00),  # free tier
    "glm-4v":                  (0.10,   0.10),
    # Kimi / Moonshot
    "moonshot-v1-8k":          (0.17,   0.17),
    "moonshot-v1-32k":         (0.34,   0.34),
    "moonshot-v1-128k":        (1.36,   1.36),
    # DeepSeek
    "deepseek-chat":           (0.14,   0.28),
    "deepseek-reasoner":       (0.55,   2.19),
    # Yi
    "yi-lightning":            (0.014,  0.014),
    "yi-large":                (3.00,   3.00),
    # Mistral
    "mistral-large-latest":    (2.00,   6.00),
    "mistral-small-latest":    (0.20,   0.60),
    "codestral-latest":        (0.20,   0.60),
    # Gemini
    "gemini-2.0-flash":        (0.10,   0.40),
    "gemini-1.5-pro":          (1.25,   5.00),
    # Self-hosted = free
    "__local__":               (0.00,   0.00),
}


def _cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    row = COST_TABLE.get(model_id, COST_TABLE.get("__local__", (0.0, 0.0)))
    return (prompt_tokens * row[0] + completion_tokens * row[1]) / 1_000_000


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, dict] = {
    # ── Major Cloud ──────────────────────────────────────────────────────
    "openai": {
        "base": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY",
        "style": "openai",
    },
    "anthropic": {
        "base": "https://api.anthropic.com/v1",
        "key_env": "ANTHROPIC_API_KEY",
        "style": "anthropic",
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "key_env": "GEMINI_API_KEY",
        "style": "gemini",
    },

    # ── Chinese / Open-source Cloud ──────────────────────────────────────
    "qwen": {                               # Alibaba DashScope
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY",
        "style": "openai",
    },
    "glm": {                                # Zhipu AI
        "base": "https://open.bigmodel.cn/api/paas/v4",
        "key_env": "ZHIPU_API_KEY",
        "style": "openai",
    },
    "kimi": {                               # Moonshot AI
        "base": "https://api.moonshot.cn/v1",
        "key_env": "MOONSHOT_API_KEY",
        "style": "openai",
    },
    "deepseek": {
        "base": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
        "style": "openai",
    },
    "yi": {                                 # 01.AI
        "base": "https://api.lingyiwanwu.com/v1",
        "key_env": "YI_API_KEY",
        "style": "openai",
    },
    "mistral": {
        "base": "https://api.mistral.ai/v1",
        "key_env": "MISTRAL_API_KEY",
        "style": "openai",
    },
    "baichuan": {
        "base": "https://api.baichuan-ai.com/v1",
        "key_env": "BAICHUAN_API_KEY",
        "style": "openai",
    },
    "minimax": {
        "base": "https://api.minimax.chat/v1",
        "key_env": "MINIMAX_API_KEY",
        "style": "openai",
    },

    # ── Self-hosted ──────────────────────────────────────────────────────
    "ollama": {
        "base": lambda: os.getenv("OLLAMA_BASE", "http://localhost:11434/v1"),
        "key_env": None,
        "style": "openai",
        "local": True,
    },
    "vllm": {
        "base": lambda: os.getenv("VLLM_BASE", "http://localhost:8000/v1"),
        "key_env": None,
        "style": "openai",
        "local": True,
    },
    "llamacpp": {
        "base": lambda: os.getenv("LLAMACPP_BASE", "http://localhost:8080/v1"),
        "key_env": None,
        "style": "openai",
        "local": True,
    },
    "tgi": {                                # HuggingFace TGI
        "base": lambda: os.getenv("TGI_BASE", "http://localhost:8080/v1"),
        "key_env": None,
        "style": "openai",
        "local": True,
    },
}


def get(name: str) -> dict:
    cfg = _REGISTRY.get(name)
    if cfg is None:
        raise ValueError(
            f"Unknown provider '{name}'. "
            f"Available: {list(_REGISTRY)}. "
            "Or register a custom one with unillm.register_provider()"
        )
    # Resolve lazy base URLs (lambdas for self-hosted that read env at call time)
    base = cfg["base"]
    if callable(base):
        cfg = {**cfg, "base": base()}
    return cfg


def register(name: str, base_url: str, *, api_key: str | None = None,
             key_env: str | None = None) -> None:
    """Register any OpenAI-compatible provider at runtime."""
    _REGISTRY[name] = {
        "base": base_url,
        "key_env": key_env,
        "_key": api_key,
        "style": "openai",
    }


def list_providers() -> list[str]:
    return list(_REGISTRY.keys())
