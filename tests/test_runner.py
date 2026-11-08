"""Tests for the runner: checks, comparison, verdicts, checkpoints, reports."""

import json
import os
import shutil
import tempfile
import unittest

from switchit import core, demo_data, providers as pv, runner


def demo_store():
    raw_calls = core.parse_jsonl_lines(demo_data.demo_lines(), "demo")
    calls = [core.make_call(raw, "demo", "demo") for raw in raw_calls]
    return core.build_store(calls)


def demo_cases_doc():
    store = demo_store()
    workflow_map = core.group_workflows(store)
    return core.select_cases(store, workflow_map, max_cases=24), store


def case_by_trace(cases_doc, trace_id):
    return [case for case in cases_doc["cases"] if case["trace_id"] == trace_id][0]


class DeepSubsetTests(unittest.TestCase):
    def test_nested_dicts_and_lists(self):
        self.assertTrue(runner._deep_subset({"a": {"b": [1, 2]}}, {"a": {"b": [1]}}))
        self.assertFalse(runner._deep_subset({"a": {"b": [1]}}, {"a": {"b": [1, 2]}}))
        self.assertFalse(runner._deep_subset({"a": 1}, {"a": 1, "b": 2}))

    def test_deep_subset_missing_length_guard(self):
        # A regression guard: subset longer than superset must fail, not crash.
        self.assertFalse(runner._deep_subset([1], [1, 2]))


class CheckEvaluationTests(unittest.TestCase):
    def make_result(self, **overrides):
        result = {
            "text": "Order 88412 is in transit.",
            "tool_calls": [{"name": "get_order_status", "arguments": {"order_id": "88412"}}],
            "finish_reason": "stop",
            "error": None,
            "latency_ms": 700,
            "usage": {"input_tokens": 500, "output_tokens": 80, "basis": "recorded", "estimator": None},
        }
        result.update(overrides)
        return result

    def test_no_error(self):
        self.assertEqual(runner.evaluate_check({"type": "no_error", "required": True}, self.make_result())["status"], "pass")
        failed = runner.evaluate_check({"type": "no_error", "required": True}, self.make_result(error="HTTP 429"))
        self.assertEqual(failed["status"], "fail")

    def test_tool_called(self):
        check = {"type": "tool_called", "name": "get_order_status", "required": True}
        self.assertEqual(runner.evaluate_check(check, self.make_result())["status"], "pass")
        missing = runner.evaluate_check(check, self.make_result(tool_calls=[]))
        self.assertEqual(missing["status"], "fail")

    def test_tool_args_include(self):
        check = {"type": "tool_args_include", "name": "get_order_status", "arguments": {"order_id": "88412"}, "required": True}
        self.assertEqual(runner.evaluate_check(check, self.make_result())["status"], "pass")
        drifted = runner.evaluate_check(check, self.make_result(tool_calls=[{"name": "get_order_status", "arguments": {"order_id": "wrong"}}]))
        self.assertEqual(drifted["status"], "fail")

    def test_tool_calls_equal_order_matters(self):
        check = {"type": "tool_calls_equal", "names": ["a", "b"], "required": True}
        self.assertEqual(runner.evaluate_check(check, self.make_result(tool_calls=[{"name": "a"}, {"name": "b"}]))["status"], "pass")
        self.assertEqual(runner.evaluate_check(check, self.make_result(tool_calls=[{"name": "b"}, {"name": "a"}]))["status"], "fail")

    def test_output_checks(self):
        contains = {"type": "output_contains", "text": "IN TRANSIT", "required": False}
        self.assertEqual(runner.evaluate_check(contains, self.make_result())["status"], "pass")
        regex = {"type": "output_regex", "pattern": r"Order \d+"}
        self.assertEqual(runner.evaluate_check(regex, self.make_result())["status"], "pass")
        bad_regex = {"type": "output_regex", "pattern": "(unclosed"}
        self.assertEqual(runner.evaluate_check(bad_regex, self.make_result())["status"], "unknown")
        min_chars = {"type": "output_min_chars", "value": 5}
        self.assertEqual(runner.evaluate_check(min_chars, self.make_result())["status"], "pass")

    def test_usage_check_is_unknown_when_usage_unknown(self):
        check = {"type": "usage_output_max", "value": 100, "required": True}
        result = self.make_result(usage={"input_tokens": None, "output_tokens": None, "basis": "unknown", "estimator": None})
        evaluated = runner.evaluate_check(check, result)
        self.assertEqual(evaluated["status"], "unknown")
        result = self.make_result(usage={"input_tokens": 1, "output_tokens": 250, "basis": "recorded", "estimator": None})
        self.assertEqual(runner.evaluate_check(check, result)["status"], "fail")

    def test_error_target_makes_other_checks_unknown(self):
        result = self.make_result(error="provider down")
        for check_type in ("output_contains", "tool_called", "usage_output_max"):
            evaluated = runner.evaluate_check({"type": check_type}, result)
            self.assertEqual(evaluated["status"], "unknown", check_type)

    def test_unknown_check_type_is_not_silently_passing(self):
        self.assertEqual(runner.evaluate_check({"type": "vibes"}, self.make_result())["status"], "unknown")


