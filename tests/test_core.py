"""Tests for core: importers, redaction, usage reconciliation, mapping, cases."""

import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from switchit import agent_sessions, core, demo_data, devin


class RedactionTests(unittest.TestCase):
    def test_email_is_redacted(self):
        self.assertEqual(
            core.redact_text("contact dana@example.com today"),
            "contact [EMAIL] today",
        )

    def test_password_assignment_is_redacted(self):
        text = core.redact_text("password: hunter2 please")
        self.assertNotIn("hunter2", text)
        self.assertIn("[REDACTED]", text)

    def test_phone_number_is_redacted(self):
        text = core.redact_text("call me at +1 (555) 010-9988 now")
        self.assertNotIn("555", text.replace("[NUMBER]", ""))
        self.assertIn("[NUMBER]", text)

    def test_aws_and_sk_keys_are_redacted(self):
        text = core.redact_text("keys AKIAIOSFODNN7EXAMPLE and sk-abcdefghijklmnop1234")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", text)
        self.assertNotIn("sk-abcdefghijklmnop1234", text)

    def test_normal_text_survives(self):
        self.assertEqual(core.redact_text("Where is my order 88412?"), "Where is my order 88412?")

    def test_excerpt_elides_head_and_tail(self):
        text = "x" * 5000 + "ENDMARK" + "y" * 5000
        cut = core.excerpt(text, limit=200)
        self.assertLess(len(cut), 260)
        self.assertTrue(cut.startswith("x"))
        self.assertTrue(cut.endswith("y"))
        self.assertIn("…", cut)

    def test_redact_messages_never_leak_secrets(self):
        cleaned = core.redact_messages([{"role": "user", "content": "email me at a@b.co or 1234567890"}])
        self.assertEqual(cleaned[0]["content"], "email me at [EMAIL] or [NUMBER]")


class JsonlImportTests(unittest.TestCase):
    def setUp(self):
        self.lines = demo_data.demo_lines()

    def test_demo_dataset_imports_completely(self):
        raw_calls = core.parse_jsonl_lines(self.lines, "demo")
        calls = [core.make_call(raw, "demo", "demo") for raw in raw_calls]
        store = core.build_store(calls)
        # 16 calls, 9 traces; every call counted exactly once.
        self.assertEqual(store["totals"]["calls"], 16)
        self.assertEqual(store["totals"]["traces"], 9)
        counted = sum(bucket["calls"] for bucket in store["totals"]["usage"].values())
        self.assertEqual(counted, 16)

    def test_usage_provenance_stays_separate(self):
        raw_calls = core.parse_jsonl_lines(self.lines, "demo")
        calls = [core.make_call(raw, "demo", "demo") for raw in raw_calls]
        store = core.build_store(calls)
        usage = store["totals"]["usage"]
        self.assertEqual(usage["recorded"]["calls"], 12)
        self.assertEqual(usage["estimated"]["calls"], 2)
        self.assertEqual(usage["unknown"]["calls"], 2)
        # The two estimated calls declare cl100k_base as their estimator.
        estimators = {call["usage"]["estimator"] for call in calls if call["usage"]["basis"] == "estimated"}
        self.assertEqual(estimators, {"cl100k_base"})

    def test_missing_trace_id_raises(self):
        with self.assertRaises(ValueError):
            core.make_call({"messages": []}, "demo", "nowhere")

    def test_trace_aggregates_tools_and_errors(self):
        raw_calls = core.parse_jsonl_lines(self.lines, "demo")
        calls = [core.make_call(raw, "demo", "demo") for raw in raw_calls]
        store = core.build_store(calls)
        traces = {trace["trace_id"]: trace for trace in store["traces"]}
        self.assertEqual(traces["t-order-001"]["tool_names"], ["get_order_status"])
        self.assertEqual(traces["t-refund-001"]["call_count"], 2)
        self.assertEqual(traces["t-refund-003"]["error_count"], 1)
        # The deliberate secrets never reach the store.
        joined = json.dumps(store)
        self.assertNotIn("dana@example.com", joined)
        self.assertIn("[EMAIL]", joined)

    def test_sessions_are_linked(self):
        raw_calls = core.parse_jsonl_lines(self.lines, "demo")
        calls = [core.make_call(raw, "demo", "demo") for raw in raw_calls]
        store = core.build_store(calls)
        sessions = {session["session_id"]: session for session in store["sessions"]}
        self.assertIn("s-100", sessions)
        self.assertEqual(sorted(sessions["s-100"]["trace_ids"]), ["t-order-001", "t-order-002"])

    def test_response_message_shape_keeps_tool_calls(self):
        raw_calls = core.parse_jsonl_lines(self.lines, "demo")
        calls = [core.make_call(raw, "demo", "demo") for raw in raw_calls]
        first = [call for call in calls if call["call_id"] == "c-001"][0]
        self.assertEqual(first["response"]["tool_calls"][0]["name"], "get_order_status")
        self.assertEqual(first["response"]["tool_calls"][0]["arguments"], {"order_id": "88412"})

    def test_tool_call_arguments_as_json_string_are_parsed(self):
        call = core.make_call(
            {"trace_id": "t", "response": {"tool_calls": [{"name": "lookup", "arguments": '{"x": 1}'}]}},
            "test", "test",
        )
        self.assertEqual(call["response"]["tool_calls"][0]["arguments"], {"x": 1})


