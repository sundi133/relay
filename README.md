# relay

A minimal, **supply-chain-safe** LLM router for Python.  
One runtime dependency (`httpx`). ~500 lines of auditable code.  
Built-in retry, fallback, cost tracking, and SQLite persistence.

> **Why relay?**  
> [LiteLLM was compromised in a supply chain attack (March 2026)](https://docs.litellm.ai/blog/security-update-march-2026) via a stolen PyPI token from an unpinned CI dependency. Relay is a clean-room replacement designed with that attack in mind — single dependency, OIDC publishing, SHA-pinned CI actions, and a codebase small enough to audit in one sitting.

---

## Install

```bash
pip install relay-llm
# or with uv (faster, hash-verified):
uv pip install relay-llm
```

---

## Quick Start

```python
import asyncio
import unillm

messages = [{"role": "user", "content": "Tell me a joke"}]

async def main():
    resp = await unillm.completion("qwen/qwen-turbo", messages)
    print(resp.content)
    print(resp.usage)
    # {"prompt_tokens": 12, "completion_tokens": 38, "total_tokens": 50, "cost_usd": 0.0000025}

asyncio.run(main())
```

---

## Supported Providers

| Shortname   | Provider           | Example models                              | Env var               |
|-------------|--------------------|---------------------------------------------|-----------------------|
| `openai`    | OpenAI             | gpt-4o, gpt-4o-mini, o1                    | `OPENAI_API_KEY`      |
| `anthropic` | Anthropic          | claude-opus-4, claude-sonnet-4             | `ANTHROPIC_API_KEY`   |
| `gemini`    | Google             | gemini-2.0-flash, gemini-1.5-pro           | `GEMINI_API_KEY`      |
| `qwen`      | Alibaba DashScope  | qwen-turbo, qwen-plus, qwen-max, qwen2.5-* | `DASHSCOPE_API_KEY`   |
| `glm`       | Zhipu AI           | glm-4, glm-4-flash, glm-4v                | `ZHIPU_API_KEY`       |
| `kimi`      | Moonshot AI        | moonshot-v1-8k/32k/128k                    | `MOONSHOT_API_KEY`    |
| `deepseek`  | DeepSeek           | deepseek-chat, deepseek-reasoner           | `DEEPSEEK_API_KEY`    |
| `yi`        | 01.AI              | yi-lightning, yi-large                     | `YI_API_KEY`          |
| `mistral`   | Mistral AI         | mistral-large-latest, codestral            | `MISTRAL_API_KEY`     |
| `baichuan`  | Baichuan           | Baichuan4                                  | `BAICHUAN_API_KEY`    |
| `minimax`   | MiniMax            | abab6.5-chat                               | `MINIMAX_API_KEY`     |
| `ollama`    | Self-hosted        | any model you've pulled                    | none                  |
| `vllm`      | Self-hosted        | any HuggingFace model                      | none                  |
| `llamacpp`  | Self-hosted        | any GGUF model                             | none                  |
| `tgi`       | HuggingFace TGI    | any TGI-served model                       | none                  |

All providers use the **OpenAI-compatible** `/v1/chat/completions` format except Anthropic and Gemini, which have their own thin adapters.

---

## Features

### Streaming

```python
async for chunk in await unillm.stream("glm/glm-4", messages):
    print(chunk, end="", flush=True)
```

### Automatic Retry

Exponential backoff with full jitter on `429`, `500`, `502`, `503`, `504`.  
Does **not** retry `400`/`401`/`404` — those are your bug, not the server's.

```python
resp = await unillm.completion("qwen/qwen-turbo", messages, max_attempts=5)
```

### Fallback / Redundancy

Try models in priority order — first success wins.

```python
from unillm import FallbackRouter

router = FallbackRouter([
    "deepseek/deepseek-chat",    # cheapest first
    "qwen/qwen-plus",            # mid-tier backup
    "openai/gpt-4o-mini",        # reliable cloud fallback
    "ollama/llama3.2",           # local last resort
])

resp = await router.completion(messages)
print(f"Served by: {resp['_used_model']}")
print(resp.content)
```

### Cost & Usage Tracking (persistent across restarts)

Every call is written to a local SQLite database at `~/.unillm/usage.db`.  
Totals are loaded back automatically on startup — no data lost between restarts.

```python
import unillm

resp = await unillm.completion("qwen/qwen-turbo", messages)

# Lifetime totals (includes previous runs)
print(unillm.tracker.total_calls)
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
  ───────────────────────────────────────────────────────────────────────────────────
  deepseek/deepseek-chat               1200    1,500,000    0.168000      280       1
  qwen/qwen-turbo                       500      600,000    0.030000      310       0
  ollama/llama3.2                       147      204,100    0.000000      890       0

  Daily cost (last 7 days):
  Date           Calls     Tokens     Cost ($)
  ────────────────────────────────────────────
  2026-04-08       312    420,300    0.047163
  2026-04-07       288    390,100    0.043811
```

**Query history:**

```python
# Last 20 calls
unillm.tracker.history(limit=20)

# All errors
unillm.tracker.history(errors_only=True)

# Filter by model and date
unillm.tracker.history(model="qwen/qwen-turbo", since="2026-04-01")

# Cost per day
unillm.tracker.daily_cost()

# Change DB location
import os
os.environ["UNILLM_DB"] = "./myproject.db"
# or: UsageTracker(db_path="./myproject.db")

# Clear memory, keep DB
unillm.tracker.reset_session()

# Delete everything (irreversible)
unillm.tracker.wipe()
```

### Register Any Custom Endpoint

Any OpenAI-compatible server works — local or remote:

```python
unillm.register_provider(
    "myserver",
    "http://my-inference-server:8000/v1",
    api_key="sk-optional",
)
resp = await unillm.completion("myserver/my-fine-tuned-model", messages)
```

---

## Self-hosted Setup

**Ollama** (easiest way to run Qwen, GLM, Llama locally):

```bash
ollama pull qwen2.5:7b
ollama pull glm4
```

```python
resp = await unillm.completion("ollama/qwen2.5:7b", messages)
resp = await unillm.completion("ollama/glm4", messages)
```

**vLLM** (for GPU servers):

```bash
vllm serve Qwen/Qwen2.5-72B-Instruct --port 8000
```

```python
resp = await unillm.completion("vllm/Qwen2.5-72B-Instruct", messages)
```

---

## Project Structure

```
relay/
├── unillm/
│   ├── __init__.py      # Public API: completion(), stream(), register_provider()
│   ├── providers.py     # Provider registry + cost table (15+ providers)
│   ├── handlers.py      # HTTP logic (openai-compat, anthropic, gemini)
│   ├── retry.py         # Exponential backoff with full jitter
│   ├── tracker.py       # SQLite-backed cost + usage tracker
│   └── fallback.py      # FallbackRouter
├── tests/
│   ├── test_unillm.py   # Provider + retry + fallback tests (all mocked)
│   └── test_tracker.py  # Persistence tests using pytest tmp_path
├── .github/
│   └── workflows/
│       └── publish.yml  # SHA-pinned CI, OIDC Trusted Publisher (no stored token)
└── pyproject.toml
```

---

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

All 18 tests run with mocked HTTP (no real API keys needed).

---

## Supply Chain Security

This project was built specifically to avoid the class of vulnerability that compromised LiteLLM:

| Risk | LiteLLM | Relay |
|------|---------|-------|
| PyPI publishing | Stored API token (stolen) | OIDC Trusted Publisher — no token |
| CI dependencies | Unpinned `trivy` action (hijacked) | All actions pinned to commit SHA |
| Runtime deps | 100+ transitive deps | `httpx` only |
| Install-time execution | `.pth` file ran on every Python startup | Nothing runs on install |
| Codebase size | ~50,000 lines | ~500 lines — auditable in one sitting |

**Verify a specific release:**

```bash
pip install relay-llm==0.1.0 --require-hashes \
    --hash=sha256:<hash-from-pypi>
```

---

## Adding a New Provider

Any OpenAI-compatible API takes 4 lines in `unillm/providers.py`:

```python
"spark": {                          # Xfei Spark, Baidu ERNIE, etc.
    "base": "https://your-api.com/v1",
    "key_env": "YOUR_API_KEY",
    "style": "openai",
},
```

Then use it immediately:

```python
resp = await unillm.completion("spark/spark-max", messages)
```

---

## License

MIT
