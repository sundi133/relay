# relay

A minimal, **supply-chain-safe** LLM proxy and router for Python.  
Drop-in replacement for LiteLLM — same config format, same CLI, same API.

> **Why relay?**  
> [LiteLLM was compromised in a supply chain attack (March 2026)](https://docs.litellm.ai/blog/security-update-march-2026) — a stolen PyPI token from an unpinned CI dependency was used to inject credential-stealing malware into 119,000+ installs. Relay is a clean-room replacement built with that attack in mind.

| | LiteLLM | Relay |
|---|---|---|
| Runtime deps | 100+ transitive | `httpx`, `fastapi`, `uvicorn`, `pyyaml` |
| PyPI publishing | Stored API token *(stolen)* | OIDC Trusted Publisher — no token |
| CI dependencies | Unpinned `trivy` *(hijacked)* | All actions pinned to commit SHA |
| Install-time execution | `.pth` ran on every Python start | Nothing runs on install |
| Codebase | ~50,000 lines | ~600 lines — auditable in one sitting |
| Cost tracking | In-memory only | SQLite — persists across restarts |

---

## Install

```bash
pip install relay-llm
```

---

## Start the proxy (identical to LiteLLM)

```bash
relay --config config.yaml --port 4000
```

```
  ██████  ███████ ██      █████  ██    ██
  ██   ██ ██      ██     ██   ██  ██  ██
  ██████  █████   ██     ███████   ████
  ██   ██ ██      ██     ██   ██    ██
  ██   ██ ███████ ██████ ██   ██    ██

  Relay LLM Proxy  — supply-chain-safe LiteLLM drop-in

  Config      : /your/path/config.yaml
  Models      : gpt-4o, qwen-turbo, glm-4, llama3, ...
  Auth        : master_key set ✓
  Listening   : http://0.0.0.0:4000

  Endpoints:
    GET  /health
    GET  /v1/models
    POST /v1/chat/completions
    GET  /v1/usage
```

### CLI options

```bash
relay --config config.yaml --port 4000
relay --config config.yaml --port 4000 --host 0.0.0.0
relay --config config.yaml --port 4000 --workers 4
relay --config config.yaml --port 4000 --log-level warning
relay --version
```

---

## config.yaml

Same format as LiteLLM — copy your existing config straight over.

```yaml
model_list:
  # OpenAI
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY    # reads from env var

  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY

  # Anthropic
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY

  # Qwen (Alibaba DashScope)
  - model_name: qwen-turbo
    litellm_params:
      model: qwen/qwen-turbo
      api_key: os.environ/DASHSCOPE_API_KEY

  - model_name: qwen-max
    litellm_params:
      model: qwen/qwen-max
      api_key: os.environ/DASHSCOPE_API_KEY

  # GLM (Zhipu AI)
  - model_name: glm-4
    litellm_params:
      model: glm/glm-4
      api_key: os.environ/ZHIPU_API_KEY

  # Kimi (Moonshot AI)
  - model_name: kimi
    litellm_params:
      model: kimi/moonshot-v1-8k
      api_key: os.environ/MOONSHOT_API_KEY

  # DeepSeek
  - model_name: deepseek
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/DEEPSEEK_API_KEY

  # Self-hosted via Ollama (no key needed)
  - model_name: llama3
    litellm_params:
      model: ollama/llama3.2

  - model_name: qwen-local
    litellm_params:
      model: ollama/qwen2.5:7b

  # Self-hosted via vLLM
  - model_name: qwen-72b
    litellm_params:
      model: vllm/Qwen2.5-72B-Instruct

general_settings:
  master_key: sk-relay-secret-change-me   # set to null to disable auth
  request_timeout: 120
```

---

## Supported Providers

| Shortname    | Provider           | Example models                                | Env var               |
|--------------|--------------------|-----------------------------------------------|-----------------------|
| `openai`     | OpenAI             | gpt-4o, gpt-4o-mini, o1                      | `OPENAI_API_KEY`      |
| `anthropic`  | Anthropic          | claude-opus-4, claude-sonnet-4               | `ANTHROPIC_API_KEY`   |
| `gemini`     | Google             | gemini-2.0-flash, gemini-1.5-pro             | `GEMINI_API_KEY`      |
| `qwen`       | Alibaba DashScope  | qwen-turbo, qwen-plus, qwen-max, qwen2.5-*  | `DASHSCOPE_API_KEY`   |
| `glm`        | Zhipu AI           | glm-4, glm-4-flash, glm-4v                  | `ZHIPU_API_KEY`       |
| `kimi`       | Moonshot AI        | moonshot-v1-8k/32k/128k                      | `MOONSHOT_API_KEY`    |
| `deepseek`   | DeepSeek           | deepseek-chat, deepseek-reasoner             | `DEEPSEEK_API_KEY`    |
| `yi`         | 01.AI              | yi-lightning, yi-large                       | `YI_API_KEY`          |
| `mistral`    | Mistral AI         | mistral-large-latest, codestral             | `MISTRAL_API_KEY`     |
| `baichuan`   | Baichuan           | Baichuan4                                    | `BAICHUAN_API_KEY`    |
| `minimax`    | MiniMax            | abab6.5-chat                                 | `MINIMAX_API_KEY`     |
| `ollama`     | Self-hosted        | any model you've pulled                      | none                  |
| `vllm`       | Self-hosted        | any HuggingFace model                        | none                  |
| `llamacpp`   | Self-hosted        | any GGUF model                               | none                  |
| `tgi`        | HuggingFace TGI    | any TGI-served model                         | none                  |

---

## API Endpoints

### `POST /v1/chat/completions`

Fully OpenAI-compatible. Accepts model aliases from `config.yaml` or direct `provider/model` strings.

```bash
# Using a config alias
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-relay-secret" \
  -d '{
    "model": "qwen-turbo",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# Using a provider/model string directly (no config entry needed)
curl http://localhost:4000/v1/chat/completions \
  -d '{"model": "deepseek/deepseek-chat", "messages": [...]}'

# Streaming
curl http://localhost:4000/v1/chat/completions \
  -d '{"model": "glm-4", "messages": [...], "stream": true}'
```

### `GET /v1/models`

Lists all models defined in your `config.yaml`.

```bash
curl http://localhost:4000/v1/models
```

### `GET /v1/usage`

Relay extension — live cost and token stats, backed by SQLite.

```bash
curl http://localhost:4000/v1/usage
```

```json
{
  "total_calls": 1847,
  "total_tokens": 2304100,
  "total_cost_usd": 0.23041,
  "db_path": "/home/you/.unillm/usage.db",
  "per_model": {
    "deepseek/deepseek-chat": {
      "calls": 1200, "total_tokens": 1500000,
      "cost_usd": 0.168, "avg_latency_ms": 280, "errors": 0
    }
  }
}
```

---

## Use with the OpenAI SDK

Point `base_url` at relay — everything else is unchanged.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:4000/v1",
    api_key="sk-relay-secret",       # your master_key from config.yaml
)