class OtlpImportTests(unittest.TestCase):
    def test_otlp_import_maps_gen_ai_attributes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "otlp.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(demo_data.DEMO_OTLP, stream)
            raw_calls = core.import_otlp(path)
            calls = [core.make_call(raw, "otlp", path) for raw in raw_calls]
        self.assertEqual(len(calls), 1)  # the plain HTTP span must be skipped
        call = calls[0]
        self.assertEqual(call["provider"], "openai")
        self.assertEqual(call["model"], "gpt-4o-mini")
        self.assertEqual(call["latency_ms"], 900)
        self.assertEqual(call["usage"]["basis"], "recorded")
        self.assertEqual(call["usage"]["input_tokens"], 88)
        self.assertEqual(call["usage"]["output_tokens"], 12)
        self.assertEqual(call["request"]["messages"][0]["content"], "What is the status of order 88412?")
        self.assertEqual(call["session_id"], "s-otlp")


class LangfuseImportTests(unittest.TestCase):
    def test_langfuse_import_maps_generations_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "langfuse.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(demo_data.DEMO_LANGFUSE, stream)
            raw_calls = core.import_langfuse(path)
            calls = [core.make_call(raw, "langfuse", path) for raw in raw_calls]
        self.assertEqual(len(calls), 2)
        by_id = {call["call_id"]: call for call in calls}
        self.assertEqual(by_id["obs-1"]["usage"]["basis"], "recorded")
        self.assertEqual(by_id["obs-1"]["latency_ms"], 1000)
        # The second generation carries level=ERROR; the error must survive import.
        self.assertIn("unreachable", by_id["obs-2"]["response"]["error"])
        self.assertEqual(by_id["obs-2"]["usage"]["basis"], "recorded")
        self.assertIsNone(by_id["obs-2"]["usage"]["output_tokens"])
        self.assertEqual(by_id["obs-2"]["session_id"], "s-lf")