class CompareToolsTests(unittest.TestCase):
    def test_missing_added_and_drift(self):
        baseline = [{"name": "get_order_status", "arguments": {"order_id": "88412"}}]
        same = runner.compare_tools(baseline, [{"name": "get_order_status", "arguments": {"order_id": "88412"}}])
        self.assertEqual(same[0]["kind"], "match")
        missing = runner.compare_tools(baseline, [])
        self.assertEqual(missing[0]["kind"], "missing_tool")
        added = runner.compare_tools([], [{"name": "delete_account", "arguments": {}}])
        self.assertEqual(added[0]["kind"], "added_tool")
        drift = runner.compare_tools(baseline, [{"name": "get_order_status", "arguments": {"order_id": "999"}}])
        self.assertEqual(drift[0]["kind"], "arg_drift")

    def test_both_sides_empty_is_a_match(self):
        self.assertEqual(runner.compare_tools([], [])[0]["kind"], "match")


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.cases_doc, _ = demo_cases_doc()

    def test_pass_with_mock_mirror(self):
        case = case_by_trace(self.cases_doc, "t-order-001")
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="mirror"))
        self.assertEqual(result["verdict"], "pass")
        self.assertTrue(all(check["status"] == "pass" for check in result["checks_target"]))

    def test_fail_when_required_tool_check_fails(self):
        case = case_by_trace(self.cases_doc, "t-order-001")
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="degrade", text="long enough output text for the min chars check to be evaluated and passed here"))
        self.assertEqual(result["verdict"], "fail")
        kinds = {failure["kind"] for failure in result["failures"]}
        self.assertIn("required_check_failed", kinds)

    def test_tool_drift_without_explicit_check_is_uncertain(self):
        case = case_by_trace(self.cases_doc, "t-order-001")
        # Remove the generated tool checks: tool behavior now drifts un-checked.
        case["checks"] = [{"type": "no_error", "required": True, "source": "user"}]
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="degrade", text="some text"))
        self.assertEqual(result["verdict"], "uncertain")
        self.assertTrue(any("no explicit check" in note for note in result["uncertainty"]))

    def test_error_target_verdict_is_error(self):
        case = case_by_trace(self.cases_doc, "t-order-001")
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="fail"))
        self.assertEqual(result["verdict"], "error")
        self.assertIn("target_error", [failure["kind"] for failure in result["failures"]])

    def test_usage_provenance_note_does_not_flip_verdict(self):
        case = case_by_trace(self.cases_doc, "t-order-001")
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="mirror"))
        self.assertEqual(result["verdict"], "pass")
        self.assertTrue(result["notes"])
        self.assertTrue(any("provenance" in note for note in result["notes"]))

    def test_historical_error_case_is_flagged_as_mis_specified(self):
        case = case_by_trace(self.cases_doc, "t-refund-003")
        self.assertIsNotNone(case["historical"]["error"])
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="mirror"))
        self.assertEqual(result["verdict"], "error")
        self.assertTrue(any("mis-specified" in note for note in result["uncertainty"]))


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_binds_cases_and_adapters(self):
        cases_doc, _ = demo_cases_doc()
        first = runner.run_fingerprint(cases_doc, {"kind": "recorded"}, {"kind": "mock"})
        second = runner.run_fingerprint(cases_doc, {"kind": "recorded"}, {"kind": "mock"})
        self.assertEqual(first, second)
        changed_cases = json.loads(json.dumps(cases_doc))
        changed_cases["cases"][0]["checks"].append({"type": "output_contains", "text": "x", "required": False})
        third = runner.run_fingerprint(changed_cases, {"kind": "recorded"}, {"kind": "mock"})
        self.assertNotEqual(first, third)
        fourth = runner.run_fingerprint(cases_doc, {"kind": "recorded"}, {"kind": "openai"})
        self.assertNotEqual(first, fourth)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchit-runner-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_resume_skips_completed_cases(self):
        cases_doc, _ = demo_cases_doc()
        baseline, target = pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="mirror")

        baseline_calls = {"n": 0}
        original_replay = baseline.replay

        def counting_replay(request, case=None):
            baseline_calls["n"] += 1
            return original_replay(request, case)

        baseline.replay = counting_replay
        first = runner.run_replay(
            cases_doc, baseline, target, {"kind": "recorded"}, {"kind": "mock"},
            os.path.join(self.tmp, "run"),
        )
        completed_first = len(first["case_results"])

        resumed = runner.run_replay(
            cases_doc, baseline, target, {"kind": "recorded"}, {"kind": "mock"},
            os.path.join(self.tmp, "run"),
        )
        self.assertEqual(len(resumed["case_results"]), completed_first)
        self.assertEqual(baseline_calls["n"], completed_first)  # no case was re-run

    def test_fingerprint_mismatch_is_refused(self):
        cases_doc, _ = demo_cases_doc()
        run_dir = os.path.join(self.tmp, "run")
        runner.run_replay(cases_doc, pv.RecordedBaselineAdapter(), pv.MockAdapter(), {"kind": "recorded"}, {"kind": "mock"}, run_dir)
        tampered = json.loads(json.dumps(cases_doc))
        tampered["cases"][0]["request"]["messages"][0]["content"] = "changed input"
        with self.assertRaises(RuntimeError):
            runner.run_replay(
                tampered, pv.RecordedBaselineAdapter(), pv.MockAdapter(),
                {"kind": "recorded"}, {"kind": "mock"}, run_dir,
            )

    def test_checkpoint_file_exists_and_is_valid(self):
        cases_doc, _ = demo_cases_doc()
        run_dir = os.path.join(self.tmp, "run")
        runner.run_replay(cases_doc, pv.RecordedBaselineAdapter(), pv.MockAdapter(), {"kind": "recorded"}, {"kind": "mock"}, run_dir)
        with open(os.path.join(run_dir, "run_state.json"), "r", encoding="utf-8") as stream:
            state = json.load(stream)
        self.assertIn("fingerprint", state)
        self.assertEqual(len(state["completed"]), len(cases_doc["cases"]))