resp = client.chat.completions.create(
    model="qwen-turbo",              # model_name from config.yaml
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

---

## Use as a Python library

```python
import asyncio
import unillm

messages = [{"role": "user", "content": "Tell me a joke"}]

async def main():
    # Single call
    resp = await unillm.completion("qwen/qwen-turbo", messages)
    print(resp.content)
    print(resp.usage)   # {"prompt_tokens": 12, "completion_tokens": 38, "cost_usd": 0.0000025}

    # Streaming
    async for chunk in await unillm.stream("glm/glm-4", messages):
        print(chunk, end="", flush=True)

    # Fallback chain — tries cheapest first, escalates on failure
    from unillm import FallbackRouter
    router = FallbackRouter([
        "deepseek/deepseek-chat",
        "qwen/qwen-plus",
        "openai/gpt-4o-mini",
        "ollama/llama3.2",
    ])
    resp = await router.completion(messages)
    print(f"Served by: {resp['_used_model']}")

    # Register any custom OpenAI-compatible endpoint
    unillm.register_provider("myserver", "http://my-server:8000/v1", api_key="sk-x")
    resp = await unillm.completion("myserver/my-model", messages)

asyncio.run(main())
```

---

## Cost Tracking (persists across restarts)

Every call is written to `~/.unillm/usage.db` (SQLite). Totals reload automatically when the process starts — no data lost on restart.

```python
import unillm

print(unillm.tracker.total_calls)       # lifetime total
print(unillm.tracker.total_cost_usd)
print(unillm.tracker.summary(detailed=True))
```

```
╔══════════════════════════════════════════════════╗
║              UniLLM Usage Summary                 ║
╚══════════════════════════════════════════════════╝
  DB            : /home/you/.unillm/usage.db
  Session start : 2026-04-08 10:00:00 UTC
  Total calls   : 1,847  (lifetime)
  Total tokens  : 2,304,100
  Total cost    : $0.230410

  Per-model breakdown (lifetime):
  Model                               Calls     Tokens     Cost ($)   Avg ms  Errors
  deepseek/deepseek-chat               1200    1,500,000    0.168000      280       0
  qwen/qwen-turbo                       500      600,000    0.030000      310       0
  ollama/llama3.2                       147      204,100    0.000000      890       0

  Daily cost (last 7 days):
  Date           Calls     Tokens     Cost ($)
  2026-04-08       312    420,300    0.047163
  2026-04-07       288    390,100    0.043811
```

```python
# Query history
unillm.tracker.history(limit=20)
unillm.tracker.history(model="qwen/qwen-turbo", since="2026-04-01")
unillm.tracker.history(errors_only=True)
unillm.tracker.daily_cost()

# Change DB location
import os; os.environ["UNILLM_DB"] = "./myproject.db"

# Clear memory, keep DB history
unillm.tracker.reset_session()

# Wipe everything (irreversible)
unillm.tracker.wipe()
```

---

## Retry & Fallback

Built-in exponential backoff with full jitter. Retries on `429`, `500`, `502`, `503`, `504`. Never retries `400`/`401`/`404`.

```python
# Up to 5 attempts with backoff
resp = await unillm.completion("qwen/qwen-turbo", messages, max_attempts=5)
```

---

## Self-hosted Setup

**Ollama** — easiest way to run Qwen, GLM, Llama locally:

```bash
ollama pull qwen2.5:7b
ollama pull glm4
```
```python
resp = await unillm.completion("ollama/qwen2.5:7b", messages)
```

**vLLM** — for GPU servers:

```bash
vllm serve Qwen/Qwen2.5-72B-Instruct --port 8000
```
```python
resp = await unillm.completion("vllm/Qwen2.5-72B-Instruct", messages)
```

---

## Adding a New Provider

Any OpenAI-compatible API is 4 lines in `unillm/providers.py`:

```python
"spark": {
    "base": "https://spark-api-open.xf-yun.com/v1",
    "key_env": "SPARK_API_KEY",
    "style": "openai",
},
```

---

## Project Structure

```
relay/
├── unillm/
│   ├── __init__.py      # Public API: completion(), stream(), register_provider()
│   ├── providers.py     # Provider registry + cost table (15+ providers)
│   ├── handlers.py      # HTTP adapters (openai-compat, anthropic, gemini)
│   ├── retry.py         # Exponential backoff with full jitter
│   ├── tracker.py       # SQLite-backed cost + usage tracker
│   ├── fallback.py      # FallbackRouter
│   ├── config.py        # config.yaml loader (litellm-compatible format)
│   ├── server.py        # FastAPI proxy server
│   └── cli.py           # relay CLI entry point
├── tests/
│   ├── test_unillm.py   # Provider, retry, fallback tests
│   ├── test_tracker.py  # SQLite persistence tests
│   └── test_server.py   # Config loader + server endpoint tests
├── config.yaml          # Example config (edit and use directly)
├── .github/
│   └── workflows/
│       └── publish.yml  # SHA-pinned CI, OIDC Trusted Publisher
└── pyproject.toml
```

---

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
# 30 tests, all mocked — no real API keys needed
```

---

## Supply Chain Security

This project was built specifically to avoid what compromised LiteLLM:

1. **OIDC Trusted Publishers** — no stored PyPI token that can be stolen
2. **All GitHub Actions pinned to commit SHAs** — tags are mutable, SHAs are not
3. **Minimal deps** — `httpx`, `fastapi`, `uvicorn`, `pyyaml` only
4. **No `.pth` files, no import hooks** — nothing executes on install
5. **Hash-pinned build requirements** in `.github/build-requirements.txt`

Verify any release:
```bash
pip install relay-llm==0.1.0 --require-hashes --hash=sha256:<hash>
```

---

## License

MIT