class ParquetImportTests(unittest.TestCase):
    def test_parquet_import_roundtrip(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            self.skipTest("pyarrow not installed")
        table = pa.table(
            {
                "trace_id": ["t-1", "t-1"],
                "span_id": ["s1", "s2"],
                "call_index": [0, 1],
                "input_tokens": [100, 150],
                "output_tokens": [10, None],
                "usage_basis": ["source_reported", "estimated_cl100k_base"],
                "input_messages_json": ['[{"role":"user","content":"hi there"}]', "[]"],
                "output_messages_json": ["[]", "[]"],
                "extra_json": ["{}", '{"latency_ms": 250}'],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "traces.parquet")
            pq.write_table(table, path)
            raw_calls = core.import_parquet(path)
            calls = [core.make_call(raw, "parquet", path) for raw in raw_calls]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["usage"]["basis"], "recorded")
        self.assertEqual(calls[1]["usage"]["basis"], "estimated")
        self.assertIsNone(calls[1]["usage"]["output_tokens"])
        self.assertEqual(calls[1]["latency_ms"], 250)

    def test_parquet_zip_without_parquet_inside_is_rejected(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "bundle.zip")
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("not_the_parquet.txt", "nope")
            with self.assertRaises(ValueError):
                core.import_parquet(zip_path)


class WorkflowGroupingTests(unittest.TestCase):
    def setUp(self):
        raw_calls = core.parse_jsonl_lines(demo_data.demo_lines(), "demo")
        calls = [core.make_call(raw, "demo", "demo") for raw in raw_calls]
        self.store = core.build_store(calls)
        self.workflow_map = core.group_workflows(self.store)

    def test_related_traces_cluster_together(self):
        by_name = {}
        for workflow in self.workflow_map["workflows"]:
            by_name[tuple(sorted(workflow["trace_ids"]))] = workflow
        order = [wf for wf in self.workflow_map["workflows"] if "t-order-001" in wf["trace_ids"]][0]
        self.assertIn("t-order-002", order["trace_ids"])
        self.assertIn("t-order-003", order["trace_ids"])
        self.assertIn("get_order_status", order["tool_names"])
        refund = [wf for wf in self.workflow_map["workflows"] if "t-refund-001" in wf["trace_ids"]][0]
        self.assertIn("t-refund-002", refund["trace_ids"])

    def test_uncertainty_signals_are_present(self):
        singletons = [wf for wf in self.workflow_map["workflows"] if len(wf["trace_ids"]) == 1]
        self.assertTrue(singletons)
        self.assertTrue(all("single_trace_cluster" in wf["uncertainty"] for wf in singletons))
        failed = [wf for wf in self.workflow_map["workflows"] if "t-refund-003" in wf["trace_ids"]]
        self.assertTrue(failed and "contains_failed_calls" in failed[0]["uncertainty"])

    def test_labels_declare_themselves_as_heuristic(self):
        for workflow in self.workflow_map["workflows"]:
            self.assertEqual(workflow["label_source"], "heuristic")
            self.assertFalse(workflow["edited"])

    def test_every_trace_is_assigned_or_listed_unassigned(self):
        assigned = set()
        for workflow in self.workflow_map["workflows"]:
            assigned.update(workflow["trace_ids"])
        self.assertEqual(
            assigned | set(self.workflow_map["unassigned_trace_ids"]),
            {trace["trace_id"] for trace in self.store["traces"]},
        )

    def test_apply_edits_preserves_reviewer_changes(self):
        edited = json.loads(json.dumps(self.workflow_map))
        target = [wf for wf in edited["workflows"] if "t-order-001" in wf["trace_ids"]][0]
        target["edited"] = True
        target["name"] = "Order status lookups (reviewed)"
        target["trace_ids"] = ["t-order-001", "t-order-002", "t-order-003"]
        refreshed = core.group_workflows(self.store)
        merged = core.apply_edits(refreshed, edited)
        kept = [wf for wf in merged["workflows"] if wf.get("edited")]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "Order status lookups (reviewed)")
        self.assertEqual(sorted(kept[0]["trace_ids"]), ["t-order-001", "t-order-002", "t-order-003"])
        # Edited traces are not duplicated into automatic groups.
        assigned = []
        for workflow in merged["workflows"]:
            if not workflow.get("edited"):
                assigned.extend(workflow["trace_ids"])
        self.assertNotIn("t-order-001", assigned)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        raw_calls = core.parse_jsonl_lines(demo_data.demo_lines(), "demo")
        calls = [core.make_call(raw, "demo", "demo") for raw in raw_calls]
        self.document = core.build_profile(core.build_store(calls))
        self.profile = self.document["profile"]

    def test_totals_and_usage(self):
        self.assertEqual(self.profile["totals"]["calls"], 16)
        self.assertEqual(self.profile["totals"]["traces"], 9)
        self.assertEqual(self.profile["totals"]["sessions"], 5)
        self.assertEqual(self.profile["totals"]["calls_with_errors"], 1)
        self.assertEqual(self.profile["usage"]["recorded"]["calls"], 12)

    def test_system_prompts_surfaced(self):
        prompts = self.profile["system_prompts"]
        self.assertTrue(prompts)
        self.assertIn("retail support agent", prompts[0]["excerpt"])

    def test_tool_inventory_with_inferred_schema(self):
        tools = {tool["name"]: tool for tool in self.profile["tools"]}
        self.assertIn("get_order_status", tools)
        schema = tools["get_order_status"]["schema_inferred"]
        self.assertEqual(schema["properties"]["order_id"]["type"], "string")
        self.assertIn("order_id", schema.get("required", []))
        self.assertEqual(schema["inferred_from"], 3)
        self.assertEqual(tools["get_order_status"]["proposals"], 3)

    def test_provider_mix(self):
        providers = self.profile["providers"]
        self.assertEqual(providers[0]["provider"], "openai")
        self.assertEqual(providers[0]["model"], "gpt-4o-mini")
        self.assertEqual(providers[0]["calls"], 16)

    def test_error_samples_present(self):
        self.assertEqual(len(self.profile["error_samples"]), 1)
        self.assertIn("timeout", self.profile["error_samples"][0]["error"])

    def test_inferred_app_config_is_reusable_shape(self):
        config = self.document["app_config_inferred"]
        self.assertTrue(config["inferred"])
        self.assertEqual(config["provider"]["model"], "gpt-4o-mini")
        names = {tool["name"] for tool in config["tools"]}
        self.assertIn("get_order_status", names)
        for tool in config["tools"]:
            self.assertIn("properties", tool["parameters"])

    def test_schema_inference_mixed_types_falls_back_to_string(self):
        schema = core.infer_tool_schema([{"a": 1}, {"a": "x"}])
        self.assertEqual(schema["properties"]["a"]["type"], "string")
        self.assertIn("a", schema["required"])  # presence, not type, drives required

    def test_schema_inference_requires_only_persistent_keys(self):
        schema = core.infer_tool_schema([{"a": 1, "optional": "x"}, {"a": 2}])
        self.assertEqual(schema["required"], ["a"])
        self.assertIn("optional", schema["properties"])

    def test_schema_inference_empty_is_safe(self):
        schema = core.infer_tool_schema([])
        self.assertEqual(schema["inferred_from"], 0)


