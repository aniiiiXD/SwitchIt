"""A small synthetic dataset embedded for offline demos and tests.

Everything here is synthetic: invented support-conversation traces over three
workflows (order status, password reset, refunds) with mixed usage provenance,
one failed call, tool proposals, and one message deliberately containing an
email address and a phone number so redaction can be observed end to end.

No part of this module is derived from, or may be replaced by, any assessment
or third-party dataset.
"""

from __future__ import annotations

DEMO_JSONL = "\n".join(
    [
        # --- workflow 1: order status lookups (session s-100) ----------------
        '{"trace_id": "t-order-001", "session_id": "s-100", "call_id": "c-001", "call_index": 0,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 820,'
        ' "input_tokens": 512, "output_tokens": 96, "usage_basis": "source_reported",'
        ' "messages": [{"role": "system", "content": "You are a retail support agent."},'
        ' {"role": "user", "content": "Where is my order 88412? It was supposed to arrive Tuesday."}],'
        ' "tools": [{"name": "get_order_status", "description": "Look up order status by id",'
        ' "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}}}],'
        ' "output": {"message": {"role": "assistant", "content": "",'
        ' "tool_calls": [{"name": "get_order_status", "arguments": {"order_id": "88412"}}]},'
        ' "finish_reason": "tool_calls"}}',
        '{"trace_id": "t-order-001", "session_id": "s-100", "call_id": "c-002", "call_index": 1,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 640,'
        ' "input_tokens": 688, "output_tokens": 118, "usage_basis": "source_reported",'
        ' "messages": [{"role": "tool", "content": "{\\"status\\": \\"in transit\\", \\"eta\\": \\"Thursday\\"}"}],'
        ' "output": {"text": "Your order 88412 is in transit and should arrive Thursday.",'
        ' "finish_reason": "stop"}}',
        '{"trace_id": "t-order-002", "session_id": "s-100", "call_id": "c-003", "call_index": 0,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 910,'
        ' "input_tokens": 534, "output_tokens": 88, "usage_basis": "source_reported",'
        ' "messages": [{"role": "user", "content": "Check the delivery status for order 90155 please."}],'
        ' "output": {"message": {"role": "assistant", "content": "",'
        ' "tool_calls": [{"name": "get_order_status", "arguments": {"order_id": "90155"}}]},'
        ' "finish_reason": "tool_calls"}}',
        '{"trace_id": "t-order-002", "session_id": "s-100", "call_id": "c-004", "call_index": 1,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 700,'
        ' "input_tokens": 704, "output_tokens": 102, "usage_basis": "estimated", "usage_estimator": "cl100k_base",'
        ' "messages": [{"role": "tool", "content": "{\\"status\\": \\"delivered\\"}"}],'
        ' "output": {"text": "Order 90155 was delivered yesterday afternoon.", "finish_reason": "stop"}}',
        '{"trace_id": "t-order-003", "session_id": "s-101", "call_id": "c-005", "call_index": 0,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 880,'
        ' "input_tokens": 501, "output_tokens": 91, "usage_basis": "source_reported",'
        ' "messages": [{"role": "user", "content": "I need a tracking update for order 77023."}],'
        ' "output": {"message": {"role": "assistant", "content": "",'
        ' "tool_calls": [{"name": "get_order_status", "arguments": {"order_id": "77023"}}]},'
        ' "finish_reason": "tool_calls"}}',
        '{"trace_id": "t-order-003", "session_id": "s-101", "call_id": "c-006", "call_index": 1,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 655,'
        ' "input_tokens": 690, "output_tokens": 110, "usage_basis": "estimated", "usage_estimator": "cl100k_base",'
        ' "messages": [{"role": "tool", "content": "{\\"status\\": \\"in transit\\"}"}],'
        ' "output": {"text": "Order 77023 is in transit with the carrier now.", "finish_reason": "stop"}}',
        # --- workflow 2: password resets (contains deliberate secrets) -------
        '{"trace_id": "t-pass-001", "session_id": "s-200", "call_id": "c-007", "call_index": 0,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 540,'
        ' "input_tokens": 410, "output_tokens": 74, "usage_basis": "source_reported",'
        ' "messages": [{"role": "user", "content": "I cannot sign in. My email is dana@example.com and my phone is +1 (555) 010-9988. Reset my password."}],'
        ' "tools": [{"name": "reset_password", "description": "Send a password reset link",'
        ' "parameters": {"type": "object", "properties": {"email": {"type": "string"}}}}],'
        ' "output": {"message": {"role": "assistant", "content": "",'
        ' "tool_calls": [{"name": "reset_password", "arguments": {"email": "dana@example.com"}}]},'
        ' "finish_reason": "tool_calls"}}',
        '{"trace_id": "t-pass-001", "session_id": "s-200", "call_id": "c-008", "call_index": 1,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 480,'
        ' "input_tokens": 560, "output_tokens": 66, "usage_basis": "source_reported",'
        ' "messages": [{"role": "tool", "content": "{\\"sent\\": true}"}],'
        ' "output": {"text": "I sent a password reset link to your email address on file.", "finish_reason": "stop"}}',
        '{"trace_id": "t-pass-002", "session_id": "s-200", "call_id": "c-009", "call_index": 0,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 505,'
        ' "input_tokens": 395, "output_tokens": 70, "usage_basis": "source_reported",'
        ' "messages": [{"role": "user", "content": "Password reset needed for my account, the login keeps failing."}],'
        ' "output": {"message": {"role": "assistant", "content": "",'
        ' "tool_calls": [{"name": "reset_password", "arguments": {"email": "account-on-file"}}]},'
        ' "finish_reason": "tool_calls"}}',
        '{"trace_id": "t-pass-002", "session_id": "s-200", "call_id": "c-010", "call_index": 1,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 460,'
        ' "input_tokens": 545, "output_tokens": 61, "usage_basis": "unknown",'
        ' "messages": [{"role": "tool", "content": "{\\"sent\\": true}"}],'
        ' "output": {"text": "The reset link is on its way to your inbox.", "finish_reason": "stop"}}',
        # --- workflow 3: refund requests, including a failed call -----------
        '{"trace_id": "t-refund-001", "session_id": "s-300", "call_id": "c-011", "call_index": 0,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 1200,'
        ' "input_tokens": 980, "output_tokens": 132, "usage_basis": "source_reported",'
        ' "messages": [{"role": "user", "content": "The headphones arrived broken. I want a refund for order 88412."}],'
        ' "tools": [{"name": "issue_refund", "description": "Refund an order line",'
        ' "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}}}}],'
        ' "output": {"message": {"role": "assistant", "content": "",'
        ' "tool_calls": [{"name": "issue_refund", "arguments": {"order_id": "88412", "reason": "arrived damaged"}}]},'
        ' "finish_reason": "tool_calls"}}',
        '{"trace_id": "t-refund-001", "session_id": "s-300", "call_id": "c-012", "call_index": 1,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 940,'
        ' "input_tokens": 1204, "output_tokens": 154, "usage_basis": "source_reported",'
        ' "messages": [{"role": "tool", "content": "{\\"refund_id\\": \\"R-2201\\", \\"amount\\": 79.5}"}],'
        ' "output": {"text": "Your refund of 79.50 for order 88412 is approved and will post in 3-5 days.", "finish_reason": "stop"}}',
        '{"trace_id": "t-refund-002", "session_id": "s-300", "call_id": "c-013", "call_index": 0,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 1010,'
        ' "input_tokens": 902, "output_tokens": 120, "usage_basis": "source_reported",'
        ' "messages": [{"role": "user", "content": "Requesting my money back for the damaged lamp, order 61208."}],'
        ' "output": {"message": {"role": "assistant", "content": "",'
        ' "tool_calls": [{"name": "issue_refund", "arguments": {"order_id": "61208", "reason": "damaged item"}}]},'
        ' "finish_reason": "tool_calls"}}',
        '{"trace_id": "t-refund-002", "session_id": "s-300", "call_id": "c-014", "call_index": 1,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 1100,'
        ' "input_tokens": 1150, "output_tokens": 140, "usage_basis": "unknown",'
        ' "messages": [{"role": "tool", "content": "{\\"refund_id\\": \\"R-2310\\", \\"amount\\": 42.0}"}],'
        ' "output": {"text": "Refund R-2310 for 42.00 has been issued for order 61208.", "finish_reason": "stop"}}',
        '{"trace_id": "t-refund-003", "session_id": "s-301", "call_id": "c-015", "call_index": 0,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 3000,'
        ' "input_tokens": 870, "output_tokens": null, "usage_basis": "source_reported",'
        ' "messages": [{"role": "user", "content": "I still have not been refunded for the cracked mug, order 55119. This is the second time I ask."}],'
        ' "output": {"error": "upstream timeout after 3000 ms: provider_rate_limit", "finish_reason": "error"}}',
        # --- workflow 4: singleton with only tool evidence -------------------
        '{"trace_id": "t-misc-001", "session_id": null, "call_id": "c-016", "call_index": 0,'
        ' "provider": "openai", "model": "gpt-4o-mini", "latency_ms": 610,'
        ' "input_tokens": 430, "output_tokens": 80, "usage_basis": "source_reported",'
        ' "messages": [{"role": "user", "content": "hi"}],'
        ' "output": {"message": {"role": "assistant", "content": "",'
        ' "tool_calls": [{"name": "lookup_store_hours", "arguments": {}}]}, "finish_reason": "tool_calls"}}',
    ]
)

