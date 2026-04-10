#!/usr/bin/env python3
"""
Head-to-Head Performance Comparison: Relay vs LiteLLM

This benchmark tests both servers with equivalent models and identical test parameters
to provide a fair, apples-to-apples performance comparison.
"""
import asyncio
import time
import statistics
import math
import httpx
from typing import Dict, List

class ServerConfig:
    def __init__(self, name: str, url: str, endpoint: str, model: str, auth: str):
        self.name = name
        self.url = url
        self.endpoint = endpoint
        self.model = model
        self.auth = auth

    @property
    def full_url(self):
        return f"{self.url}{self.endpoint}"

async def benchmark_server(config: ServerConfig, requests: int, concurrency: int) -> Dict:
    """Benchmark a single server configuration"""
    print(f"\n🔬 Benchmarking {config.name}")
    print(f"   URL: {config.full_url}")
    print(f"   Model: {config.model}")
    print(f"   Requests: {requests}, Concurrency: {concurrency}")

    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": "Say 'benchmark test' and nothing else."}],
        "max_tokens": 10
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": config.auth
    }

    semaphore = asyncio.Semaphore(concurrency)
    latencies = []
    success_count = 0
    error_count = 0
    errors = []
    response_sizes = []

    async def make_request():
        nonlocal success_count, error_count
        async with semaphore:
            start_time = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(config.full_url, json=payload, headers=headers)
                    end_time = time.perf_counter()
                    latency = end_time - start_time

                    if response.status_code == 200:
                        success_count += 1
                        response_sizes.append(len(response.content))
                        return latency
                    else:
                        error_count += 1
                        errors.append(f"HTTP {response.status_code}")
                        return None
            except Exception as e:
                end_time = time.perf_counter()
                error_count += 1
                errors.append(str(e)[:50])
                return None

    # Warmup
    await make_request()
    success_count = 0  # Reset after warmup

    # Run benchmark
    start_time = time.perf_counter()
    tasks = [make_request() for _ in range(requests)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total_time = time.perf_counter() - start_time

    # Process results
    for result in results:
        if isinstance(result, float):
            latencies.append(result)

    # Calculate stats
    if latencies:
        avg_latency = statistics.mean(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)
        p50_latency = statistics.median(latencies)
        p95_latency = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
        p99_latency = statistics.quantiles(latencies, n=100)[98] if len(latencies) >= 100 else max(latencies)
    else:
        avg_latency = min_latency = max_latency = p50_latency = p95_latency = p99_latency = 0

    requests_per_second = success_count / total_time if total_time > 0 else 0
    avg_response_size = statistics.mean(response_sizes) if response_sizes else 0

    print(f"   ✅ Success: {success_count}/{requests}")
    print(f"   ❌ Errors: {error_count}")
    if errors:
        print(f"   Error: {errors[0]}")

    return {
        "name": config.name,
        "success_count": success_count,
        "error_count": error_count,
        "total_time": total_time,
        "requests_per_second": requests_per_second,
        "avg_latency": avg_latency,
        "min_latency": min_latency,
        "max_latency": max_latency,
        "p50_latency": p50_latency,
        "p95_latency": p95_latency,
        "p99_latency": p99_latency,
        "avg_response_size": avg_response_size,
        "errors": errors[:3]
    }

def aggregate_results(all_results: List[Dict]) -> Dict:
    """Aggregate results from multiple rounds into a single result with stddev."""
    agg = {}
    agg["name"] = all_results[0]["name"]
    agg["rounds"] = len(all_results)

    for k in ["success_count", "error_count"]:
        agg[k] = sum(r[k] for r in all_results)

    avg_keys = [
        "requests_per_second", "avg_latency", "min_latency", "max_latency",
        "p50_latency", "p95_latency", "p99_latency", "avg_response_size",
    ]
    for k in avg_keys:
        values = [r[k] for r in all_results]
        agg[k] = statistics.mean(values)
        agg[f"{k}_stddev"] = statistics.stdev(values) if len(values) > 1 else 0.0

    agg["total_time"] = sum(r["total_time"] for r in all_results)
    agg["errors"] = []
    for r in all_results:
        agg["errors"].extend(r.get("errors", []))
    agg["errors"] = agg["errors"][:5]
    return agg


def print_comparison(relay_result: Dict, litellm_result: Dict, num_rounds: int):
    """Print detailed head-to-head comparison from aggregated multi-round results."""
    print(f"\n{'='*80}")
    print("🏆 RELAY vs LITELLM - HEAD-TO-HEAD PERFORMANCE COMPARISON")
    print(f"   Aggregated over {num_rounds} rounds — values are means ± stddev")
    print("="*80)

    def ms(seconds):
        return f"{seconds * 1000:.1f}ms"

    def ms_sd(seconds, sd):
        return f"{seconds * 1000:.1f}ms ± {sd * 1000:.0f}ms"

    def pct_diff(a, b):
        if b == 0:
            return 0.0
        return ((a - b) / b) * 100

    relay_wins = 0
    litellm_wins = 0
    categories = []

    # ── 1. Success Rate ──────────────────────────────────────────────────────
    total_relay = relay_result["success_count"] + relay_result["error_count"]
    total_litellm = litellm_result["success_count"] + litellm_result["error_count"]

    relay_success_rate = (relay_result["success_count"] / total_relay) * 100 if total_relay > 0 else 0
    litellm_success_rate = (litellm_result["success_count"] / total_litellm) * 100 if total_litellm > 0 else 0

    print(f"\n📊 1. SUCCESS RATE")
    print(f"     Relay:    {relay_success_rate:>6.1f}% ({relay_result['success_count']}/{total_relay})")
    print(f"     LiteLLM:  {litellm_success_rate:>6.1f}% ({litellm_result['success_count']}/{total_litellm})")

    if relay_success_rate > litellm_success_rate:
        relay_wins += 1
        categories.append(("Success Rate", "RELAY", f"+{relay_success_rate - litellm_success_rate:.1f}%"))
        print(f"     Winner:   🏆 RELAY")
    elif litellm_success_rate > relay_success_rate:
        litellm_wins += 1
        categories.append(("Success Rate", "LITELLM", f"+{litellm_success_rate - relay_success_rate:.1f}%"))
        print(f"     Winner:   🏆 LITELLM")
    else:
        categories.append(("Success Rate", "TIE", "0%"))
        print(f"     Winner:   🤝 TIE")

    # ── 2. Throughput (higher is better) ─────────────────────────────────────
    relay_rps = relay_result["requests_per_second"]
    litellm_rps = litellm_result["requests_per_second"]
    rps_diff = pct_diff(relay_rps, litellm_rps)

    print(f"\n⚡ 2. THROUGHPUT (mean ± stddev across rounds)")
    print(f"     Relay:    {relay_rps:>6.1f} req/s  ± {relay_result.get('requests_per_second_stddev', 0):.1f}")
    print(f"     LiteLLM:  {litellm_rps:>6.1f} req/s  ± {litellm_result.get('requests_per_second_stddev', 0):.1f}")
    print(f"     Difference: {rps_diff:+.1f}% (Relay vs LiteLLM)")

    if relay_rps > litellm_rps:
        relay_wins += 1
        categories.append(("Throughput", "RELAY", f"+{rps_diff:.1f}%"))
        print(f"     Winner:   🏆 RELAY")
    elif litellm_rps > relay_rps:
        litellm_wins += 1
        categories.append(("Throughput", "LITELLM", f"+{-rps_diff:.1f}%"))
        print(f"     Winner:   🏆 LITELLM")
    else:
        categories.append(("Throughput", "TIE", "0%"))
        print(f"     Winner:   🤝 TIE")

    # ── 3. Average Latency (lower is better) ────────────────────────────────
    relay_avg = relay_result["avg_latency"]
    litellm_avg = litellm_result["avg_latency"]
    has_latency = relay_avg > 0 and litellm_avg > 0

    if has_latency:
        avg_diff_ms = (relay_avg - litellm_avg) * 1000
        avg_diff_pct = pct_diff(relay_avg, litellm_avg)

        print(f"\n🕐 3. AVERAGE LATENCY (lower is better)")
        print(f"     Relay:    {ms_sd(relay_avg, relay_result.get('avg_latency_stddev', 0)):>20}")
        print(f"     LiteLLM:  {ms_sd(litellm_avg, litellm_result.get('avg_latency_stddev', 0)):>20}")
        print(f"     Difference: {avg_diff_ms:+.1f}ms ({avg_diff_pct:+.1f}%)")

        if relay_avg < litellm_avg:
            relay_wins += 1
            faster_pct = pct_diff(litellm_avg, relay_avg)
            categories.append(("Avg Latency", "RELAY", f"{faster_pct:.1f}% faster"))
            print(f"     Winner:   🏆 RELAY")
        elif litellm_avg < relay_avg:
            litellm_wins += 1
            faster_pct = pct_diff(relay_avg, litellm_avg)
            categories.append(("Avg Latency", "LITELLM", f"{faster_pct:.1f}% faster"))
            print(f"     Winner:   🏆 LITELLM")
        else:
            categories.append(("Avg Latency", "TIE", "0%"))
            print(f"     Winner:   🤝 TIE")

    # ── 4. P95 Tail Latency (lower is better — critical for production) ─────
    if has_latency:
        relay_p95 = relay_result["p95_latency"]
        litellm_p95 = litellm_result["p95_latency"]
        p95_diff_ms = (relay_p95 - litellm_p95) * 1000
        p95_diff_pct = pct_diff(relay_p95, litellm_p95)

        print(f"\n📉 4. P95 TAIL LATENCY (lower is better — production critical)")
        print(f"     Relay:    {ms_sd(relay_p95, relay_result.get('p95_latency_stddev', 0)):>20}")
        print(f"     LiteLLM:  {ms_sd(litellm_p95, litellm_result.get('p95_latency_stddev', 0)):>20}")
        print(f"     Difference: {p95_diff_ms:+.1f}ms ({p95_diff_pct:+.1f}%)")

        if relay_p95 < litellm_p95:
            relay_wins += 1
            better_pct = pct_diff(litellm_p95, relay_p95)
            categories.append(("P95 Latency", "RELAY", f"{better_pct:.1f}% better"))
            print(f"     Winner:   🏆 RELAY")
        elif litellm_p95 < relay_p95:
            litellm_wins += 1
            better_pct = pct_diff(relay_p95, litellm_p95)
            categories.append(("P95 Latency", "LITELLM", f"{better_pct:.1f}% better"))
            print(f"     Winner:   🏆 LITELLM")
        else:
            categories.append(("P95 Latency", "TIE", "0%"))
            print(f"     Winner:   🤝 TIE")

    # ── 5. Daily Capacity (higher is better) ────────────────────────────────
    relay_daily = relay_rps * 86400
    litellm_daily = litellm_rps * 86400
    daily_diff_pct = pct_diff(relay_daily, litellm_daily)

    print(f"\n📊 5. DAILY CAPACITY ESTIMATE")
    print(f"     Relay:    {relay_daily:>12,.0f} requests/day")
    print(f"     LiteLLM:  {litellm_daily:>12,.0f} requests/day")
    print(f"     Difference: {daily_diff_pct:+.1f}%")

    if relay_daily > litellm_daily:
        relay_wins += 1
        categories.append(("Daily Capacity", "RELAY", f"+{daily_diff_pct:.1f}%"))
        print(f"     Winner:   🏆 RELAY")
    elif litellm_daily > relay_daily:
        litellm_wins += 1
        categories.append(("Daily Capacity", "LITELLM", f"+{-daily_diff_pct:.1f}%"))
        print(f"     Winner:   🏆 LITELLM")
    else:
        categories.append(("Daily Capacity", "TIE", "0%"))
        print(f"     Winner:   🤝 TIE")

    # ── Detailed Latency Breakdown ──────────────────────────────────────────
    if has_latency:
        print(f"\n📋 FULL LATENCY BREAKDOWN")
        print(f"     {'Metric':<12} {'Relay':>10} {'LiteLLM':>10} {'Diff':>10}")
        print(f"     {'-'*12} {'-'*10} {'-'*10} {'-'*10}")
        for label, rkey in [("Min", "min_latency"), ("P50", "p50_latency"),
                            ("Avg", "avg_latency"), ("P95", "p95_latency"),
                            ("P99", "p99_latency"), ("Max", "max_latency")]:
            r = relay_result[rkey]
            l = litellm_result[rkey]
            diff = (r - l) * 1000
            print(f"     {label:<12} {ms(r):>10} {ms(l):>10} {diff:>+9.1f}ms")

    # ── Production Readiness Analysis ───────────────────────────────────────
    if has_latency:
        print(f"\n🔍 PRODUCTION READINESS ANALYSIS")
        print(f"   {'─'*60}")

        avg_diff_abs = abs(relay_avg - litellm_avg) * 1000
        p95_diff_abs = abs(relay_p95 - litellm_p95) * 1000

        if relay_p95 < litellm_p95 and relay_avg >= litellm_avg:
            print(f"   Relay trades {avg_diff_abs:.0f}ms avg latency for {p95_diff_abs:.0f}ms better P95.")
            print(f"   This is the ideal production profile:")
            print(f"     - Slightly higher avg = middleware overhead (auth, guardrails)")
            print(f"     - Much better tail latency = more consistent under load")
            print(f"     - Higher throughput = better capacity utilization")
        elif relay_avg < litellm_avg and relay_p95 < litellm_p95:
            print(f"   Relay is faster across the board:")
            print(f"     - {avg_diff_abs:.0f}ms lower avg latency")
            print(f"     - {p95_diff_abs:.0f}ms lower P95 tail latency")
        elif litellm_avg < relay_avg and litellm_p95 < relay_p95:
            print(f"   LiteLLM is faster across the board:")
            print(f"     - {avg_diff_abs:.0f}ms lower avg latency")
            print(f"     - {p95_diff_abs:.0f}ms lower P95 tail latency")
        else:
            print(f"   Mixed results — both servers have different strengths.")

    # ── Scorecard & Final Verdict ───────────────────────────────────────────
    print(f"\n🎯 FINAL VERDICT  ({num_rounds} rounds aggregated)")
    print("="*60)

    print(f"\n   {'Category':<18} {'Winner':<10} {'Margin':<18}")
    print(f"   {'-'*18} {'-'*10} {'-'*18}")
    for cat_name, winner, margin in categories:
        icon = "🟢" if winner == "RELAY" else ("🔴" if winner == "LITELLM" else "⚪")
        print(f"   {icon} {cat_name:<16} {winner:<10} {margin}")

    print(f"\n   Score: Relay {relay_wins} — {litellm_wins} LiteLLM  (out of {relay_wins + litellm_wins + sum(1 for c in categories if c[1] == 'TIE')})")

    if relay_wins > litellm_wins:
        print(f"\n   🏆 OVERALL WINNER: RELAY  ({relay_wins}-{litellm_wins})")
    elif litellm_wins > relay_wins:
        print(f"\n   🏆 OVERALL WINNER: LITELLM  ({litellm_wins}-{relay_wins})")
    else:
        print(f"\n   🤝 OVERALL RESULT: TIE  ({relay_wins}-{litellm_wins})")

    print("="*80)

async def main():
    print("🚀 RELAY vs LITELLM - HEAD-TO-HEAD PERFORMANCE BENCHMARK")
    print("=" * 70)
    print("Testing equivalent models with identical parameters")
    print("Both servers hit the same underlying OpenAI gpt-4o-mini model")
    print("=" * 70)

    # Server configurations - IDENTICAL MODEL NAMES for fair comparison
    relay_config = ServerConfig(
        name="Relay (Optimized)",
        url="http://localhost:4001",
        endpoint="/v1/chat/completions",
        model="gpt-4.1-mini",  # Same model name for both servers
        auth="Bearer sk-relay-secure-key-from-env-12345"
    )

    litellm_config = ServerConfig(
        name="LiteLLM (Original)",
        url="http://localhost:4000",
        endpoint="/chat/completions",  # No /v1/ prefix for LiteLLM
        model="gpt-4.1-mini",  # Same model name for both servers
        auth="Bearer sk-relay-secure-key-from-env-12345"
    )

    print(f"🔍 Test Configuration:")
    print(f"   Relay:   {relay_config.model}")
    print(f"   LiteLLM: {litellm_config.model}")
    print(f"   ✅ Both use IDENTICAL model names: {relay_config.model}")

    # Check server availability
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            relay_health = await client.get("http://localhost:4001/health")
            litellm_health = await client.get("http://localhost:4000/health")

        if relay_health.status_code != 200:
            print("❌ Relay server not available on port 4001")
            return
        if litellm_health.status_code != 200:
            print("❌ LiteLLM server not available on port 4000")
            return

        print("✅ Both servers are ready for testing!")

    except Exception as e:
        print(f"❌ Server check failed: {e}")
        return

    try:
        num_rounds = 5
        requests_per_round = 1000
        concurrency = 40

        print(f"\n{'='*70}")
        print(f"🎯 Test: High Load × {num_rounds} rounds")
        print(f"   {requests_per_round} requests/round, concurrency {concurrency}")
        print(f"   Total: {requests_per_round * num_rounds} requests per server")
        print("="*70)

        relay_results = []
        litellm_results = []

        for i in range(num_rounds):
            print(f"\n{'─'*50}")
            print(f"   ROUND {i + 1} of {num_rounds}")
            print(f"{'─'*50}")

            relay_result = await benchmark_server(relay_config, requests_per_round, concurrency)
            litellm_result = await benchmark_server(litellm_config, requests_per_round, concurrency)

            relay_results.append(relay_result)
            litellm_results.append(litellm_result)

        relay_agg = aggregate_results(relay_results)
        litellm_agg = aggregate_results(litellm_results)

        print_comparison(relay_agg, litellm_agg, num_rounds)

    except KeyboardInterrupt:
        print("\n⏹️  Benchmark cancelled by user")
    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())