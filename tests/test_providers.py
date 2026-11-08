"""Tests for provider adapters: offline behavior, remote guards, parsing."""

import json
import os
import unittest
from unittest import mock

from switchit import providers as pv


def sample_case():
    return {
        "case_id": "case_001",
        "request": {
            "messages": [
                {"role": "system", "content": "You are a support agent."},
                {"role": "user", "content": "Where is order 88412?"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"name": "get_order_status", "arguments": {"order_id": "88412"}}],
                },
                {"role": "tool", "content": '{"status": "in transit"}'},
                {"role": "user", "content": "Any update? It is urgent."},
            ],
            "tools": [
                {
                    "name": "get_order_status",
                    "description": "Look up order status",
                    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
                }
            ],
        },
        "historical": {
            "text": "Order 88412 is in transit and should arrive Thursday.",
            "tool_calls": [{"name": "get_order_status", "arguments": {"order_id": "88412"}}],
            "finish_reason": "stop",
            "error": None,
            "usage": {"input_tokens": 688, "output_tokens": 118, "basis": "recorded", "estimator": None},
            "latency_ms": 640,
            "model": "gpt-4o-mini",
            "call_id": "c-002",
        },
    }


class RecordedAdapterTests(unittest.TestCase):
    def test_replays_history_verbatim(self):
        adapter = pv.RecordedBaselineAdapter()
        result = adapter.replay(sample_case()["request"], sample_case())
        self.assertEqual(result["text"], "Order 88412 is in transit and should arrive Thursday.")
        self.assertEqual(result["tool_calls"][0]["name"], "get_order_status")
        self.assertEqual(result["latency_ms"], 640)
        self.assertEqual(result["usage"]["basis"], "recorded")
        self.assertEqual(result["usage"]["input_tokens"], 688)
        self.assertIsNone(result["error"])
        self.assertFalse(adapter.remote)

    def test_preserves_estimated_and_unknown_provenance(self):
        case = sample_case()
        case["historical"]["usage"] = {"input_tokens": None, "output_tokens": None, "basis": "unknown", "estimator": None}
        result = pv.RecordedBaselineAdapter().replay(case["request"], case)
        self.assertEqual(result["usage"]["basis"], "unknown")

    def test_requires_a_case(self):
        with self.assertRaises(pv.ProviderError):
            pv.RecordedBaselineAdapter().replay({"messages": []}, None)

    def test_capabilities_declared(self):
        capabilities = pv.RecordedBaselineAdapter().capabilities()
        self.assertTrue(capabilities["tool_calling"])
        self.assertIn("Recorded", capabilities["notes"])


class MockAdapterTests(unittest.TestCase):
    def test_mirror_reproduces_tools_and_error(self):
        case = sample_case()
        case["historical"]["error"] = "historical failure"
        result = pv.MockAdapter(mode="mirror").replay(case["request"], case)
        self.assertEqual(result["tool_calls"][0]["name"], "get_order_status")
        self.assertEqual(result["error"], "historical failure")
        self.assertTrue(result["text"].startswith("MOCK MIRROR:"))

    def test_echo_drops_tools(self):
        result = pv.MockAdapter(mode="echo").replay(sample_case()["request"], sample_case())
        self.assertIn("Any update", result["text"])  # echoes the last user message
        self.assertEqual(result["tool_calls"], [])

    def test_mirror_usage_provenance_follows_the_call(self):
        healthy = sample_case()
        result = pv.MockAdapter(mode="mirror").replay(healthy["request"], healthy)
        self.assertEqual(result["usage"]["basis"], "estimated")  # local estimate
        errored = sample_case()
        errored["historical"]["error"] = "historical failure"
        result = pv.MockAdapter(mode="mirror").replay(errored["request"], errored)
        self.assertEqual(result["usage"]["basis"], "unknown")  # failed calls report no usage

    def test_canned_returns_configured_calls(self):
        adapter = pv.MockAdapter(
            mode="canned",
            text="canned answer",
            tool_calls=[{"name": "issue_refund", "arguments": {"order_id": "1"}}],
        )
        result = adapter.replay(sample_case()["request"], sample_case())
        self.assertEqual(result["text"], "canned answer")
        self.assertEqual(result["tool_calls"][0]["name"], "issue_refund")

    def test_degrade_returns_text_but_no_tools(self):
        adapter = pv.MockAdapter(mode="degrade", text="degraded", tool_calls=[{"name": "x", "arguments": {}}])
        result = adapter.replay(sample_case()["request"], sample_case())
        self.assertEqual(result["text"], "degraded")
        self.assertEqual(result["tool_calls"], [])

    def test_fail_mode_reports_error_with_unknown_usage(self):
        result = pv.MockAdapter(mode="fail").replay(sample_case()["request"], sample_case())
        self.assertIn("mock provider error", result["error"])
        self.assertEqual(result["usage"]["basis"], "unknown")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(pv.ProviderError):
            pv.MockAdapter(mode="explode")

    def test_capabilities_reflect_mode(self):
        self.assertTrue(pv.MockAdapter(mode="mirror").capabilities()["tool_calling"])
        self.assertFalse(pv.MockAdapter(mode="degrade").capabilities()["tool_calling"])