class AgentSessionTests(unittest.TestCase):
    """Tests for coding-agent session importers (synthetic fixtures only)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchit-agents-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, relative, content):
        path = Path(self.tmp) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_codex_rollout_parses_calls_tools_and_usage(self):
        lines = [
            json.dumps({"type": "session_meta", "payload": {
                "session_id": "abc-123", "model_provider": "openai",
                "base_instructions": {"text": "You are Codex, an agent. Email admin@corp.io."}}}),
            json.dumps({"type": "event_msg", "payload": {"info": {
                "total_token_usage": {"input_tokens": 1200, "output_tokens": 85}}}}),
            json.dumps({"type": "response_item", "payload": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "Fix the failing test in app.py"}]}}),
            json.dumps({"type": "response_item", "payload": {
                "type": "function_call", "name": "shell",
                "arguments": json.dumps({"command": ["pytest", "-x"]}), "call_id": "c1"}}),
            json.dumps({"type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "c1", "output": "1 failed"}}),
            json.dumps({"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Fixed the import order."}]}}),
            "not json at all",
        ]
        path = self.write("codex/rollout-test.jsonl", "\n".join(lines))
        raws = agent_sessions.import_codex(path)
        calls = [core.make_call(raw, "agents", str(path)) for raw in raws]
        store = core.build_store(calls)
        self.assertEqual(store["totals"]["calls"], 4)  # user turn, fn call, fn output, assistant
        by_model = {}
        for call in calls:
            by_model.setdefault(call["model"], []).append(call)
        tool_call = [c for c in calls if c["response"]["tool_calls"]][0]
        self.assertEqual(tool_call["response"]["tool_calls"][0]["name"], "shell")
        self.assertEqual(tool_call["response"]["tool_calls"][0]["arguments"], {"command": ["pytest", "-x"]})
        assistant = [c for c in calls if c["response"]["text"] == "Fixed the import order."][0]
        self.assertEqual(assistant["usage"]["basis"], "recorded")
        self.assertEqual(assistant["usage"]["output_tokens"], 85)
        system = [m for call in calls for m in call["request"]["messages"] if m["role"] == "system"]
        self.assertTrue(any("Codex" in m["content"] for m in system))
        # Redaction applies to agent content too.
        joined = json.dumps(store)
        self.assertNotIn("admin@corp.io", joined)

    def test_codex_zstd_rollouts_are_read(self):
        try:
            from compression import zstd  # Python 3.14+
        except ImportError:
            try:
                import zstandard  # noqa: F401
            except ImportError:
                self.skipTest("no zstd support available")
        lines = [
            json.dumps({"type": "session_meta", "payload": {"session_id": "zst-1"}}),
            json.dumps({"type": "response_item", "payload": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "compressed history"}]}}),
        ]
        raw_text = "\n".join(lines)
        path = Path(self.tmp) / "codex" / "rollout-zst.jsonl.zst"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from compression import zstd

            with zstd.open(path, "wt", encoding="utf-8") as stream:
                stream.write(raw_text)
        except ImportError:
            import zstandard

            with zstandard.open(path, "wt", encoding="utf-8") as stream:
                stream.write(raw_text)
        raws = agent_sessions.import_codex(path)
        calls = [core.make_call(raw, "agents", str(path)) for raw in raws]
        self.assertTrue(any(c["response"]["text"] == "compressed history" for c in calls))

    def test_codex_thread_catalog_tags_cloud_vs_local(self):
        catalog_db = Path(self.tmp) / "codex-dev.db"
        connection = sqlite3.connect(catalog_db)
        connection.execute(
            "CREATE TABLE local_thread_catalog (host_id TEXT, thread_id TEXT,"
            " display_title TEXT, source_created_at REAL, source_updated_at REAL,"
            " cwd TEXT, source_kind TEXT, source_detail TEXT, model_provider TEXT,"
            " git_branch TEXT)"
        )
        connection.execute(
            "INSERT INTO local_thread_catalog VALUES ('h', 'abc-123', 'Refactor auth flow',"
            " 1.0, 2.0, '/repo', 'codex-cli', NULL, 'openai', 'main')"
        )
        connection.commit()
        connection.close()
        rollout = self.write(
            "codex/rollout-abc.jsonl",
            "\n".join([
                json.dumps({"type": "session_meta", "payload": {"session_id": "abc-123"}}),
                json.dumps({"type": "response_item", "payload": {
                    "type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": "Refactored the auth flow."}]}}),
            ]),
        )
        sessions = [{"agent": "codex", "path": str(rollout)}]
        try:
            meta = agent_sessions.collect_session_meta(sessions, catalog_db=catalog_db)
            self.assertEqual(meta["codex:abc-123"]["title"], "Refactor auth flow")
            self.assertEqual(meta["codex:abc-123"]["origin"], "local")
            self.assertEqual(meta["codex:abc-123"]["cwd"], "/repo")
            # build_store merges the metadata into the trace.
            raws = agent_sessions.import_agent_sessions(sessions)
            calls = [core.make_call(raw, "agents", "codex") for raw in raws]
            store = core.build_store(calls, session_meta=meta)
            trace = store["traces"][0]
            self.assertEqual(trace["title"], "Refactor auth flow")
            self.assertEqual(trace["origin"], "local")
        finally:
            pass

    def test_claude_code_transcript_parses_tools_results_and_usage(self):
        lines = [
            json.dumps({"type": "user", "sessionId": "s1", "timestamp": "2026-11-01T10:00:00Z",
                        "message": {"role": "user", "content": "Please refactor utils.py"}}),
            json.dumps({"type": "assistant", "sessionId": "s1", "timestamp": "2026-11-01T10:00:05Z",
                        "message": {"role": "assistant", "model": "claude-fable-5", "stop_reason": "tool_use",
                                    "content": [{"type": "tool_use", "id": "t1", "name": "Read",
                                                 "input": {"file_path": "/x/utils.py"}}],
                                    "usage": {"input_tokens": 500, "output_tokens": 40}}}),
            json.dumps({"type": "user", "sessionId": "s1", "timestamp": "2026-11-01T10:00:07Z",
                        "message": {"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": "t1",
                             "content": "def util(): pass"}]}}),
            json.dumps({"type": "assistant", "sessionId": "s1", "timestamp": "2026-11-01T10:00:09Z",
                        "message": {"role": "assistant", "model": "claude-fable-5", "stop_reason": "end_turn",
                                    "content": [{"type": "text", "text": "Done, utils.py is clean."}],
                                    "usage": {"input_tokens": 900, "output_tokens": 60}}}),
            json.dumps({"type": "file-history-snapshot", "sessionId": "s1"}),
        ]
        path = self.write("claude/session-s1.jsonl", "\n".join(lines))
        raws = agent_sessions.import_claude_code(path)
        calls = [core.make_call(raw, "agents", str(path)) for raw in raws]
        store = core.build_store(calls)
        trace = store["traces"][0]
        self.assertEqual(trace["trace_id"], "claude-code:s1")
        self.assertIn("Read", trace["tool_names"])
        tool_result = [c for c in calls if c["request"]["messages"]
                       and c["request"]["messages"][0].get("role") == "tool"]
        self.assertTrue(any("def util" in c["request"]["messages"][0]["content"]
                            for c in tool_result))
        assistants = [c for c in calls if c["model"] == "claude-fable-5"]
        self.assertEqual(len(assistants), 2)
        self.assertEqual(assistants[0]["usage"]["basis"], "recorded")
        self.assertEqual(assistants[0]["usage"]["output_tokens"], 40)
        self.assertEqual(assistants[1]["response"]["text"], "Done, utils.py is clean.")

    def test_opencode_database_parses_messages_parts_and_tokens(self):
        db_path = Path(self.tmp) / "opencode.db"
        connection = sqlite3.connect(db_path)
        connection.executescript(
            "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,"
            " time_updated INTEGER, data TEXT);"
            "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,"
            " time_created INTEGER, time_updated INTEGER, data TEXT);"
        )
        connection.execute("INSERT INTO message VALUES ('m1', 'ses1', 1, 1, ?)", (json.dumps({
            "role": "user", "parts": []}),))
        connection.execute("INSERT INTO part VALUES ('p1', 'm1', 'ses1', 1, 1, ?)",
                           (json.dumps({"type": "text", "text": "Add a README"}),))
        connection.execute("INSERT INTO message VALUES ('m2', 'ses1', 2, 2, ?)", (json.dumps({
            "role": "assistant", "providerID": "fireworks-ai",
            "modelID": "kimi-k2p7", "finish": "stop",
            "tokens": {"input": 800, "output": 30}}),))
        connection.execute("INSERT INTO part VALUES ('p2', 'm2', 'ses1', 2, 2, ?)",
                           (json.dumps({"type": "text", "text": "README added."}),))
        connection.execute("INSERT INTO part VALUES ('p3', 'm2', 'ses1', 2, 2, ?)",
                           (json.dumps({"type": "tool", "tool": "write",
                                        "state": {"input": {"path": "README.md"}}}),))
        connection.commit()
        connection.close()
        raws = agent_sessions.import_opencode(db_path)
        calls = [core.make_call(raw, "agents", str(db_path)) for raw in raws]
        store = core.build_store(calls)
        trace = store["traces"][0]
        self.assertEqual(trace["trace_id"], "opencode:ses1")
        self.assertIn("write", trace["tool_names"])
        assistant = [c for c in calls if c["response"]["text"] == "README added."][0]
        self.assertEqual(assistant["model"], "kimi-k2p7")
        self.assertEqual(assistant["usage"]["basis"], "recorded")
        self.assertEqual(assistant["usage"]["output_tokens"], 30)

    def test_cursor_chat_store_parses_messages_and_system(self):
        chat_dir = Path(self.tmp) / "chats" / "ws1" / "chat1"
        chat_dir.mkdir(parents=True)
        (chat_dir / "meta.json").write_text(json.dumps(
            {"title": "Refactor", "cwd": "/tmp/project"}), encoding="utf-8")
        connection = sqlite3.connect(chat_dir / "store.db")
        connection.execute("CREATE TABLE blobs (id TEXT, data BLOB)")
        blobs = [
            json.dumps({"role": "system", "content": "You are an AI coding assistant, powered by Composer."}),
            json.dumps({"role": "user", "content": "Add a login page. My email is boss@corp.io."}),
            json.dumps({"role": "assistant", "id": "a1", "content": [
                {"type": "text", "text": "Working on it."},
                {"type": "toolCall", "toolCall": {"name": "edit_file",
                                                  "arguments": {"path": "login.tsx"}}},
            ]}),
            json.dumps({"role": "assistant", "id": "a2", "content": "Login page added."}),
        ]
        for blob in blobs:
            connection.execute("INSERT INTO blobs VALUES (?, ?)", (os.urandom(16).hex(), blob))
        connection.commit()
        connection.close()
        raws = agent_sessions.import_cursor(chat_dir)
        calls = [core.make_call(raw, "agents", str(chat_dir)) for raw in raws]
        store = core.build_store(calls)
        trace = store["traces"][0]
        self.assertEqual(trace["trace_id"], "cursor:chat1")
        self.assertIn("edit_file", trace["tool_names"])
        assistants = [c for c in calls if c["model"] not in ("user-turn", None) or c["response"]["text"]]
        first = [c for c in calls if c["response"]["text"] == "Working on it."][0]
        system = [m for m in first["request"]["messages"] if m["role"] == "system"]
        self.assertTrue(system and "Composer" in system[0]["content"])
        self.assertEqual(calls[-1]["usage"]["basis"], "unknown")  # Cursor reports no usage
        self.assertNotIn("boss@corp.io", json.dumps(store))

    def test_discovery_finds_fixture_folders_and_skips_missing_agents(self):
        self.write("codex/rollout-x.jsonl", json.dumps(
            {"type": "session_meta", "payload": {"session_id": "x"}}))
        sessions = agent_sessions.discover_agent_sessions(
            kinds=["codex"], limit=10)
        # Discovery reads real home folders; point it at the fixture via monkeypatching.
        original = agent_sessions.AGENT_DEFAULTS
        try:
            agent_sessions.AGENT_DEFAULTS = {
                "codex": [Path(self.tmp) / "codex"],
                "claude-code": [Path(self.tmp) / "claude"],
                "opencode": [Path(self.tmp) / "opencode.db"],
                "cursor": [Path(self.tmp) / "chats"],
            }
            sessions = agent_sessions.discover_agent_sessions(limit=10)
            self.assertEqual([entry["agent"] for entry in sessions], ["codex"])
            self.assertIn("rollout-x", sessions[0]["path"])
        finally:
            agent_sessions.AGENT_DEFAULTS = original

    def test_import_agent_sessions_skips_unreadable_files(self):
        raws = agent_sessions.import_agent_sessions([
            {"agent": "claude-code", "path": str(Path(self.tmp) / "does-not-exist.jsonl")},
            {"agent": "unknown-agent", "path": "whatever"},
        ])
        self.assertEqual(raws, [])


    def test_cline_sdk_session_parses_metrics_and_tools(self):
        session_dir = Path(self.tmp) / "cline" / "data" / "sessions" / "s1"
        session_dir.mkdir(parents=True)
        (session_dir / "s1.messages.json").write_text(json.dumps({
            "version": 1, "agent": "lead", "sessionId": "s1",
            "system_prompt": "You are Cline, a software engineering agent.",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Fix the bug"}]},
                {"role": "assistant", "modelInfo": {"id": "claude-fable-5", "provider": "anthropic"},
                 "metrics": {"inputTokens": 420, "outputTokens": 55},
                 "content": [
                     {"type": "text", "text": "On it."},
                     {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "app.py"}},
                 ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "name": "read_file",
                     "content": "def main(): pass"}]},
            ]}), encoding="utf-8")
        raws = agent_sessions.import_cline(Path(self.tmp) / "cline" / "data")
        calls = [core.make_call(raw, "agents", "cline") for raw in raws]
        store = core.build_store(calls)
        trace = store["traces"][0]
        self.assertEqual(trace["trace_id"], "cline:s1")
        self.assertIn("read_file", trace["tool_names"])
        assistant = [c for c in calls if c["model"] == "claude-fable-5"][0]
        self.assertEqual(assistant["usage"]["basis"], "recorded")
        self.assertEqual(assistant["usage"]["input_tokens"], 420)
        system = [m for m in assistant["request"]["messages"] if m["role"] == "system"]
        self.assertTrue(system and "Cline" in system[0]["content"])

    def test_cline_legacy_task_parses_conversation_and_usage(self):
        task_dir = Path(self.tmp) / "cline" / "data" / "tasks" / "1700000000000"
        task_dir.mkdir(parents=True)
        (task_dir / "api_conversation_history.json").write_text(json.dumps([
            {"role": "user", "content": "add tests"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Adding tests."},
                {"type": "tool_use", "id": "t9", "name": "write_to_file",
                 "input": {"path": "test_x.py", "content": "def test_x(): pass"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t9", "content": "file written"}]},
        ]), encoding="utf-8")
        (task_dir / "ui_messages.json").write_text(json.dumps([
            {"ts": 1, "type": "say", "say": "api_req_started",
             "text": json.dumps({"request": "claude", "tokensIn": 300, "tokensOut": 70})},
        ]), encoding="utf-8")
        raws = agent_sessions.import_cline(task_dir)
        calls = [core.make_call(raw, "agents", "cline-legacy") for raw in raws]
        store = core.build_store(calls)
        self.assertEqual(store["totals"]["calls"], 3)
        assistant = [c for c in calls if c["response"]["tool_calls"]][0]
        self.assertEqual(assistant["response"]["tool_calls"][0]["name"], "write_to_file")
        self.assertEqual(assistant["usage"]["basis"], "recorded")
        self.assertEqual(assistant["usage"]["input_tokens"], 300)
    def test_aider_history_is_task_level_evidence(self):
        history = self.write("proj/.aider.chat.history.md", "\n".join([
            "# aider chat started at 2026-11-07 10:00:00",
            "",
            "#### rename foo to bar  ",
            "Here is the change:",
            "",
            "> Applied edit to a.py  ",
            "> Commit abc1234 refactor  ",
            "",
        ]))
        raws = agent_sessions.import_aider(history)
        calls = [core.make_call(raw, "agents", str(history)) for raw in raws]
        store = core.build_store(calls)
        trace = store["traces"][0]
        self.assertTrue(trace["trace_id"].startswith("aider:"))
        self.assertIn("rename foo to bar", " ".join(trace["user_requests"]))
        tool_output_calls = [c for c in calls if c["request"]["messages"]
                             and c["request"]["messages"][0].get("role") == "tool"]
        self.assertTrue(any("Applied edit" in c["request"]["messages"][0]["content"]
                            for c in tool_output_calls))
        self.assertEqual(trace["tool_names"], [])  # no structured tool calls in aider
        self.assertEqual(trace["usage"]["unknown"]["calls"], trace["call_count"])

    def test_gemini_cli_session_applies_patches_and_records_usage(self):
        session_file = self.write("gemini/tmp/proj/chats/session-2026.jsonl", "\n".join([
            json.dumps({"sessionId": "gem-1", "projectHash": "h", "startTime": "2026-11-07T09:00:00Z",
                        "kind": "main"}),
            json.dumps({"id": "m1", "timestamp": "2026-11-07T09:00:10Z", "type": "user",
                        "content": "list the src directory"}),
            json.dumps({"id": "m2", "timestamp": "2026-11-07T09:00:12Z", "type": "gemini",
                        "content": "Checking the directory.",
                        "tokens": {"input": 2210, "output": 31, "total": 2285},
                        "model": "gemini-2.5-flash"}),
            json.dumps({"$patch": {"updates": [{"id": "m2", "toolCalls": [
                {"id": "tc1", "name": "list_directory", "args": {"dir_path": "/src"},
                 "result": [{"functionResponse": {"response": {"output": "index.ts"}}}],
                 "status": "success"}]}]}}),
        ]))
        raws = agent_sessions.import_gemini_cli(session_file)
        calls = [core.make_call(raw, "agents", str(session_file)) for raw in raws]
        store = core.build_store(calls)
        trace = store["traces"][0]
        self.assertEqual(trace["trace_id"], "gemini-cli:gem-1")
        self.assertIn("list_directory", trace["tool_names"])
        assistant = [c for c in calls if c["model"] == "gemini-2.5-flash"][0]
        self.assertEqual(assistant["usage"]["basis"], "recorded")
        self.assertEqual(assistant["usage"]["input_tokens"], 2210)
        tool_result = [c for c in calls if c["request"]["messages"]
                       and c["request"]["messages"][0].get("role") == "tool"]
        self.assertTrue(any("index.ts" in c["request"]["messages"][0]["content"] for c in tool_result))

    def test_copilot_cli_events_parse_prompts_tools_and_usage(self):
        events = [
            {"id": "e1", "timestamp": "2026-11-07T08:00:00Z", "type": "session.start",
             "data": {"sessionId": "cop-1", "context": {"cwd": "/repo"},
                      "selectedModel": "claude-sonnet-5"}},
            {"id": "e2", "timestamp": "2026-11-07T08:00:01Z", "type": "system.message",
             "data": {"content": "You are Copilot, a coding agent."}},
            {"id": "e3", "timestamp": "2026-11-07T08:00:05Z", "type": "user.message",
             "data": {"content": "Add a test", "messageId": "u1"}},
            {"id": "e4", "timestamp": "2026-11-07T08:00:09Z", "type": "assistant.message",
             "data": {"messageId": "a1", "model": "claude-sonnet-5", "content": "Writing it.",
                      "toolRequests": [{"toolCallId": "call_1", "name": "apply_patch",
                                        "arguments": {"patch": "..."}}]}},
            {"id": "e5", "timestamp": "2026-11-07T08:00:11Z", "type": "tool.execution_complete",
             "data": {"toolCallId": "call_1", "success": True,
                      "result": {"content": "patch applied"}}},
            {"id": "e6", "timestamp": "2026-11-07T08:00:12Z", "type": "session.usage_record",
             "data": {"usage": {"model": "claude-sonnet-5", "inputTokens": 1500,
                                "outputTokens": 120}}},
            {"id": "e7", "timestamp": "2026-11-07T08:00:13Z", "type": "assistant.message",
             "data": {"messageId": "a2", "model": "claude-sonnet-5",
                      "content": "Test added and passing."}},
        ]
        session_dir = Path(self.tmp) / "copilot" / "session-state" / "cop-1"
        session_dir.mkdir(parents=True)
        (session_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events), encoding="utf-8")
        raws = agent_sessions.import_copilot_cli(session_dir / "events.jsonl")
        calls = [core.make_call(raw, "agents", "copilot") for raw in raws]
        store = core.build_store(calls)
        trace = store["traces"][0]
        self.assertEqual(trace["trace_id"], "copilot-cli:cop-1")
        self.assertIn("apply_patch", trace["tool_names"])
        final = [c for c in calls if c["response"]["text"] == "Test added and passing."][0]
        self.assertEqual(final["usage"]["basis"], "recorded")
        self.assertEqual(final["usage"]["output_tokens"], 120)
        system = [m for m in calls[1]["request"]["messages"] if m["role"] == "system"]
        self.assertTrue(system and "Copilot" in system[0]["content"])


class DevinTests(unittest.TestCase):
    """Tests for the Devin cloud importer (mocked API, no network)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchit-devin-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_session_objects_become_task_level_traces(self):
        sessions = [
            {"session_id": "dev-1", "prompt": "Migrate the auth module", "status": "exit",
             "pull_requests": [{"pr_url": "https://github.com/x/y/pull/9"}],
             "acus_consumed": 12},
            {"session_id": "dev-2", "title": "Add tests", "status": "running"},
        ]
        raws = devin.import_devin(sessions)
        calls = [core.make_call(raw, "devin", "api.devin.ai") for raw in raws]
        store = core.build_store(
            calls,
            session_meta={"devin:dev-1": {"origin": "cloud (Devin)"},
                          "devin:dev-2": {"origin": "cloud (Devin)"}},
        )
        traces = {t["trace_id"]: t for t in store["traces"]}
        self.assertIn("devin:dev-1", traces)
        self.assertEqual(traces["devin:dev-1"]["origin"], "cloud (Devin)")
        self.assertIn("Migrate the auth module", " ".join(traces["devin:dev-1"]["user_requests"]))
        # The outcome summary lives on the call's response, derived from metadata.
        outcome_call = [c for c in calls if c["trace_id"] == "devin:dev-1"][0]
        self.assertIn("pull_requests=1", outcome_call["response"]["text"])
        self.assertIn("status=exit", outcome_call["response"]["text"])
        # ACUs are compute units, not tokens: they must never enter token totals.
        self.assertNotIn("acus_consumed=12".split("=")[1], json.dumps(traces["devin:dev-1"]["usage"]))

    def test_remote_import_requires_env_credentials(self):
        saved = {key: os.environ.get(key) for key in ("DEVIN_API_KEY", "DEVIN_ORG_ID")}
        try:
            for key in saved:
                os.environ.pop(key, None)
            with self.assertRaises(devin.DevinError) as caught:
                devin.import_devin_remote()
            self.assertIn("DEVIN_API_KEY", str(caught.exception))
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_remote_import_follows_cursor_and_caps_limit(self):
        page_one = {"items": [{"session_id": f"dev-{i}", "status": "exit"} for i in range(50)],
                    "has_more": True, "end_cursor": "cursor-1"}
        page_two = {"items": [{"session_id": f"dev-{i}", "status": "exit"} for i in range(50, 80)],
                    "has_more": False}
        with mock.patch.object(devin, "_fetch_json", side_effect=[page_one, page_two]):
            raws = devin.import_devin_remote(limit=80, api_key="k", org_id="o")
        self.assertEqual(len(raws), 80)
        self.assertTrue(all(raw["trace_id"].startswith("devin:") for raw in raws))


