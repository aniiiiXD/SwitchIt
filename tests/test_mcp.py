"""Tests for the MCP server: protocol handling, tools, and safety guards."""

import json
import os
import shutil
import tempfile
import unittest

from switchit import mcp_server, providers as pv


def rpc(request_id, method, params=None):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def call_tool(name, arguments=None):
    return mcp_server.handle_message(
        rpc(99, "tools/call", {"name": name, "arguments": arguments or {}})
    )["result"]


class ProtocolTests(unittest.TestCase):
    def test_initialize_echoes_client_protocol_version(self):
        response = mcp_server.handle_message(rpc(1, "initialize", {"protocolVersion": "2024-11-05"}))
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(response["result"]["serverInfo"]["name"], "switchit")

    def test_initialize_without_version_uses_default(self):
        response = mcp_server.handle_message(rpc(1, "initialize", {}))
        self.assertEqual(response["result"]["protocolVersion"], mcp_server.PROTOCOL_VERSION)

    def test_initialized_notification_gets_no_response(self):
        self.assertIsNone(mcp_server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_ping(self):
        response = mcp_server.handle_message(rpc(2, "ping"))
        self.assertEqual(response["result"], {})

    def test_unknown_method_is_method_not_found(self):
        response = mcp_server.handle_message(rpc(3, "resources/list"))
        self.assertEqual(response["error"]["code"], -32601)

    def test_malformed_line_is_ignored_not_fatal(self):
        self.assertIsNone(mcp_server.handle_line("this is not json"))
        self.assertIsNone(mcp_server.handle_line(""))

    def test_handle_line_roundtrip(self):
        output = mcp_server.handle_line(json.dumps(rpc(4, "ping")))
        self.assertEqual(json.loads(output)["id"], 4)

    def test_tools_list_exposes_the_pipeline(self):
        response = mcp_server.handle_message(rpc(5, "tools/list"))
        names = [tool["name"] for tool in response["result"]["tools"]]
        for expected in ("switchit_import", "switchit_map", "switchit_cases",
                         "switchit_replay", "switchit_report", "switchit_run",
                         "switchit_status", "switchit_read_report", "switchit_list_providers"):
            self.assertIn(expected, names)

    def test_unknown_tool_reports_error_without_crashing(self):
        result = call_tool("switchit_nuke_everything")
        self.assertTrue(result["isError"])
        self.assertIn("Unknown tool", result["content"][0]["text"])


class ProviderToolTests(unittest.TestCase):
    def test_list_providers_mentions_presets_and_supermemory_boundary(self):
        result = call_tool("switchit_list_providers")
        text = result["content"][0]["text"]
        self.assertIn("grok", text)
        self.assertIn("ollama", text)
        self.assertIn("Supermemory", text)
        self.assertIn("never replay targets", text)


class PipelineToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchit-mcp-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.state_dir = os.path.join(self.tmp, "state")

    def test_full_demo_pipeline_through_mcp(self):
        result = call_tool("switchit_run", {"demo": True, "state_dir": self.state_dir})
        self.assertFalse(result["isError"])
        text = result["content"][0]["text"]
        self.assertIn("Report written", text)

        status = call_tool("switchit_status", {"state_dir": self.state_dir})
        self.assertIn("migration report: present", status["content"][0]["text"])
        self.assertIn("traces: 9", status["content"][0]["text"])

        report = call_tool("switchit_read_report", {"state_dir": self.state_dir})
        self.assertFalse(report["isError"])
        digest = json.loads(report["content"][0]["text"])
        self.assertEqual(sum(digest["verdicts"].values()), digest["case_count"])
        self.assertTrue(digest["boundaries"])

    def test_map_before_import_reports_error(self):
        result = call_tool("switchit_map", {"state_dir": os.path.join(self.tmp, "empty")})
        self.assertTrue(result["isError"])
        self.assertIn("import", result["content"][0]["text"])

    def test_live_target_without_remote_is_refused_through_mcp(self):
        call_tool("switchit_run", {"demo": True, "state_dir": self.state_dir})
        result = call_tool("switchit_replay", {"state_dir": self.state_dir, "target": "grok"})
        self.assertTrue(result["isError"])
        self.assertIn("--remote", result["content"][0]["text"])

    def test_replay_with_remote_but_no_key_fails_cleanly(self):
        call_tool("switchit_run", {"demo": True, "state_dir": self.state_dir})
        saved = {key: os.environ.get(key) for key in ("XAI_API_KEY", "GROK_API_KEY")}
        try:
            for key in saved:
                os.environ.pop(key, None)
            result = call_tool("switchit_replay",
                               {"state_dir": self.state_dir, "target": "grok",
                                "remote": True, "fresh": True})
            self.assertTrue(result["isError"])
            self.assertIn("XAI_API_KEY", result["content"][0]["text"])
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_tool_output_never_reaches_protocol_stdout_unparsed(self):
        # The CLI prints human output; the MCP layer must capture it into the
        # tool result. Verify the captured text contains the banner.
        result = call_tool("switchit_import", {"demo": True, "state_dir": self.state_dir})
        self.assertIn("Imported to", result["content"][0]["text"])


class PresetTests(unittest.TestCase):
    def test_grok_preset_resolves_to_xai_endpoint(self):
        adapter = pv.build_adapter("grok", {}, remote=True)
        self.assertIsInstance(adapter, pv.OpenAICompatibleAdapter)
        self.assertEqual(adapter.base_url, "https://api.x.ai/v1")
        self.assertEqual(adapter.model, "grok-4")

    def test_preset_overrides_win(self):
        adapter = pv.build_adapter("grok", {"model": "grok-3", "base_url": "http://localhost:9999/v1"}, remote=True)
        self.assertEqual(adapter.model, "grok-3")
        self.assertEqual(adapter.base_url, "http://localhost:9999/v1")

    def test_ollama_preset_needs_no_key(self):
        self.assertEqual(pv.PROVIDER_PRESETS["ollama"]["key_envs"], ())
        self.assertEqual(pv.read_preset_key("ollama"), None)

    def test_read_preset_key_reads_environment_only(self):
        adapter = pv.build_adapter("groq", {}, remote=True)
        saved = os.environ.get("GROQ_API_KEY")
        try:
            os.environ["GROQ_API_KEY"] = "test-key-123"
            self.assertEqual(pv.read_preset_key("groq"), "test-key-123")
        finally:
            if saved is None:
                os.environ.pop("GROQ_API_KEY", None)
            else:
                os.environ["GROQ_API_KEY"] = saved
        self.assertIsNone(adapter.api_key)  # key is injected by the caller, never inside build

    def test_vllm_without_model_is_rejected_with_guidance(self):
        with self.assertRaises(pv.ProviderError) as caught:
            pv.build_adapter("vllm", {}, remote=True)
        self.assertIn("--target-model", str(caught.exception))

    def test_unknown_preset_lists_known_ones(self):
        with self.assertRaises(pv.ProviderError) as caught:
            pv.build_adapter("skynet", {}, remote=True)
        self.assertIn("grok", str(caught.exception))

    def test_adapter_spec_for_preset_is_fingerprint_stable_and_secret_free(self):
        spec = pv.adapter_spec("grok", {"api_key": "super-secret"})
        self.assertNotIn("super-secret", json.dumps(spec))
        self.assertEqual(spec["preset"], "grok")
        self.assertEqual(spec["kind"], "openai")
        self.assertEqual(pv.adapter_spec("grok", {}), spec)


if __name__ == "__main__":
    unittest.main()
