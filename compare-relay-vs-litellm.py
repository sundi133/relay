#!/usr/bin/env python3
"""
Head-to-Head Performance Comparison: Relay vs LiteLLM

This benchmark tests both servers with equivalent models and identical test parameters
to provide a fair, apples-to-apples performance comparison.
"""
import asyncio
import time
import statistics
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

def print_comparison(relay_result: Dict, litellm_result: Dict):
    """Print detailed head-to-head comparison"""
    print(f"\n{'='*80}")
    print("🏆 RELAY vs LITELLM - HEAD-TO-HEAD PERFORMANCE COMPARISON")
    print("="*80)

    def ms(seconds):
        return f"{seconds * 1000:.1f}ms"

    def pct_improvement(new_val, old_val):
        if old_val == 0:
            return "N/A"
        improvement = ((new_val - old_val) / old_val) * 100
        if improvement > 0:
            return f"+{improvement:.1f}%"
        else:
            return f"{improvement:.1f}%"

    # Success Rate Comparison
    total_relay = relay_result["success_count"] + relay_result["error_count"]
    total_litellm = litellm_result["success_count"] + litellm_result["error_count"]

    relay_success_rate = (relay_result["success_count"] / total_relay) * 100 if total_relay > 0 else 0
    litellm_success_rate = (litellm_result["success_count"] / total_litellm) * 100 if total_litellm > 0 else 0

    print(f"📊 SUCCESS RATE")
    print(f"   Relay:    {relay_success_rate:>6.1f}% ({relay_result['success_count']}/{total_relay})")
    print(f"   LiteLLM:  {litellm_success_rate:>6.1f}% ({litellm_result['success_count']}/{total_litellm})")

    if relay_success_rate >= litellm_success_rate:
        print(f"   Winner:   🏆 RELAY ({relay_success_rate - litellm_success_rate:+.1f}%)")
    else:
        print(f"   Winner:   🏆 LITELLM ({litellm_success_rate - relay_success_rate:+.1f}%)")

    print(f"\n⚡ PERFORMANCE METRICS")

    # Throughput
    rps_improvement = pct_improvement(relay_result["requests_per_second"], litellm_result["requests_per_second"])
    print(f"   Throughput:")
    print(f"     Relay:    {relay_result['requests_per_second']:>6.1f} req/s")
    print(f"     LiteLLM:  {litellm_result['requests_per_second']:>6.1f} req/s")
    print(f"     Difference: {rps_improvement}")

    if relay_result["requests_per_second"] > litellm_result["requests_per_second"]:
        print(f"     Winner:   🏆 RELAY")
    elif litellm_result["requests_per_second"] > relay_result["requests_per_second"]:
        print(f"     Winner:   🏆 LITELLM")
    else:
        print(f"     Winner:   🤝 TIE")

    # Average Latency (lower is better)
    if relay_result["avg_latency"] > 0 and litellm_result["avg_latency"] > 0:
        latency_improvement = pct_improvement(litellm_result["avg_latency"], relay_result["avg_latency"])
        print(f"\n   Average Latency:")
        print(f"     Relay:    {ms(relay_result['avg_latency']):>9}")
        print(f"     LiteLLM:  {ms(litellm_result['avg_latency']):>9}")

        if relay_result["avg_latency"] < litellm_result["avg_latency"]:
            faster_by = ((litellm_result["avg_latency"] - relay_result["avg_latency"]) / litellm_result["avg_latency"]) * 100
            print(f"     Relay is: {faster_by:.1f}% FASTER")
            print(f"     Winner:   🏆 RELAY")
        elif litellm_result["avg_latency"] < relay_result["avg_latency"]:
            faster_by = ((relay_result["avg_latency"] - litellm_result["avg_latency"]) / relay_result["avg_latency"]) * 100
            print(f"     LiteLLM is: {faster_by:.1f}% FASTER")
            print(f"     Winner:   🏆 LITELLM")
        else:
            print(f"     Winner:   🤝 TIE")

        # P95 Latency
        print(f"\n   P95 Latency:")
        print(f"     Relay:    {ms(relay_result['p95_latency']):>9}")
        print(f"     LiteLLM:  {ms(litellm_result['p95_latency']):>9}")

        if relay_result["p95_latency"] < litellm_result["p95_latency"]:
            better_by = ((litellm_result["p95_latency"] - relay_result["p95_latency"]) / litellm_result["p95_latency"]) * 100
            print(f"     Relay is: {better_by:.1f}% BETTER")
            print(f"     Winner:   🏆 RELAY")
        elif litellm_result["p95_latency"] < relay_result["p95_latency"]:
            better_by = ((relay_result["p95_latency"] - litellm_result["p95_latency"]) / relay_result["p95_latency"]) * 100
            print(f"     LiteLLM is: {better_by:.1f}% BETTER")
            print(f"     Winner:   🏆 LITELLM")
        else:
            print(f"     Winner:   🤝 TIE")

    print(f"\n📊 CAPACITY ESTIMATES (daily)")
    relay_daily = relay_result["requests_per_second"] * 60 * 60 * 24
    litellm_daily = litellm_result["requests_per_second"] * 60 * 60 * 24
    print(f"   Relay:    {relay_daily:>10,.0f} requests/day")
    print(f"   LiteLLM:  {litellm_daily:>10,.0f} requests/day")

    print(f"\n🎯 FINAL VERDICT")
    print("="*50)

    # Overall scoring
    relay_wins = 0
    litellm_wins = 0

    # Success rate point
    if relay_success_rate > litellm_success_rate:
        relay_wins += 1
    elif litellm_success_rate > relay_success_rate:
        litellm_wins += 1

    # Throughput point
    if relay_result["requests_per_second"] > litellm_result["requests_per_second"]:
        relay_wins += 1
    elif litellm_result["requests_per_second"] > relay_result["requests_per_second"]:
        litellm_wins += 1

    # Latency point (lower is better)
    if relay_result["avg_latency"] > 0 and litellm_result["avg_latency"] > 0:
        if relay_result["avg_latency"] < litellm_result["avg_latency"]:
            relay_wins += 1
        elif litellm_result["avg_latency"] < relay_result["avg_latency"]:
            litellm_wins += 1

    if relay_wins > litellm_wins:
        print("🏆 OVERALL WINNER: RELAY")
        print(f"   Score: Relay {relay_wins} - {litellm_wins} LiteLLM")
    elif litellm_wins > relay_wins:
        print("🏆 OVERALL WINNER: LITELLM")
        print(f"   Score: LiteLLM {litellm_wins} - {relay_wins} Relay")
    else:
        print("🤝 OVERALL RESULT: TIE")
        print(f"   Score: {relay_wins} - {litellm_wins}")

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
        auth="Bearer sk-relay-secret-change-me"
    )

    litellm_config = ServerConfig(
        name="LiteLLM (Original)",
        url="http://localhost:4000",
        endpoint="/chat/completions",  # No /v1/ prefix for LiteLLM
        model="gpt-4.1-mini",  # Same model name for both servers
        auth="Bearer sk-relay-secret-change-me"
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
        # Test scenarios with high concurrency
        scenarios = [
            {"name": "Moderate Load", "requests": 50, "concurrency": 20},
            {"name": "High Load", "requests": 100, "concurrency": 40},
        ]

        for scenario in scenarios:
            print(f"\n{'='*70}")
            print(f"🎯 Test Scenario: {scenario['name']}")
            print(f"   Requests: {scenario['requests']}, Concurrency: {scenario['concurrency']}")
            print("="*70)

            # Run both benchmarks concurrently for fairness
            relay_result, litellm_result = await asyncio.gather(
                benchmark_server(relay_config, scenario["requests"], scenario["concurrency"]),
                benchmark_server(litellm_config, scenario["requests"], scenario["concurrency"])
            )

            print_comparison(relay_result, litellm_result)

    except KeyboardInterrupt:
        print("\n⏹️  Benchmark cancelled by user")
    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())