class RemoteGuardTests(unittest.TestCase):
    def test_live_adapters_refuse_without_remote_opt_in(self):
        case = sample_case()
        for kind, model in (("openai", "gpt-4o-mini"), ("anthropic", "claude-sonnet-4-5"), ("bedrock", "amazon.nova-micro-v1:0")):
            adapter = pv.build_adapter(kind, {"model": model}, remote=False)
            with self.assertRaises(pv.RemoteDisabledError):
                adapter.replay(case["request"], case)

    def test_live_adapters_refuse_without_env_key(self):
        case = sample_case()
        saved = {key: os.environ.get(key) for key in (
            "SWITCHIT_OPENAI_API_KEY", "OPENAI_API_KEY", "SWITCHIT_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")}
        try:
            for key in saved:
                os.environ.pop(key, None)
            openai_adapter = pv.build_adapter("openai", {"model": "gpt-4o-mini"}, remote=True)
            with self.assertRaises(pv.ProviderError) as caught:
                openai_adapter.replay(case["request"], case)
            self.assertIn("SWITCHIT_OPENAI_API_KEY", str(caught.exception))
            anthropic_adapter = pv.build_adapter("anthropic", {"model": "claude-sonnet-4-5"}, remote=True)
            with self.assertRaises(pv.ProviderError) as caught:
                anthropic_adapter.replay(case["request"], case)
            self.assertIn("SWITCHIT_ANTHROPIC_API_KEY", str(caught.exception))
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_bedrock_without_boto3_reports_missing_dependency(self):
        adapter = pv.build_adapter("bedrock", {"model": "m"}, remote=True)
        try:
            import boto3  # noqa: F401
            has_boto3 = True
        except ImportError:
            has_boto3 = False
        if has_boto3:
            self.skipTest("boto3 installed; cannot test the missing-dependency path")
        with self.assertRaises(pv.ProviderError) as caught:
            adapter.replay({"messages": []}, None)
        self.assertIn("boto3", str(caught.exception))