class CaseSelectionTests(unittest.TestCase):
    def setUp(self):
        raw_calls = core.parse_jsonl_lines(demo_data.demo_lines(), "demo")
        calls = [core.make_call(raw, "demo", "demo") for raw in raw_calls]
        self.store = core.build_store(calls)
        self.workflow_map = core.group_workflows(self.store)
        self.cases_doc = core.select_cases(self.store, self.workflow_map, max_cases=24)

    def test_failed_trace_is_selected(self):
        failed = [case for case in self.cases_doc["cases"] if "failed" in case["origins"]]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["trace_id"], "t-refund-003")
        self.assertIsNotNone(failed[0]["historical"]["error"])

    def test_tool_checks_are_generated_from_trace_history(self):
        by_trace = {case["trace_id"]: case for case in self.cases_doc["cases"]}
        order_case = by_trace["t-order-001"]
        tool_checks = [check for check in order_case["checks"] if check["type"] == "tool_called"]
        self.assertEqual([check["name"] for check in tool_checks], ["get_order_status"])
        self.assertTrue(all(check["required"] for check in tool_checks))

    def test_no_error_check_is_always_first_and_required(self):
        for case in self.cases_doc["cases"]:
            self.assertEqual(case["checks"][0]["type"], "no_error")
            self.assertTrue(case["checks"][0]["required"])

    def test_max_cases_is_respected_and_failed_cases_kept(self):
        tiny = core.select_cases(self.store, self.workflow_map, max_cases=2)
        self.assertLessEqual(len(tiny["cases"]), 2)
        self.assertTrue(any("failed" in case["origins"] for case in tiny["cases"]))

    def test_boundaries_are_written_into_cases(self):
        joined = json.dumps(self.cases_doc)
        self.assertIn("not a success guarantee", joined)
        self.assertIn("never executed", joined)

    def test_recorded_tool_fixtures_are_extracted_from_history(self):
        by_trace = {case["trace_id"]: case for case in self.cases_doc["cases"]}
        order_case = by_trace["t-order-001"]
        fixtures = order_case.get("tool_fixtures") or {}
        self.assertIn("get_order_status", fixtures)
        self.assertIn("in transit", fixtures["get_order_status"])
        # Cases whose traces never called tools carry no fixture block.
        self.assertNotIn("tool_fixtures", by_trace["t-misc-001"])


