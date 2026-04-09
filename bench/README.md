# Relay Benchmark Suite

Head-to-head performance benchmark: **relay** vs **LiteLLM proxy** (or any two OpenAI-compatible proxies).

Measures: throughput (req/s), latency (p50/p90/p99), tokens/sec, error rate.  
Generates a self-contained HTML report with interactive charts.

## Install

```bash
pip install httpx fastapi uvicorn
```

## Usage

### Against real proxies

```bash
# Start relay
relay --config config.yaml --port 4000

# Start LiteLLM (must have litellm installed separately)
litellm --config config.yaml --port 8000

# Run benchmark
python bench/bench.py \
    --relay   http://localhost:4000 \
    --litellm http://localhost:8000 \
    --model   gpt-4o-mini \
    --key     sk-your-key \
    --requests 200 \
    --concurrency 20

# Generate HTML report
python bench/report.py --open
```

### Mock mode (no API keys or proxies needed)

Spins up two in-process FastAPI mock servers. Measures pure proxy overhead.

```bash
python bench/bench.py --mock --requests 500 --concurrency 50

# Test all payload sizes
python bench/bench.py --mock --payload all --requests 200

# Simulate latency difference (e.g. relay has 5ms extra delay)
python bench/bench.py --mock \
    --mock-delay-relay 5 \
    --mock-delay-litellm 0 \
    --requests 300 --concurrency 30
```

### Single proxy

```bash
python bench/bench.py \
    --relay http://localhost:4000 \
    --model qwen-turbo \
    --key   sk-relay-secret \
    --requests 100 --concurrency 10
```

## Output

Terminal output — live progress bar + results table:

```
  ┌─ relay  (http://localhost:4000)  model=gpt-4o-mini  payload=short
  │  Requests   : 200 total, 200 ok, 0 errors  (100.0% success)
  │  Throughput : 312.4 req/s  |  3748 tokens/s
  │  Latency    : min=8ms  mean=31ms  p50=29ms  p90=52ms  p99=78ms  max=91ms
  │  Std dev    : 14ms
  └─ Wall time  : 0.64s

  ┌─────────────────────────────────────────────────────────────────┐
  │                    HEAD-TO-HEAD COMPARISON                      │
  ├─────────────────────────────────────────────────────────────────┤
  │  RPS                relay: 312.4    litellm: 271.8  →  14.9% higher  (✓ relay)
  │  Mean latency       relay: 31ms     litellm: 36ms   →  14.3% faster  (✓ relay)
  │  P50                relay: 29ms     litellm: 33ms   →  12.7% faster  (✓ relay)
  │  P90                relay: 52ms     litellm: 63ms   →  17.8% faster  (✓ relay)
  │  P99                relay: 78ms     litellm: 101ms  →  22.5% faster  (✓ relay)
  └─────────────────────────────────────────────────────────────────┘
```

HTML report (`bench_report.html`) — dark-mode dashboard with:
- Latency breakdown bar chart (min/mean/p50/p90/p99/max)
- Throughput bar chart (req/s)  
- Latency percentile line chart
- Full stats table per proxy
- Head-to-head winner summary

## Options

```
bench.py:
  --relay        URL     Relay proxy URL
  --litellm      URL     LiteLLM proxy URL
  --model        NAME    Model name (default: gpt-4o-mini)
  --key          KEY     API / master key
  --requests  -n INT     Total requests (default: 100)
  --concurrency -c INT   Concurrent workers (default: 10)
  --warmup       INT     Warmup requests before timing (default: 5)
  --payload      TYPE    short | medium | long | all (default: short)
  --output       PATH    JSON results path (default: bench_results.json)
  --mock                 Use local mock servers, no API keys needed
  --mock-delay-relay  MS Artificial delay for mock relay (ms)
  --mock-delay-litellm MS Artificial delay for mock litellm (ms)

report.py:
  --input   PATH   JSON results file (default: bench_results.json)
  --output  PATH   HTML output file  (default: bench_report.html)
  --open           Open report in browser after generating
```

## What gets measured

| Metric | Description |
|--------|-------------|
| `req/s` | Successful requests per second (wall-clock) |
| `tokens/s` | Output tokens per second |
| `mean` | Average latency of successful requests |
| `p50` | Median latency |
| `p90` | 90th percentile — tail latency |
| `p99` | 99th percentile — worst-case latency |
| `success %` | Percentage of non-error responses |

> **Note on mock mode:** both proxies run on `127.0.0.1` in the same process, so results are statistically identical. Use `--mock-delay-*` to simulate real differences. For meaningful results, run against real proxies pointed at a fast local model (Ollama) to isolate proxy overhead from model latency.