class ChainedReplayTests(unittest.TestCase):
    def setUp(self):
        self.cases_doc, _ = demo_cases_doc()

    def scripted_adapter(self, turns):
        """A deterministic target that plays a scripted sequence of results."""
        calls = {"n": 0}

        class ScriptedAdapter(pv.ProviderAdapter):
            name = "scripted"

            def replay(self, request, case=None):
                index = min(calls["n"], len(turns) - 1)
                calls["n"] += 1
                return {**turns[index], "adapter": "scripted"}

        return ScriptedAdapter()

    def test_chain_completes_the_workflow_and_evaluates_the_final_answer(self):
        case = case_by_trace(self.cases_doc, "t-order-001")
        self.assertIn("get_order_status", case["tool_fixtures"])
        target = self.scripted_adapter([
            {  # turn 1: target proposes the same tool the history used
                "text": "", "error": None, "finish_reason": "tool_calls",
                "tool_calls": [{"name": "get_order_status", "arguments": {"order_id": "88412"}}],
                "usage": {"input_tokens": 100, "output_tokens": 10, "basis": "recorded", "estimator": None},
                "latency_ms": 50,
            },
            {  # turn 2: after the recorded fixture, the final answer
                "text": "Order 88412 is in transit and should arrive Thursday.",
                "error": None, "finish_reason": "stop", "tool_calls": [],
                "usage": {"input_tokens": 300, "output_tokens": 40, "basis": "recorded", "estimator": None},
                "latency_ms": 90,
            },
        ])
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), target, chain=True)
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(len(result["chain"]["turns"]), 2)
        self.assertEqual(result["chain"]["fixtures_used"], ["get_order_status"])
        self.assertEqual(result["chain"]["fixtures_missing"], [])
        # Checks were evaluated on the FINAL turn's text, not the empty turn 1.
        min_chars = [c for c in result["checks_target"] if c["type"] == "output_min_chars"]
        self.assertTrue(min_chars and min_chars[0]["status"] == "pass")

    def test_chain_flags_unknown_tools_with_placeholder_results(self):
        case = case_by_trace(self.cases_doc, "t-order-001")
        target = self.scripted_adapter([
            {"text": "", "error": None, "finish_reason": "tool_calls",
             "tool_calls": [{"name": "delete_all_memories", "arguments": {}}]},
            {"text": "done", "error": None, "finish_reason": "stop", "tool_calls": []},
        ])
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), target, chain=True)
        self.assertIn("delete_all_memories", result["chain"]["fixtures_missing"])
        self.assertTrue(any("no recorded fixture" in note for note in result["uncertainty"]))
        # The required tool was never proposed anywhere in the chain: a genuine
        # fail, with the placeholder gap recorded as uncertainty on top.
        self.assertEqual(result["verdict"], "fail")

    def test_chain_is_bounded_by_max_turns(self):
        case = case_by_trace(self.cases_doc, "t-order-001")
        looping = self.scripted_adapter([
            {"text": "", "error": None, "finish_reason": "tool_calls",
             "tool_calls": [{"name": "get_order_status", "arguments": {}}]},
        ])  # proposes the tool on every turn
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), looping, chain=True)
        self.assertEqual(len(result["chain"]["turns"]), runner.MAX_CHAIN_TURNS)

    def test_chain_flag_changes_the_run_fingerprint(self):
        baseline_spec = {"kind": "recorded"}
        plain = {"kind": "mock"}
        chained = {"kind": "mock", "chain": True}
        self.assertNotEqual(
            runner.run_fingerprint(self.cases_doc, baseline_spec, plain),
            runner.run_fingerprint(self.cases_doc, baseline_spec, chained),
        )

    def test_chain_without_fixtures_is_ignored(self):
        case = case_by_trace(self.cases_doc, "t-misc-001")  # no tool_fixtures
        result = runner.replay_case(case, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="mirror"), chain=True)
        self.assertNotIn("chain", result)
        self.assertEqual(result["verdict"], "pass")


