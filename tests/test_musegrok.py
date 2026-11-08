import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from switchit import agent_sessions, core, demo_data, devin


class MuseGrokTests(unittest.TestCase):
    """Importers for Muse Code and Grok Build, built from public documentation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchit-musegrok-")
        self.addCleanup(shutil.rmtree, self.tmp)

    @staticmethod
    def muse_envelope(seq, payload_type, payload, session_id="01a1040f", recorded_us=1791145226590500):
        return {"schema_version": 1, "id": f"rec-{seq}",
                "stream": {"kind": "session", "id": session_id},
                "sequence": seq, "recorded_at": recorded_us,
                "record_type": "event", "durability": "durable",
                "causation_id": None, "payload_type": payload_type,
                "payload_schema_version": 1, "payload": payload}

    def test_muse_session_parses_runs_tools_and_usage(self):
        session_dir = Path(self.tmp) / "muse" / "sessions" / "2026" / "11" / "08" / "01a1040f"
        session_dir.mkdir(parents=True)
        lines = [
            json.dumps({"retained_frame": "session_permission_transaction", "frame_schema_version": 1, "children": []}),
            json.dumps(self.muse_envelope(3, "runtime.session.metadata", {"kind": "metadata", "record": {
                "workspace_root": "/repo", "model_id": "muse-spark-2", "provider_id": "meta"}})),
            json.dumps(self.muse_envelope(4, "runtime.session", {"kind": "run", "run_id": "run-1",
                "event": {"kind": "started", "prompt": "Fix the flaky test"}})),
            json.dumps(self.muse_envelope(5, "runtime.session", {"kind": "run", "run_id": "run-1",
                "event": {"kind": "assistant_tool_calls_committed", "message_id": "m1",
                          "tool_calls": [{"name": "bash", "call_id": "call_1", "id": "fc_1",
                                          "args": json.dumps({"command": "pytest -x"})}]}})),
            json.dumps(self.muse_envelope(6, "runtime.session", {"kind": "run", "run_id": "run-1",
                "event": {"kind": "tool_result_batch_committed", "batch_id": "m1",
                          "results": [{"tool_call_id": "call_1", "tool_call_index": 0, "text": "1 passed"}]}})),
            json.dumps(self.muse_envelope(7, "runtime.session", {"kind": "run", "run_id": "run-1",
                "event": {"kind": "assistant_message_committed", "message_id": "m2",
                          "text": "The flaky test is fixed."}})),
            json.dumps(self.muse_envelope(8, "runtime.session", {"kind": "run", "run_id": "run-1",
                "event": {"kind": "model_completed", "model": "muse-spark-2", "finish_reason": "stop",
                          "duration_ms": 4200,
                          "usage": {"input_tokens": 900, "output_tokens": 120}}}))
        ]
        (session_dir / "session.jsonl").write_text("\n".join(lines), encoding="utf-8")
        raws = agent_sessions.import_muse(session_dir / "session.jsonl")
        calls = [core.make_call(raw, "agents", "muse") for raw in raws]
        store = core.build_store(calls)
        trace = store["traces"][0]
        self.assertEqual(trace["trace_id"], "muse:01a1040f")
        self.assertIn("Fix the flaky test", " ".join(trace["user_requests"]))
        self.assertIn("bash", trace["tool_names"])
        assistant = [c for c in calls if c["response"]["text"] == "The flaky test is fixed."][0]
        self.assertEqual(assistant["model"], "muse-spark-2")
        self.assertEqual(assistant["usage"]["basis"], "recorded")
        self.assertEqual(assistant["usage"]["input_tokens"], 900)
        tool_results = [c for c in calls if c["request"]["messages"]
                        and c["request"]["messages"][0].get("role") == "tool"]
        self.assertTrue(any("1 passed" in c["request"]["messages"][0]["content"] for c in tool_results))

    def test_muse_tree_walks_subagent_logs(self):
        parent = Path(self.tmp) / "muse" / "s" / "sess-1"
        child = parent / "subagent" / "child-1"
        child.mkdir(parents=True)
        parent_log = [
            json.dumps(self.muse_envelope(1, "runtime.session",
                         {"kind": "run", "run_id": "r1", "event": {"kind": "started", "prompt": "parent task"}},
                         session_id="sess-1")),
        ]
        (parent / "session.jsonl").write_text("\n".join(parent_log), encoding="utf-8")
        child_log = [
            json.dumps(self.muse_envelope(1, "runtime.session",
                         {"kind": "run", "run_id": "r1", "event": {"kind": "started", "prompt": "child task"}},
                         session_id="child-1")),
        ]
        (child / "session.jsonl").write_text("\n".join(child_log), encoding="utf-8")
        raws = agent_sessions.import_muse_tree(Path(self.tmp) / "muse" / "s")
        trace_ids = {raw["trace_id"] for raw in raws}
        self.assertIn("muse:sess-1", trace_ids)
        self.assertIn("muse:child-1", trace_ids)

    def test_muse_redaction_applies(self):
        session_dir = Path(self.tmp) / "muse" / "x"
        session_dir.mkdir(parents=True)
        (session_dir / "session.jsonl").write_text(json.dumps(
            self.muse_envelope(1, "runtime.session",
                               {"kind": "run", "run_id": "r", "event": {"kind": "started",
                                "prompt": "my password: Sup3rSecret! and mail me@x.io"}}),
        ), encoding="utf-8")
        raws = agent_sessions.import_muse(session_dir / "session.jsonl")
        calls = [core.make_call(raw, "agents", "muse") for raw in raws]
        store = core.build_store(calls)
        joined = json.dumps(store)
        self.assertNotIn("Sup3rSecret!", joined)
        self.assertIn("[EMAIL]", joined)

    def test_grok_build_documented_schema(self):
        session_dir = Path(self.tmp) / "grok" / "sessions" / "%2Frepo" / "uuid-1"
        session_dir.mkdir(parents=True)
        (session_dir / "summary.json").write_text(json.dumps({
            "info": {"id": "uuid-1", "cwd": "/repo"},
            "generated_title": "Refactor auth", "current_model_id": "grok-4",
            "session_kind": "worktree",
        }), encoding="utf-8")
        (session_dir / "system_prompt.txt").write_text("You are Grok, a coding agent.", encoding="utf-8")
        (session_dir / "chat_history.jsonl").write_text("\n".join([
            json.dumps({"type": "user", "content": "<user_query>Refactor the auth module</user_query>"}),
            json.dumps({"type": "assistant", "model_id": "grok-4", "content": "On it.",
                        "tool_calls": [{"id": "call_1", "name": "edit_file",
                                        "arguments": json.dumps({"path": "auth.py"})}]}),
            json.dumps({"type": "tool_result", "tool_call_id": "call_1", "content": "file edited"}),
            json.dumps({"type": "assistant", "model_id": "grok-4", "content": "Auth refactored."}),
            json.dumps({"type": "reasoning", "encrypted_content": "opaque"}),
        ]), encoding="utf-8")
        (session_dir / "signals.json").write_text(json.dumps({"input_tokens": 5000, "output_tokens": 400}), encoding="utf-8")
        (session_dir / "updates.jsonl").write_text(json.dumps({
            "timestamp": 1, "method": "session/update",
            "params": {"update": {"sessionUpdate": "turn_completed", "stopReason": "end_turn"}}}), encoding="utf-8")
        raws = agent_sessions.import_grok_build(session_dir)
        calls = [core.make_call(raw, "agents", "grok") for raw in raws]
        store = core.build_store(calls)
        trace = store["traces"][0]
        self.assertEqual(trace["trace_id"], "grok-build:uuid-1")
        self.assertIn("Refactor the auth module", " ".join(trace["user_requests"]))
        self.assertIn("edit_file", trace["tool_names"])
        final = [c for c in calls if c["response"]["text"] == "Auth refactored."][0]
        self.assertEqual(final["model"], "grok-4")
        self.assertEqual(final["response"]["finish_reason"], "end_turn")
        # Session-level usage totals land on the last assistant call, labeled.
        self.assertEqual(final["usage"]["basis"], "recorded")
        self.assertEqual(final["usage"]["estimator"], "grok_signals_session_totals")
        self.assertEqual(final["usage"]["input_tokens"], 5000)
        system = [m for m in calls[1]["request"]["messages"] if m["role"] == "system"]
        self.assertTrue(system and "Grok" in system[0]["content"])
        tool_result = [c for c in calls if c["request"]["messages"]
                       and c["request"]["messages"][0].get("role") == "tool"]
        self.assertTrue(any("file edited" in c["request"]["messages"][0]["content"] for c in tool_result))

    def test_grok_build_missing_directory_returns_empty(self):
        self.assertEqual(agent_sessions.import_grok_build(Path(self.tmp) / "nope"), [])


if __name__ == "__main__":
    unittest.main()
