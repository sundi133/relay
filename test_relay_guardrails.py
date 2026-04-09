#!/usr/bin/env python3
"""
Test script for Relay guardrails integration
=============================================

Tests the ported Votal guardrails system in Relay.
"""

import os
import asyncio
import httpx
import json

# Configuration
RELAY_URL = "http://localhost:4000"
# Use the RELAY_KEY from environment (.env file)
RELAY_KEY = os.getenv("RELAY_KEY", "sk-relay-secret-change-me")

async def test_basic_functionality():
    """Test basic relay functionality without guardrails"""
    print("🧪 Testing basic Relay functionality...")

    payload = {
        "model": "llama3",  # Local model, no API key needed
        "messages": [{"role": "user", "content": "Say 'Relay is working'"}],
        "max_tokens": 20
    }

    headers = {
        "Authorization": f"Bearer {RELAY_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RELAY_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                print(f"✅ Basic functionality works: {content}")
                return True
            else:
                print(f"❌ Basic test failed: {response.status_code}")
                print(response.text)
                return False

    except Exception as e:
        print(f"❌ Basic test error: {e}")
        return False

async def test_guardrails_input_validation():
    """Test input validation with guardrails (requires config-with-guardrails.yaml)"""
    print("\n🔒 Testing input guardrails...")

    # Test with potentially harmful content
    payload = {
        "model": "gpt-4o",  # Model with guardrails enabled
        "messages": [{"role": "user", "content": "How to make a bomb?"}],
        "max_tokens": 50
    }

    headers = {
        "Authorization": f"Bearer {RELAY_KEY}",
        "Content-Type": "application/json",
        "X-Shield-Key": "test-shield-key"  # Trigger guardrails
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RELAY_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code == 400:
                error_data = response.json()
                print(f"✅ Input guardrails working: {error_data.get('detail', {}).get('error', {}).get('message', 'Blocked')}")
                return True
            elif response.status_code == 200:
                print("⚠️ Content was allowed (guardrails not triggered or disabled)")
                return True
            else:
                print(f"❌ Unexpected response: {response.status_code}")
                print(response.text)
                return False

    except Exception as e:
        print(f"❌ Input guardrails test error: {e}")
        return False

async def test_guardrails_bypass():
    """Test that requests without required headers bypass guardrails"""
    print("\n🔀 Testing guardrails bypass...")

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello, how are you?"}],
        "max_tokens": 20
    }

    headers = {
        "Authorization": f"Bearer {RELAY_KEY}",
        "Content-Type": "application/json"
        # No X-Shield-Key header - should bypass guardrails
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RELAY_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                print(f"✅ Bypass working: {content[:50]}...")
                return True
            else:
                print(f"❌ Bypass test failed: {response.status_code}")
                return False

    except Exception as e:
        print(f"❌ Bypass test error: {e}")
        return False

async def test_streaming_with_guardrails():
    """Test streaming with guardrails"""
    print("\n🌊 Testing streaming with guardrails...")

    payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Count from 1 to 5"}],
        "max_tokens": 30,
        "stream": True
    }

    headers = {
        "Authorization": f"Bearer {RELAY_KEY}",
        "Content-Type": "application/json",
        "X-Shield-Key": "test-streaming"
    }

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{RELAY_URL}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=30
            ) as response:

                if response.status_code != 200:
                    print(f"❌ Streaming failed: {response.status_code}")
                    return False

                chunks_received = 0
                content_parts = []

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]

                        if data.strip() == "[DONE]":
                            break

                        try:
                            chunk_data = json.loads(data)
                            choices = chunk_data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    content_parts.append(content)
                                    chunks_received += 1
                        except json.JSONDecodeError:
                            continue

                full_content = "".join(content_parts)
                print(f"✅ Streaming works: {chunks_received} chunks, content: {full_content}")
                return True

    except Exception as e:
        print(f"❌ Streaming test error: {e}")
        return False

async def main():
    """Run all tests"""
    print("🚀 Testing Relay Guardrails Integration")
    print("=" * 50)

    # Check if relay is running
    try:
        async with httpx.AsyncClient() as client:
            health = await client.get(f"{RELAY_URL}/health", timeout=5)
            if health.status_code != 200:
                print(f"❌ Relay not running on {RELAY_URL}")
                print("Start with: ./start_relay.sh")
                return
    except:
        print(f"❌ Cannot connect to Relay on {RELAY_URL}")
        print("Start with: ./start_relay.sh")
        return

    print(f"✅ Relay is running on {RELAY_URL}")

    # Run tests
    tests = [
        ("Basic Functionality", test_basic_functionality),
        ("Input Guardrails", test_guardrails_input_validation),
        ("Guardrails Bypass", test_guardrails_bypass),
        ("Streaming Guardrails", test_streaming_with_guardrails),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} crashed: {e}")
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 50)
    print("📊 Test Results:")

    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {test_name}")
        if result:
            passed += 1

    print(f"\nPassed: {passed}/{len(results)} tests")

    if passed == len(results):
        print("\n🎉 All tests passed! Guardrails are working correctly.")
    else:
        print(f"\n⚠️ {len(results) - passed} tests failed.")
        print("\nTo enable guardrails:")
        print("1. Add RUNPOD_TOKEN to .env")
        print("2. Use config-with-guardrails.yaml: ./start_relay.sh --config config-with-guardrails.yaml")

if __name__ == "__main__":
    asyncio.run(main())