DEMO_APP_CONFIG = {
    "name": "retail-support-demo",
    "prompts": [
        {"id": "system_main", "role": "system", "template": "You are a retail support agent. Help with orders, sign-in, and refunds."},
    ],
    "tools": [
        {
            "name": "get_order_status",
            "description": "Look up order status by id",
            "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
        },
        {
            "name": "reset_password",
            "description": "Send a password reset link",
            "parameters": {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]},
        },
        {
            "name": "issue_refund",
            "description": "Refund an order line",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["order_id", "reason"],
            },
        },
    ],
    "provider": {"provider": "openai", "model": "gpt-4o-mini", "base_url": None},
}

# A deliberately minimal OTLP document for the OTLP importer path in demos/tests.
DEMO_OTLP = {
    "resourceSpans": [
        {
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "demo-app"}}]},
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "name": "chat gpt-4o-mini",
                            "traceId": "0xaf3e",
                            "spanId": "0x11",
                            "startTimeUnixNano": 1700000000000000000,
                            "endTimeUnixNano": 1700000000900000000,
                            "attributes": [
                                {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                                {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o-mini"}},
                                {"key": "session.id", "value": {"stringValue": "s-otlp"}},
                                {"key": "gen_ai.prompt.0.role", "value": {"stringValue": "user"}},
                                {"key": "gen_ai.prompt.0.content", "value": {"stringValue": "What is the status of order 88412?"}},
                                {"key": "gen_ai.completion.0.role", "value": {"stringValue": "assistant"}},
                                {"key": "gen_ai.completion.0.content", "value": {"stringValue": "It is in transit."}},
                                {"key": "gen_ai.usage.input_tokens", "value": {"intValue": 88}},
                                {"key": "gen_ai.usage.output_tokens", "value": {"intValue": 12}},
                            ],
                        }
                    ]
                }
            ],
        }
    ]
}