class UsageAccountingTests(unittest.TestCase):
    def test_add_usage_counts_missing_values_per_field(self):
        usage = core.empty_usage()
        core.add_usage(usage, 10, 20, "recorded")
        core.add_usage(usage, None, 5, "recorded")
        core.add_usage(usage, None, None, "unknown")
        recorded = usage["recorded"]
        self.assertEqual(recorded["calls"], 2)
        self.assertEqual(recorded["input_tokens_known"], 10)
        self.assertEqual(recorded["input_missing_calls"], 1)
        self.assertEqual(recorded["calls_missing_either"], 1)
        self.assertEqual(usage["unknown"]["calls"], 1)

    def test_merge_usage_sums_without_double_counting(self):
        first = core.empty_usage()
        second = core.empty_usage()
        core.add_usage(first, 10, 10, "recorded")
        core.add_usage(second, 5, None, "estimated")
        merged = core.merge_usage(first, second)
        self.assertEqual(merged["recorded"]["input_tokens_known"], 10)
        self.assertEqual(merged["estimated"]["calls"], 1)
        self.assertEqual(merged["estimated"]["output_missing_calls"], 1)

    def test_basis_normalization_maps_source_reported(self):
        self.assertEqual(core.normalize_basis("source_reported")[0], "recorded")
        self.assertEqual(core.normalize_basis("estimated_cl100k_base")[0], "estimated")
        self.assertEqual(core.normalize_basis(None)[0], "unknown")

    def test_tokens_with_no_declared_basis_default_to_recorded(self):
        _, _, basis, _ = core.usage_from_record({"input_tokens": 5, "output_tokens": 6})
        self.assertEqual(basis, "recorded")


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data", "store.json")
            core.atomic_write_json(path, {"a": 1})
            with open(path, "r", encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), {"a": 1})
            self.assertEqual([name for name in os.listdir(os.path.dirname(path)) if name.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
