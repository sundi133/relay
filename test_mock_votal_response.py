#!/usr/bin/env python3
"""
Test what Relay would return if Votal server was properly blocking content
"""

# This simulates what LiteLLM gets when Votal server works correctly
mock_votal_response = {
    "safe": False,
    "action": "block",
    "guardrail_results": [
        {
            "guardrail": "adversarial_detection",
            "passed": False,
            "action": "block",
            "message": "Unsafe [harmful_content] (confidence: 1.00)",
            "details": {
                "is_adversarial": True,
                "attack_type": "harmful_content",
                "confidence": 1.0
            }
        },
        {
            "guardrail": "safety_check",
            "passed": False,
            "action": "block",
            "message": "Unsafe [harmful_content]",
            "details": {
                "safe": False,
                "category": "harmful_content"
            }
        },
        {
            "guardrail": "topic_restriction",
            "passed": False,
            "action": "block",
            "message": "Message is off-topic — detected: illegal_activities",
            "details": {
                "related": False,
                "topics": ["illegal_activities"],
                "allowed_scope": ["weapons", "illegal_activities"]
            }
        }
    ],
    "inference_time_ms": 651.07
}

# Format like Relay would return it
relay_response = {
    "detail": {
        "error": {
            "message": str({
                "info": "Request blocked by Votal guardrails",
                "blocked_guardrails": mock_votal_response["guardrail_results"],
                "total_blocked": 3,
                "inference_time_ms": 651.07
            }),
            "type": "None",
            "param": "None",
            "code": "400"
        }
    }
}

print("🔍 What Relay would return with a working Votal server:")
print(relay_response)