class AggregateTests(unittest.TestCase):
    def test_failure_groups_fixes_and_usage(self):
        cases_doc, _ = demo_cases_doc()
        results_doc = runner.run_replay(
            cases_doc, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="fail"),
            {"kind": "recorded"}, {"kind": "mock"}, tempfile.mkdtemp(prefix="switchit-agg-"),
        )
        capabilities_baseline = pv.RecordedBaselineAdapter().capabilities()
        capabilities_target = pv.MockAdapter(mode="fail").capabilities()
        summary = runner.aggregate(results_doc, cases_doc, capabilities_baseline, capabilities_target)
        self.assertEqual(summary["verdicts"]["error"], len(cases_doc["cases"]))
        error_groups = [group for group in summary["failure_groups"] if group["key"] == "target_error"]
        self.assertTrue(error_groups)
        self.assertEqual(summary["prioritized_fixes"][0]["priority"], 1)
        # Usage: every target call failed without usage → all unknown.
        self.assertEqual(summary["usage"]["target"]["recorded"]["calls"], 0)
        self.assertEqual(summary["usage"]["target"]["requests_without_usage"], len(cases_doc["cases"]))

    def test_capability_gap_is_flagged_for_toolless_target(self):
        cases_doc, _ = demo_cases_doc()
        results_doc = runner.run_replay(
            cases_doc, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="degrade"),
            {"kind": "recorded"}, {"kind": "mock"}, tempfile.mkdtemp(prefix="switchit-cap-"),
        )
        target_caps = pv.MockAdapter(mode="degrade").capabilities()
        summary = runner.aggregate(results_doc, cases_doc, pv.RecordedBaselineAdapter().capabilities(), target_caps)
        self.assertTrue(summary["capability_gaps"])
        self.assertEqual(summary["capability_gaps"][0]["capability"], "tool_calling")


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="switchit-report-")
        self.addCleanup(shutil.rmtree, self.tmp)
        cases_doc, _ = demo_cases_doc()
        results_doc = runner.run_replay(
            cases_doc, pv.RecordedBaselineAdapter(), pv.MockAdapter(mode="fail"),
            {"kind": "recorded"}, {"kind": "mock"}, os.path.join(self.tmp, "run"),
        )
        summary = runner.aggregate(
            results_doc, cases_doc,
            pv.RecordedBaselineAdapter().capabilities(), pv.MockAdapter(mode="fail").capabilities(),
        )
        self.cases_doc = cases_doc
        self.report = runner.build_report(
            results_doc, summary, cases_doc,
            meta={"cases_by_id": {case["case_id"]: case for case in cases_doc["cases"]}},
        )
        self.paths = runner.write_report(self.report, os.path.join(self.tmp, "run"))

    def test_report_files_written(self):
        self.assertTrue(os.path.exists(self.paths["json"]))
        self.assertTrue(os.path.exists(self.paths["html"]))

    def test_report_carries_boundaries_and_redaction_notice(self):
        joined = json.dumps(self.report)
        self.assertIn("triage aids", joined)
        self.assertIn("never executes a business tool", joined)
        self.assertIn("redacted at import", joined)

    def test_html_is_self_contained_and_escaped(self):
        with open(self.paths["html"], "r", encoding="utf-8") as stream:
            html = stream.read()
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertIn("&quot;", html)  # JSON specs in the header are escaped
        self.assertIn("&#x27;", html)  # apostrophes are escaped
        self.assertIn("MIGRATION REPORT", html)
        self.assertIn("MIGRATION REPORT", html)

    def test_no_secrets_reach_the_report(self):
        with open(self.paths["html"], "r", encoding="utf-8") as stream:
            html = stream.read()
        with open(self.paths["json"], "r", encoding="utf-8") as stream:
            document = stream.read()
        for artifact in (html, document):
            self.assertNotIn("dana@example.com", artifact)
            self.assertNotIn("555-010-9988", artifact)


if __name__ == "__main__":
    unittest.main()
