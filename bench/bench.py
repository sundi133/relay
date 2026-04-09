"""
bench.py — benchmark two OpenAI-compatible proxy endpoints head-to-head.

Usage:
    # Compare relay vs litellm (both must be running)
    python bench.py \
        --relay   http://localhost:4000 \
        --litellm http://localhost:8000 \
        --model   gpt-4o-mini \
        --key     sk-your-key \
        --requests 200 \
        --concurrency 20

    # Benchmark a single proxy
    python bench.py \
        --relay http://localhost:4000 \
        --model qwen-turbo \
        --requests 100 \
        --concurrency 10

    # Run against a mock server (no real API keys needed)
    python bench.py --mock --requests 500 --concurrency 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
import traceback

import httpx


# ── Payload ──────────────────────────────────────────────────────────────────

PAYLOADS = {
    "short": {
        "messages": [{"role": "user", "content": "Say 'pong' and nothing else."}],
        "max_tokens": 10,
    },
    "medium": {
        "messages": [{"role": "user", "content": "List 5 capital cities in one sentence."}],
        "max_tokens": 60,
    },
    "long": {
        "messages": [{"role": "user", "content": "Explain how TCP/IP works in 3 paragraphs."}],
        "max_tokens": 300,
    },
}


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class CallResult:
    latency_ms: float
    status: int
    tokens: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status == 200


@dataclass
class BenchResult:
    name: str
    url: str
    model: str
    payload_type: str
    total_requests: int
    concurrency: int
    results: list[CallResult] = field(default_factory=list)
    wall_time_s: float = 0.0

    # ── Derived metrics ───────────────────────────────────────────────────

    @property
    def successes(self) -> list[CallResult]:
        return [r for r in self.results if r.ok]

    @property
    def errors(self) -> list[CallResult]:
        return [r for r in self.results if not r.ok]

    @property
    def success_rate(self) -> float:
        return len(self.successes) / len(self.results) * 100 if self.results else 0

    @property
    def latencies(self) -> list[float]:
        return [r.latency_ms for r in self.successes]

    @property
    def p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0

    @property
    def p90(self) -> float:
        lats = sorted(self.latencies)
        return lats[int(len(lats) * 0.9)] if lats else 0

    @property
    def p99(self) -> float:
        lats = sorted(self.latencies)
        return lats[int(len(lats) * 0.99)] if lats else 0

    @property
    def mean(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0

    @property
    def stddev(self) -> float:
        return statistics.stdev(self.latencies) if len(self.latencies) > 1 else 0

    @property
    def min_lat(self) -> float:
        return min(self.latencies) if self.latencies else 0

    @property
    def max_lat(self) -> float:
        return max(self.latencies) if self.latencies else 0

    @property
    def rps(self) -> float:
        return len(self.successes) / self.wall_time_s if self.wall_time_s else 0

    @property
    def total_tokens(self) -> int:
        return sum(r.tokens for r in self.successes)

    @property
    def tokens_per_sec(self) -> float:
        return self.total_tokens / self.wall_time_s if self.wall_time_s else 0

    def summary_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "model": self.model,
            "payload": self.payload_type,
            "total_requests": self.total_requests,
            "concurrency": self.concurrency,
            "success_rate_pct": round(self.success_rate, 2),
            "errors": len(self.errors),
            "rps": round(self.rps, 2),
            "tokens_per_sec": round(self.tokens_per_sec, 1),
            "latency_ms": {
                "min":   round(self.min_lat, 1),
                "mean":  round(self.mean, 1),
                "p50":   round(self.p50, 1),
                "p90":   round(self.p90, 1),
                "p99":   round(self.p99, 1),
                "max":   round(self.max_lat, 1),
                "stdev": round(self.stddev, 1),
            },
            "wall_time_s": round(self.wall_time_s, 2),
        }


# ── Single request ────────────────────────────────────────────────────────────

async def _one_request(
    client: httpx.AsyncClient,
    url: str,
    model: str,
    payload: dict,
    api_key: Optional[str],
) -> CallResult:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {"model": model, "temperature": 0, **payload}
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{url}/v1/chat/completions",
            json=body,
            headers=headers,
            timeout=60,
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            return CallResult(latency_ms=latency_ms, status=r.status_code,
                              error=f"HTTP {r.status_code}: {r.text[:120]}")
        data = r.json()
        tokens = data.get("usage", {}).get("total_tokens", 0)
        return CallResult(latency_ms=latency_ms, status=200, tokens=tokens)

    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000
        return CallResult(latency_ms=latency_ms, status=0, error=str(exc)[:120])


# ── Concurrent load runner ────────────────────────────────────────────────────

async def run_bench(
    name: str,
    url: str,
    model: str,
    payload_type: str,
    n_requests: int,
    concurrency: int,
    api_key: Optional[str],
    warmup: int = 5,
) -> BenchResult:
    payload = PAYLOADS[payload_type]
    result = BenchResult(
        name=name, url=url, model=model,
        payload_type=payload_type,
        total_requests=n_requests,
        concurrency=concurrency,
    )

    sem = asyncio.Semaphore(concurrency)
    progress = {"done": 0}

    async def _worker(client: httpx.AsyncClient) -> CallResult:
        async with sem:
            r = await _one_request(client, url, model, payload, api_key)
            progress["done"] += 1
            _print_progress(progress["done"], n_requests, name)
            return r

    limits = httpx.Limits(max_connections=concurrency + 10,
                          max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(limits=limits) as client:
        # Warmup
        if warmup:
            print(f"  Warming up {name} ({warmup} requests)…")
            warmup_tasks = [_one_request(client, url, model, payload, api_key)
                            for _ in range(warmup)]
            await asyncio.gather(*warmup_tasks)

        print(f"\n  Running {name}: {n_requests} requests @ concurrency={concurrency}")
        t0 = time.perf_counter()
        tasks = [_worker(client) for _ in range(n_requests)]
        results = await asyncio.gather(*tasks)
        result.wall_time_s = time.perf_counter() - t0

    result.results = list(results)
    print()  # newline after progress bar
    return result


def _print_progress(done: int, total: int, name: str):
    pct = done / total
    bar = "█" * int(pct * 30) + "░" * (30 - int(pct * 30))
    print(f"\r  [{bar}] {done}/{total} {name}", end="", flush=True)


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_report(results: list[BenchResult]):
    print("\n")
    print("╔══════════════════════════════════════════════════════════════════════════╗")
    print("║                        BENCHMARK RESULTS                                ║")
    print("╚══════════════════════════════════════════════════════════════════════════╝")

    for r in results:
        print(f"\n  ┌─ {r.name}  ({r.url})  model={r.model}  payload={r.payload_type}")
        print(f"  │  Requests   : {r.total_requests} total, {len(r.successes)} ok, "
              f"{len(r.errors)} errors  ({r.success_rate:.1f}% success)")
        print(f"  │  Throughput : {r.rps:.1f} req/s  |  {r.tokens_per_sec:.0f} tokens/s")
        print(f"  │  Latency    : min={r.min_lat:.0f}ms  mean={r.mean:.0f}ms  "
              f"p50={r.p50:.0f}ms  p90={r.p90:.0f}ms  p99={r.p99:.0f}ms  max={r.max_lat:.0f}ms")
        print(f"  │  Std dev    : {r.stddev:.0f}ms")
        print(f"  └─ Wall time  : {r.wall_time_s:.2f}s")

    # ── Head-to-head comparison ───────────────────────────────────────────────
    if len(results) == 2:
        a, b = results[0], results[1]
        print("\n")
        print("  ┌─────────────────────────────────────────────────────────────────┐")
        print("  │                    HEAD-TO-HEAD COMPARISON                      │")
        print("  ├─────────────────────────────────────────────────────────────────┤")

        def _cmp(label, a_val, b_val, unit="", lower_is_better=True):
            if a_val == 0 and b_val == 0:
                return
            if b_val == 0:
                diff_str = "N/A"
                winner = a.name
            else:
                diff = (a_val - b_val) / b_val * 100
                if lower_is_better:
                    winner = a.name if a_val < b_val else b.name
                    sign = "faster" if a_val < b_val else "slower"
                    diff_str = f"{abs(diff):.1f}% {sign}"
                else:
                    winner = a.name if a_val > b_val else b.name
                    sign = "higher" if a_val > b_val else "lower"
                    diff_str = f"{abs(diff):.1f}% {sign}"

            print(f"  │  {label:<18} {a.name}: {a_val:.1f}{unit:<4}  "
                  f"{b.name}: {b_val:.1f}{unit}  →  {diff_str}  (✓ {winner})")

        _cmp("RPS",         a.rps,    b.rps,    "",    lower_is_better=False)
        _cmp("Mean latency",a.mean,   b.mean,   "ms",  lower_is_better=True)
        _cmp("P50",         a.p50,    b.p50,    "ms",  lower_is_better=True)
        _cmp("P90",         a.p90,    b.p90,    "ms",  lower_is_better=True)
        _cmp("P99",         a.p99,    b.p99,    "ms",  lower_is_better=True)
        _cmp("Tokens/sec",  a.tokens_per_sec, b.tokens_per_sec, "", lower_is_better=False)
        _cmp("Errors",      len(a.errors), len(b.errors), "", lower_is_better=True)

        print("  └─────────────────────────────────────────────────────────────────┘")


def save_json(results: list[BenchResult], path: str = "bench_results.json"):
    data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": [r.summary_dict() for r in results],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\n  Results saved → {path}")


# ── Mock server (for testing without live proxies) ────────────────────────────

async def _start_mock_server(port: int, delay_ms: float = 0):
    """Minimal ASGI mock that returns a fake chat completion instantly."""
    from fastapi import FastAPI
    import uvicorn

    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def mock_chat(request: dict):
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        return {
            "id": "mock-123",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant",
                        "content": "pong"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.5)   # let it start
    return server, task


# ── CLI ───────────────────────────────────────────────────────────────────────

async def _main():
    parser = argparse.ArgumentParser(
        prog="bench",
        description="Benchmark relay vs LiteLLM proxy — head-to-head",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Real proxies
  python bench.py \\
      --relay   http://localhost:4000 \\
      --litellm http://localhost:8000 \\
      --model gpt-4o-mini --key sk-... \\
      --requests 200 --concurrency 20

  # Benchmark relay only
  python bench.py --relay http://localhost:4000 \\
      --model qwen-turbo --requests 100

  # Mock mode (no API keys / no proxies needed — measures proxy overhead only)
  python bench.py --mock --requests 500 --concurrency 50

  # All payload sizes
  python bench.py --mock --payload all --requests 200
        """,
    )
    parser.add_argument("--relay",       default=None, help="Relay proxy URL")
    parser.add_argument("--litellm",     default=None, help="LiteLLM proxy URL")
    parser.add_argument("--model",       default="gpt-4o-mini", help="Model name")
    parser.add_argument("--key",         default=None, help="API / master key")
    parser.add_argument("--requests","-n", type=int, default=100)
    parser.add_argument("--concurrency","-c", type=int, default=10)
    parser.add_argument("--warmup",      type=int, default=5)
    parser.add_argument("--payload",     default="short",
                        choices=["short", "medium", "long", "all"])
    parser.add_argument("--output",      default="bench_results.json")
    parser.add_argument("--mock",        action="store_true",
                        help="Spin up local mock servers (no real API needed)")
    parser.add_argument("--mock-delay-relay",   type=float, default=0,
                        help="Artificial delay ms for mock relay server")
    parser.add_argument("--mock-delay-litellm", type=float, default=0,
                        help="Artificial delay ms for mock litellm server")

    args = parser.parse_args()

    if not args.relay and not args.litellm and not args.mock:
        parser.error("Provide at least --relay or --litellm (or --mock)")

    targets: list[tuple[str, str]] = []  # (name, url)

    if args.mock:
        print("\n  Starting mock servers…")
        relay_server,   relay_task   = await _start_mock_server(14000, args.mock_delay_relay)
        litellm_server, litellm_task = await _start_mock_server(18000, args.mock_delay_litellm)
        targets = [("relay", "http://127.0.0.1:14000"),
                   ("litellm", "http://127.0.0.1:18000")]
        args.model = "mock-model"
        args.key = None
    else:
        if args.relay:
            targets.append(("relay", args.relay.rstrip("/")))
        if args.litellm:
            targets.append(("litellm", args.litellm.rstrip("/")))

    key = args.key or os.environ.get("OPENAI_API_KEY") or os.environ.get("RELAY_KEY")
    payload_types = list(PAYLOADS.keys()) if args.payload == "all" else [args.payload]

    all_results: list[BenchResult] = []

    print(f"\n  Benchmark config:")
    print(f"    Targets     : {[t[0] for t in targets]}")
    print(f"    Model       : {args.model}")
    print(f"    Requests    : {args.requests}  Concurrency: {args.concurrency}")
    print(f"    Payloads    : {payload_types}")
    print(f"    Warmup      : {args.warmup} requests\n")

    for payload_type in payload_types:
        round_results: list[BenchResult] = []
        for name, url in targets:
            result = await run_bench(
                name=name,
                url=url,
                model=args.model,
                payload_type=payload_type,
                n_requests=args.requests,
                concurrency=args.concurrency,
                api_key=key,
                warmup=args.warmup,
            )
            round_results.append(result)
            all_results.append(result)

        print_report(round_results)

    save_json(all_results, args.output)

    if args.mock:
        relay_server.should_exit = True
        litellm_server.should_exit = True


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
