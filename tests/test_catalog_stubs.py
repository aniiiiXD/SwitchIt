import json
import unittest
from pathlib import Path

from switchit import agent_sessions, core


class CatalogStubTests(unittest.TestCase):
    """Title-level stubs for cloud threads with no local transcript."""

    def setUp(self):
        self.catalog = {
            "thread-1": {"title": "Refactor auth flow", "origin": "local", "cwd": "/repo"},
            "thread-2": {"title": "Plan the launch", "origin": "cloud (ChatGPT)", "cwd": None},
            "thread-3": {"title": "", "origin": "cloud (ChatGPT)", "cwd": None},
        }

    def test_stubs_skip_existing_local_rollouts(self):
        raws = agent_sessions.catalog_stub_calls(
            self.catalog, existing_trace_ids={"codex:thread-1", "chatgpt:thread-2"}
        )
        # thread-1 has a local rollout, thread-2 already stubbed, thread-3 has no title.
        self.assertEqual(raws, [])

    def test_stubs_created_for_cloud_threads_without_transcripts(self):
        raws = agent_sessions.catalog_stub_calls(self.catalog, existing_trace_ids=set())
        by_id = {raw["trace_id"]: raw for raw in raws}
        self.assertIn("chatgpt:thread-1", by_id)
        self.assertIn("chatgpt:thread-2", by_id)
        self.assertNotIn("chatgpt:thread-3", by_id)  # empty title skipped
        self.assertEqual(by_id["chatgpt:thread-2"]["origin"], "cloud (ChatGPT)")

    def test_stub_content_is_honest_about_server_side_transcript(self):
        raws = agent_sessions.catalog_stub_calls(self.catalog, existing_trace_ids=set())
        call = core.make_call(raws[0], "agents", "catalog")
        self.assertIn("stored server-side", call["response"]["text"])
        self.assertEqual(call["usage"]["basis"], "unknown")
        self.assertEqual(call["response"]["tool_calls"], [])


if __name__ == "__main__":
    unittest.main()