# A deliberately minimal Langfuse-style export for the langfuse importer path.
DEMO_LANGFUSE = {
    "data": [
        {
            "id": "t-lf-001",
            "sessionId": "s-lf",
            "observations": [
                {
                    "id": "obs-1",
                    "type": "GENERATION",
                    "model": "gpt-4o-mini",
                    "startTime": "2026-01-01T00:00:00Z",
                    "endTime": "2026-01-01T00:00:01Z",
                    "input": {"messages": [{"role": "user", "content": "Reset the password for my account."}]},
                    "output": {"text": "Password reset link sent.", "finish_reason": "stop"},
                    "usage": {"input": 150, "output": 30, "unit": "TOKENS"},
                },
                {
                    "id": "obs-2",
                    "type": "GENERATION",
                    "model": "gpt-4o-mini",
                    "startTime": "2026-01-01T00:00:02Z",
                    "endTime": "2026-01-01T00:00:03Z",
                    "level": "ERROR",
                    "statusMessage": "upstream checkout tool unreachable",
                    "input": {"messages": [{"role": "user", "content": "I still have not been refunded for order 55119."}]},
                    "usage": {"input": 870, "output": None, "unit": "TOKENS"},
                },
            ],
        }
    ]
}


def demo_lines() -> list:
    """Return the demo JSONL as a list of lines."""
    return [line for line in DEMO_JSONL.split("\n") if line.strip()]