class OpenAIAdapterTests(unittest.TestCase):
    def test_request_payload_and_response_parsing(self):
        adapter = pv.OpenAICompatibleAdapter(model="gpt-4o-mini", api_key="test", remote=True)
        body = {
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {"id": "call_1", "type": "function",
                             "function": {"name": "get_order_status", "arguments": '{"order_id": "88412"}'}}
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 321, "completion_tokens": 45},
        }
        result = adapter.parse_response(body, latency_ms=55)
        self.assertIsNone(result["error"])
        self.assertEqual(result["finish_reason"], "tool_calls")
        self.assertEqual(result["tool_calls"], [{"name": "get_order_status", "arguments": {"order_id": "88412"}}])
        self.assertEqual(result["usage"]["basis"], "recorded")
        self.assertEqual(result["usage"]["input_tokens"], 321)
        self.assertEqual(result["usage"]["output_tokens"], 45)

    def test_missing_usage_is_recorded_as_unknown(self):
        adapter = pv.OpenAICompatibleAdapter(model="gpt-4o-mini", api_key="test", remote=True)
        result = adapter.parse_response({"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]}, 10)
        self.assertEqual(result["usage"]["basis"], "unknown")

    def test_no_choices_is_an_error_not_a_crash(self):
        adapter = pv.OpenAICompatibleAdapter(model="gpt-4o-mini", api_key="test", remote=True)
        result = adapter.parse_response({"choices": []}, 10)
        self.assertIn("no choices", result["error"])

    def test_tool_role_messages_are_mapped_with_ids(self):
        converted = pv.to_openai_messages(sample_case()["request"]["messages"])
        self.assertEqual(converted[2]["tool_calls"][0]["id"], "call_0")
        self.assertEqual(converted[3]["tool_call_id"], "call_0")
        self.assertEqual(converted[4]["role"], "user")

    def test_tools_are_wrapped_in_function_schema(self):
        wrapped = pv.to_openai_tools(sample_case()["request"]["tools"])
        self.assertEqual(wrapped[0]["type"], "function")
        self.assertEqual(wrapped[0]["function"]["name"], "get_order_status")

    def test_http_error_becomes_error_result_not_exception(self):
        import urllib.error

        adapter = pv.OpenAICompatibleAdapter(model="gpt-4o-mini", api_key="test", remote=True)
        adapter.base_url = "http://127.0.0.1:1"  # nothing listens here
        result = adapter.replay(sample_case()["request"], sample_case())
        self.assertIsNotNone(result["error"])
        self.assertEqual(result["usage"]["basis"], "unknown")


class AnthropicAdapterTests(unittest.TestCase):
    def test_content_blocks_are_parsed(self):
        adapter = pv.AnthropicAdapter(model="claude-sonnet-4-5", api_key="test", remote=True)
        body = {
            "model": "claude-sonnet-4-5",
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "tu_1", "name": "get_order_status", "input": {"order_id": "88412"}},
            ],
            "usage": {"input_tokens": 400, "output_tokens": 60},
        }
        result = adapter.parse_response(body, latency_ms=70)
        self.assertEqual(result["text"], "Checking.")
        self.assertEqual(result["tool_calls"][0]["arguments"], {"order_id": "88412"})
        self.assertEqual(result["usage"]["basis"], "recorded")

    def test_system_is_extracted_and_tool_messages_flattened(self):
        system, converted = pv.to_anthropic_messages(sample_case()["request"]["messages"])
        self.assertEqual(system, "You are a support agent.")
        self.assertEqual(converted[0]["role"], "user")
        self.assertIn("[tool result]", converted[2]["content"])

    def test_tools_use_input_schema(self):
        wrapped = pv.to_anthropic_tools(sample_case()["request"]["tools"])
        self.assertIn("input_schema", wrapped[0])
        self.assertEqual(wrapped[0]["name"], "get_order_status")


class BedrockAdapterTests(unittest.TestCase):
    def test_parse_converse_response(self):
        body = {
            "output": {"message": {"role": "assistant", "content": [
                {"text": "Refund issued."},
                {"toolUse": {"toolUseId": "t1", "name": "issue_refund", "input": {"order_id": "88412"}}},
            ]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 500, "outputTokens": 80},
        }
        result = pv.BedrockConverseAdapter.parse_response(body, latency_ms=90, model="amazon.nova-micro-v1:0")
        self.assertEqual(result["text"], "Refund issued.")
        self.assertEqual(result["tool_calls"][0]["name"], "issue_refund")
        self.assertEqual(result["usage"]["basis"], "recorded")
        self.assertEqual(result["usage"]["input_tokens"], 500)

    def test_tool_config_wraps_tool_specs(self):
        config = pv.to_bedrock_tools(sample_case()["request"]["tools"])
        self.assertEqual(config["tools"][0]["toolSpec"]["name"], "get_order_status")
        self.assertIn("json", config["tools"][0]["toolSpec"]["inputSchema"])


class RegistryTests(unittest.TestCase):
    def test_build_adapter_known_kinds(self):
        for kind in pv.ADAPTER_KINDS:
            adapter = pv.build_adapter(kind, {"model": "m"}, remote=False)
            self.assertEqual(adapter.kind in ("offline", "live"), True)

    def test_build_adapter_unknown_kind_raises(self):
        with self.assertRaises(pv.ProviderError):
            pv.build_adapter("carrier-pigeon", {}, remote=False)

    def test_adapter_spec_contains_no_secrets(self):
        spec = pv.adapter_spec("openai", {"model": "gpt-4o-mini", "api_key": "super-secret", "base_url": "http://x"})
        self.assertNotIn("super-secret", json.dumps(spec))
        self.assertEqual(spec["kind"], "openai")
        self.assertEqual(spec["model"], "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
