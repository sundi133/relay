"""
Tests for config loader and the FastAPI proxy server.
"""
import pytest
import respx
import httpx
from pathlib import Path
from fastapi.testclient import TestClient

from unillm.config import load, RelayConfig, ModelEntry
from unillm.server import create_app


# ── Config loader ─────────────────────────────────────────────────────────────

def test_load_basic(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
model_list:
  - model_name: my-qwen
    litellm_params:
      model: qwen/qwen-turbo
      api_key: sk-test

  - model_name: local-llama
    litellm_params:
      model: ollama/llama3.2

general_settings:
  master_key: sk-relay-secret
  request_timeout: 60
""")
    config = load(cfg_file)
    assert len(config.models) == 2
    assert config.models[0].model_name == "my-qwen"
    assert config.models[0].model == "qwen/qwen-turbo"
    assert config.models[0].api_key == "sk-test"
    assert config.models[1].api_key is None
    assert config.master_key == "sk-relay-secret"
    assert config.request_timeout == 60


def test_load_env_var_key(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TEST_KEY", "sk-from-env")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
model_list:
  - model_name: openai-gpt
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/MY_TEST_KEY
""")
    config = load(cfg_file)
    assert config.models[0].api_key == "sk-from-env"


def test_load_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nonexistent.yaml")


def test_model_for_lookup(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
model_list:
  - model_name: fast
    litellm_params:
      model: deepseek/deepseek-chat
""")
    config = load(cfg_file)
    entry = config.model_for("fast")
    assert entry is not None
    assert entry.model == "deepseek/deepseek-chat"
    assert config.model_for("nonexistent") is None


# ── Server endpoints ──────────────────────────────────────────────────────────

def _make_config():
    return RelayConfig(
        models=[
            ModelEntry(model_name="my-qwen",  model="qwen/qwen-turbo"),
            ModelEntry(model_name="my-ollama", model="ollama/llama3.2"),
        ],
        master_key=None,
    )


def test_health():
    client = TestClient(create_app(_make_config()))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_list_models():
    client = TestClient(create_app(_make_config()))
    r = client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["data"]]
    assert "my-qwen" in ids
    assert "my-ollama" in ids


def test_auth_blocks_without_key():
    config = RelayConfig(
        models=[ModelEntry(model_name="m", model="qwen/qwen-turbo")],
        master_key="sk-secret",
    )
    client = TestClient(create_app(config))
    r = client.post("/v1/chat/completions", json={
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 401


def test_auth_passes_with_correct_key():
    config = RelayConfig(
        models=[ModelEntry(model_name="m", model="qwen/qwen-turbo")],
        master_key="sk-secret",
    )
    client = TestClient(create_app(config), raise_server_exceptions=False)
    # Will fail at HTTP call (no real API), but auth should pass (not 401)
    r = client.post(
        "/v1/chat/completions",
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-secret"},
    )
    assert r.status_code != 401


def test_unknown_model_returns_404():
    client = TestClient(create_app(_make_config()), raise_server_exceptions=False)
    r = client.post("/v1/chat/completions", json={
        "model": "nonexistent-model",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 404


@respx.mock
def test_chat_completions_success():
    respx.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    ).mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": "你好！"}}],
        "model": "qwen-turbo",
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }))

    client = TestClient(create_app(_make_config()))
    r = client.post("/v1/chat/completions", json={
        "model": "my-qwen",
        "messages": [{"role": "user", "content": "Hello"}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "你好！"
    assert body["object"] == "chat.completion"
    assert body["relay"]["target"] == "qwen/qwen-turbo"


@respx.mock
def test_direct_provider_model_string():
    """Callers can bypass config and use 'provider/model' directly."""
    respx.post(
        "https://api.deepseek.com/v1/chat/completions"
    ).mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
        "model": "deepseek-chat",
        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
    }))

    client = TestClient(create_app(_make_config()))
    r = client.post("/v1/chat/completions", json={
        "model": "deepseek/deepseek-chat",   # direct passthrough
        "messages": [{"role": "user", "content": "Hi"}],
    })
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Hello!"


def test_usage_endpoint():
    client = TestClient(create_app(_make_config()))
    r = client.get("/v1/usage")
    assert r.status_code == 200
    assert "total_calls" in r.json()
    assert "total_cost_usd" in r.json()
