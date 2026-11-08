"""End-to-end CLI tests: import → map → cases → replay → report, fully offline."""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from switchit import cli, demo_data


class CliEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchit-cli-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.state_dir = os.path.join(self.tmp, "state")
        self.examples_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples"
        )

    def run_cli(self, *argv):
        with mock.patch.object(cli.sys, "stdout", None):
            return cli.main(list(argv))

    def test_run_demo_produces_every_artifact(self):
        exit_code = self.run_cli("run", "--demo", "--state-dir", self.state_dir)
        self.assertEqual(exit_code, 0)
        for relative in (
            "store.json", "workflows.json", "cases.json",
            os.path.join("run", "results.json"), os.path.join("run", "run_state.json"),
            os.path.join("run", "report.json"), os.path.join("run", "report.html"),
        ):
            self.assertTrue(os.path.exists(os.path.join(self.state_dir, relative)), relative)

        with open(os.path.join(self.state_dir, "store.json"), "r", encoding="utf-8") as stream:
            store = json.load(stream)
        self.assertEqual(store["totals"]["calls"], 16)
        self.assertEqual(store["totals"]["traces"], 9)
        # Every call counted exactly once across the three usage bases.
        counted = sum(bucket["calls"] for bucket in store["totals"]["usage"].values())
        self.assertEqual(counted, 16)

        with open(os.path.join(self.state_dir, "run", "report.json"), "r", encoding="utf-8") as stream:
            report = json.load(stream)
        verdicts = report["verdicts"]
        self.assertEqual(sum(verdicts.values()), len(report["cases"]))
        # The historically failed trace stays visible as an error.
        self.assertGreaterEqual(verdicts["error"], 1)
        self.assertGreaterEqual(verdicts["pass"], 1)

    def test_no_secrets_anywhere_in_outputs(self):
        self.run_cli("run", "--demo", "--state-dir", self.state_dir)
        secrets = ("dana@example.com", "+1 (555) 010-9988", "555) 010-9988")
        for root, _dirs, files in os.walk(self.state_dir):
            for name in files:
                with open(os.path.join(root, name), "r", encoding="utf-8") as stream:
                    content = stream.read()
                for secret in secrets:
                    self.assertNotIn(secret, content, f"{name} leaked {secret}")
        # Redaction markers prove the redactor actually ran.
        with open(os.path.join(self.state_dir, "store.json"), "r", encoding="utf-8") as stream:
            self.assertIn("[EMAIL]", stream.read())

    def test_stepwise_pipeline_with_example_files(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        examples = os.path.join(repo_root, "examples")
        self.run_cli(
            "import",
            "--jsonl", os.path.join(examples, "traces.jsonl"),
            "--otlp", os.path.join(examples, "otlp_traces.json"),
            "--langfuse", os.path.join(examples, "langfuse_export.json"),
            "--app-config", os.path.join(examples, "app_config.json"),
            "--state-dir", self.state_dir,
        )
        self.run_cli("map", "--state-dir", self.state_dir)
        self.run_cli("cases", "--state-dir", self.state_dir)
        self.run_cli("replay", "--state-dir", self.state_dir)
        self.run_cli("report", "--state-dir", self.state_dir)
        with open(os.path.join(self.state_dir, "store.json"), "r", encoding="utf-8") as stream:
            store = json.load(stream)
        # 16 jsonl + 2 otlp gen_ai spans + 2 langfuse generations = 20 calls.
        self.assertEqual(store["totals"]["calls"], 20)

    def test_map_keep_edits_preserves_reviewer_changes(self):
        self.run_cli("run", "--demo", "--state-dir", self.state_dir)
        with open(os.path.join(self.state_dir, "workflows.json"), "r", encoding="utf-8") as stream:
            workflows = json.load(stream)
        target = [wf for wf in workflows["workflows"] if "t-order-001" in wf["trace_ids"]][0]
        target["edited"] = True
        target["name"] = "Order tracking (human reviewed)"
        with open(os.path.join(self.state_dir, "workflows.json"), "w", encoding="utf-8") as stream:
            json.dump(workflows, stream)
        self.run_cli("map", "--keep-edits", "--state-dir", self.state_dir)
        with open(os.path.join(self.state_dir, "workflows.json"), "r", encoding="utf-8") as stream:
            refreshed = json.load(stream)
        kept = [wf for wf in refreshed["workflows"] if wf.get("edited")]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "Order tracking (human reviewed)")

    def test_profile_digs_through_the_sessions(self):
        self.run_cli(
            "import", "--jsonl", os.path.join(self.examples_dir, "traces.jsonl"),
            "--app-config", os.path.join(self.examples_dir, "app_config.json"),
            "--state-dir", self.state_dir,
        )
        self.run_cli("profile", "--state-dir", self.state_dir)
        with open(os.path.join(self.state_dir, "profile.json"), "r", encoding="utf-8") as stream:
            profile = json.load(stream)
        self.assertEqual(profile["totals"]["calls"], 16)
        tool_names = {tool["name"] for tool in profile["tools"]}
        self.assertIn("get_order_status", tool_names)
        with open(os.path.join(self.state_dir, "app_config.inferred.json"), "r", encoding="utf-8") as stream:
            inferred = json.load(stream)
        self.assertTrue(inferred["inferred"])
        self.assertTrue(inferred["tools"])

    def test_live_target_without_remote_flag_is_refused(self):
        self.run_cli("run", "--demo", "--state-dir", self.state_dir)
        with self.assertRaises(SystemExit) as caught:
            self.run_cli("replay", "--target", "openai", "--state-dir", self.state_dir)
        self.assertIn("--remote", str(caught.exception))

    def test_missing_store_gives_clear_error(self):
        with self.assertRaises(SystemExit):
            self.run_cli("map", "--state-dir", os.path.join(self.tmp, "empty"))

    def test_import_requires_inputs(self):
        with self.assertRaises(SystemExit):
            self.run_cli("import", "--state-dir", self.state_dir)

    def test_report_before_replay_gives_clear_error(self):
        state_dir2 = os.path.join(self.tmp, "state2")
        self.run_cli("run", "--demo", "--state-dir", state_dir2)
        os.remove(os.path.join(state_dir2, "run", "results.json"))
        with self.assertRaises(SystemExit):
            self.run_cli("report", "--state-dir", state_dir2)

    def test_resume_reuses_checkpoint_on_second_replay(self):
        self.run_cli("run", "--demo", "--state-dir", self.state_dir)
        with open(os.path.join(self.state_dir, "run", "results.json"), "r", encoding="utf-8") as stream:
            first = json.load(stream)
        self.run_cli("replay", "--state-dir", self.state_dir)
        with open(os.path.join(self.state_dir, "run", "results.json"), "r", encoding="utf-8") as stream:
            second = json.load(stream)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["case_results"], second["case_results"])


if __name__ == "__main__":
    unittest.main()
