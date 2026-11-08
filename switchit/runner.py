"""Replay, compare, report, and checkpoints.

The runner:

- Replays each frozen case through a baseline adapter (usually the recorded
  historical response) and a target adapter (mock, offline, or explicitly
  opted-in live provider).
- Evaluates the case's success checks against the target result and, as a
  mis-specification guard, against the historical reference itself.
- Compares proposed tool behavior. Tool calls are captured and compared,
  never executed.
- Classifies every case verdict as pass, fail, error, or uncertain, with the
  reasons written out.
- Aggregates failure groups, capability gaps, usage totals by provenance
  (recorded / estimated / unknown), and a prioritized fix list.
- Persists a checkpoint after every case so an interrupted run resumes
  without re-billing completed work; the run fingerprint refuses to resume
  when cases, adapters, or runner code changed.
- Writes a local JSON report and a self-contained HTML report (no server, no
  external assets).
"""

from __future__ import annotations

import datetime
import hashlib
import html
import json
import os
import tempfile
from pathlib import Path

from . import providers as pv

RUNNER_VERSION = "0.1.0"

VERDICTS = ("pass", "fail", "error", "uncertain")

MAX_CHAIN_TURNS = 3

BOUNDARIES = [
    "Workflow labels and similarity scores are triage aids, not measured accuracy.",
    "Proposed tool calls are captured for comparison; the harness never executes a business tool.",
    "A historical model response is reference behavior, not proof of success; checks define success explicitly.",
    "Token usage is kept separate by provenance: recorded (source-reported), estimated, and unknown. These are telemetry, not invoices.",
    "End-to-end outcomes for workflows that need a real tool remain unverified until a safe tool fixture or authorized test environment exists.",
]

CHECK_TYPES = (
    "no_error", "tool_called", "tool_args_include", "tool_calls_equal",
    "output_contains", "output_regex", "output_min_chars",
    "latency_ms_max", "usage_input_max", "usage_output_max",
)


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1, sort_keys=False)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def run_fingerprint(cases_doc: dict, baseline_spec: dict, target_spec: dict) -> str:
    """Bind the run to its cases, adapter specs, and runner version."""
    payload = {
        "runner_version": RUNNER_VERSION,
        "cases_digest": hashlib.sha256(
            json.dumps(cases_doc, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "baseline": baseline_spec,
        "target": target_spec,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def clip(text, limit=600):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: int(limit * 0.75)] + " […] " + text[-int(limit * 0.25) :]


# ---------------------------------------------------------------------------
# Check evaluation
# ---------------------------------------------------------------------------


def _tool_call_names(result: dict) -> list:
    return [call.get("name", "") for call in result.get("tool_calls") or []]


def _deep_subset(superset, subset) -> bool:
    """True when every key/value in subset appears in superset (recursively)."""
    if isinstance(subset, dict):
        if not isinstance(superset, dict):
            return False
        for key, value in subset.items():
            if key not in superset or not _deep_subset(superset[key], value):
                return False
        return True
    if isinstance(subset, list):
        if not isinstance(superset, list) or len(superset) < len(subset):
            return False
        remaining = list(superset)
        for item in subset:
            for index, candidate in enumerate(remaining):
                if _deep_subset(candidate, item):
                    remaining.pop(index)
                    break
            else:
                return False
        return True
    return superset == subset


def evaluate_check(check: dict, result: dict) -> dict:
    """Evaluate one check against one replay result.

    Statuses: pass, fail, unknown. A check whose inputs are missing (for
    example usage when the provider reported none) evaluates to unknown
    rather than silently passing.
    """
    check_type = check.get("type")
    evaluated = {
        "type": check_type,
        "required": bool(check.get("required", True)),
        "source": check.get("source", "user"),
        "status": "unknown",
        "detail": "",
    }
    error = result.get("error")
    text = result.get("text") or ""
    tool_calls = result.get("tool_calls") or []
    usage = result.get("usage") or {}

    if error:
        if check_type == "no_error":
            evaluated.update(status="fail", detail=clip(error, 200))
        else:
            evaluated.update(status="unknown", detail="target call errored; check not evaluable")
        return evaluated

    if check_type == "no_error":
        evaluated.update(status="pass", detail="completed without a provider error")

    elif check_type == "tool_called":
        name = check.get("name", "")
        if name in _tool_call_names(result):
            evaluated.update(status="pass", detail=f"proposed tool '{name}'")
        else:
            evaluated.update(
                status="fail",
                detail=f"tool '{name}' was not proposed; proposed: {_tool_call_names(result) or 'none'}",
            )

    elif check_type == "tool_args_include":
        name = check.get("name", "")
        subset = check.get("arguments") or {}
        for call in tool_calls:
            if call.get("name") == name and _deep_subset(call.get("arguments") or {}, subset):
                evaluated.update(status="pass", detail=f"arguments of '{name}' include the required subset")
                break
        else:
            evaluated.update(
                status="fail",
                detail=f"no proposed '{name}' call includes arguments subset {json.dumps(subset, sort_keys=True)}",
            )

    elif check_type == "tool_calls_equal":
        expected = list(check.get("names") or [])
        actual = _tool_call_names(result)
        evaluated.update(
            status="pass" if actual == expected else "fail",
            detail=f"expected {expected} in order; got {actual}",
        )

    elif check_type == "output_contains":
        needle = check.get("text", "")
        if needle.casefold() in text.casefold():
            evaluated.update(status="pass", detail=f"output contains {needle[:60]!r}")
        else:
            evaluated.update(status="fail", detail=f"output does not contain {clip(needle, 60)!r}")

    elif check_type == "output_regex":
        import re

        try:
            matched = re.search(check.get("pattern", ""), text)
        except re.error as regex_error:
            evaluated.update(status="unknown", detail=f"invalid regex: {regex_error}")
            return evaluated
        if matched:
            evaluated.update(status="pass", detail=f"regex matched {matched.group(0)[:60]!r}")
        else:
            evaluated.update(status="fail", detail="regex did not match the output")

    elif check_type == "output_min_chars":
        minimum = int(check.get("value", 0))
        evaluated.update(
            status="pass" if len(text) >= minimum else "fail",
            detail=f"output length {len(text)} vs minimum {minimum}",
        )

    elif check_type == "latency_ms_max":
        latency = result.get("latency_ms")
        if latency is None:
            evaluated.update(status="unknown", detail="no latency recorded")
        else:
            maximum = int(check.get("value", 0))
            evaluated.update(
                status="pass" if latency <= maximum else "fail",
                detail=f"latency {latency} ms vs maximum {maximum} ms",
            )

    elif check_type in ("usage_input_max", "usage_output_max"):
        key = "input" if check_type == "usage_input_max" else "output"
        value = usage.get(f"{key}_tokens")
        if value is None or usage.get("basis") == "unknown":
            evaluated.update(
                status="unknown",
                detail=f"target usage for '{key}' is unknown (provenance: {usage.get('basis', 'unknown')})",
            )
        else:
            maximum = int(check.get("value", 0))
            evaluated.update(
                status="pass" if value <= maximum else "fail",
                detail=f"{key} tokens {value} vs maximum {maximum} (provenance: {usage.get('basis')})",
            )

    else:
        evaluated.update(status="unknown", detail=f"unknown check type {check_type!r}")
    return evaluated


def evaluate_checks(checks: list, result: dict) -> list:
    return [evaluate_check(check, result) for check in checks or []]


def historical_result(case: dict) -> dict:
    """The case's historical response shaped like a replay result."""
    historical = case.get("historical") or {}
    usage = dict(historical.get("usage") or {})
    usage.setdefault("basis", "unknown")
    usage.setdefault("estimator", None)
    return {
        "text": historical.get("text") or "",
        "tool_calls": historical.get("tool_calls") or [],
        "finish_reason": historical.get("finish_reason"),
        "error": historical.get("error"),
        "latency_ms": historical.get("latency_ms"),
        "usage": usage,
        "adapter": "historical",
        "model": historical.get("model"),
    }


# ---------------------------------------------------------------------------
# Tool behavior comparison
# ---------------------------------------------------------------------------


def compare_tools(baseline_tools: list, target_tools: list) -> list:
    """Compare proposed tool calls between reference and target.

    Returns findings of kind: match, missing_tool, added_tool, arg_drift.
    """
    findings = []
    baseline_by_name = {call.get("name", ""): call for call in baseline_tools or []}
    target_by_name = {call.get("name", ""): call for call in target_tools or []}
    for name in sorted(baseline_by_name):
        if name in target_by_name:
            baseline_args = baseline_by_name[name].get("arguments") or {}
            target_args = target_by_name[name].get("arguments") or {}
            if _deep_subset(target_args, baseline_args) and _deep_subset(baseline_args, target_args):
                findings.append({"kind": "match", "tool": name, "detail": "same tool, equivalent arguments"})
            else:
                findings.append(
                    {
                        "kind": "arg_drift",
                        "tool": name,
                        "detail": (
                            f"reference arguments {json.dumps(baseline_args, sort_keys=True)[:200]} vs "
                            f"target {json.dumps(target_args, sort_keys=True)[:200]}"
                        ),
                    }
                )
        else:
            findings.append({"kind": "missing_tool", "tool": name, "detail": f"reference proposed '{name}' but the target did not"})
    for name in sorted(set(target_by_name) - set(baseline_by_name)):
        findings.append({"kind": "added_tool", "tool": name, "detail": f"target proposed '{name}' but the reference did not"})
    if not findings:
        findings.append({"kind": "match", "tool": "", "detail": "no proposed tool calls on either side"})
    return findings


# ---------------------------------------------------------------------------
# Per-case replay + verdict
# ---------------------------------------------------------------------------


def _chain_target_turns(case: dict, target_adapter, max_turns: int = MAX_CHAIN_TURNS):
    """Chained replay: continue the conversation through recorded tool fixtures.

    When the target proposes a business-tool call (Supermemory, an internal API,
    anything), the recorded result from history is fed back so the model can
    continue to a final answer. No real business tool is ever executed. If the
    target proposes a tool history never used, it receives an explicit
    placeholder result and the gap is reported.
    """
    request = case.get("request") or {}
    fixtures = case.get("tool_fixtures") or {}
    messages = list(request.get("messages") or [])
    tools = list(request.get("tools") or [])
    fixtures_used, fixtures_missing = [], []
    proposals = []
    turns = []
    final = None
    for turn in range(max_turns):
        result = target_adapter.replay({"messages": messages, "tools": tools}, case)
        proposals.extend(result.get("tool_calls") or [])
        turns.append({
            "turn": turn + 1,
            "text": clip(result.get("text"), 300),
            "tool_calls": [call.get("name", "") for call in result.get("tool_calls") or []],
            "error": result.get("error"),
            "usage": result.get("usage"),
            "latency_ms": result.get("latency_ms"),
        })
        final = result
        if result.get("error") or not result.get("tool_calls"):
            break
        proposed = result["tool_calls"]
        messages = messages + [{
            "role": "assistant",
            "content": result.get("text") or "",
            "tool_calls": proposed,
        }]
        for position, tool_call in enumerate(proposed):
            name = tool_call.get("name", "")
            if name in fixtures:
                fixture_text = fixtures[name]
                if name not in fixtures_used:
                    fixtures_used.append(name)
            else:
                fixture_text = (
                    f"[switchit fixture] no recorded result for tool '{name}'; "
                    "the historical trace never used it."
                )
                if name not in fixtures_missing:
                    fixtures_missing.append(name)
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{turn}_{position}",
                "name": name,
                "content": fixture_text,
            })
    return final, {
        "turns": turns,
        "proposals": proposals,
        "fixtures_used": fixtures_used,
        "fixtures_missing": fixtures_missing,
        "max_turns": max_turns,
    }


def replay_case(case: dict, baseline_adapter, target_adapter, chain: bool = False) -> dict:
    """Replay one case through baseline and target adapters.

    With ``chain=True`` and recorded tool fixtures attached to the case, the
    target's tool proposals are continued with the recorded results so checks
    can evaluate the final answer of the workflow, not just the first proposal.
    """
    request = case.get("request") or {}
    baseline_result = baseline_adapter.replay(request, case)
    if chain and case.get("tool_fixtures"):
        target_result, chain_meta = _chain_target_turns(case, target_adapter)
        # The workflow's tool behavior spans the whole chain: a required tool
        # proposed in an earlier turn satisfies tool checks even though the
        # final turn is a plain text answer. Merge first proposals by name.
        merged_calls, seen_names = [], set()
        for call in chain_meta["proposals"]:
            name = call.get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                merged_calls.append(call)
        target_result = {**target_result, "tool_calls": merged_calls}
    else:
        target_result = target_adapter.replay(request, case)
        chain_meta = None
    case_result = finalize_case(case, baseline_result, target_result)
    if chain_meta:
        case_result["chain"] = chain_meta
        if chain_meta["fixtures_missing"]:
            case_result["uncertainty"].append(
                "target proposed tool(s) with no recorded fixture ("
                + ", ".join(chain_meta["fixtures_missing"])
                + "); the continuation received a placeholder result"
            )
        used = ", ".join(chain_meta["fixtures_used"]) or "none"
        case_result["uncertainty"].append(
            f"chained replay used recorded tool fixtures ({used}); end-to-end validity "
            "depends on fixture fidelity, and no real business tool was executed"
        )
    return case_result


def finalize_case(case: dict, baseline_result: dict, target_result: dict) -> dict:
    """Evaluate checks, compare tools, and classify the case verdict."""
    checks_target = evaluate_checks(case.get("checks"), target_result)
    checks_reference = evaluate_checks(case.get("checks"), historical_result(case))
    tool_findings = compare_tools(
        historical_result(case).get("tool_calls"),
        target_result.get("tool_calls"),
    )

    reference_by_type = {check["type"]: check for check in checks_reference}
    uncertainty = []
    failures = []
    for check in checks_target:
        reference = reference_by_type.get(check["type"])
        reference_failed = bool(reference and reference["status"] == "fail")
        if check["status"] == "fail":
            failures.append(
                {
                    "kind": "required_check_failed" if check["required"] else "optional_check_failed",
                    "check": check,
                    "case_id": case["case_id"],
                }
            )
            if reference_failed:
                uncertainty.append(
                    f"check '{check['type']}' also fails against the historical reference; "
                    "the check may be mis-specified rather than the target broken"
                )
        elif check["status"] == "unknown" and check["required"]:
            uncertainty.append(f"required check '{check['type']}' could not be evaluated: {check['detail']}")

    target_error = target_result.get("error")
    if target_error:
        failures.append({"kind": "target_error", "check": None, "case_id": case["case_id"]})

    explicit_tool_checks = {
        check.get("name") for check in case.get("checks") or [] if check.get("type") == "tool_called"
    }
    for finding in tool_findings:
        if finding["kind"] == "missing_tool" and finding["tool"] not in explicit_tool_checks:
            uncertainty.append(
                f"reference proposed tool '{finding['tool']}' but the target did not, and no explicit check covers it"
            )
        elif finding["kind"] == "added_tool":
            uncertainty.append(f"target proposed an additional tool '{finding['tool']}' absent from the reference")
        elif finding["kind"] == "arg_drift":
            uncertainty.append(f"arguments drifted for tool '{finding['tool']}' while no strict check covers them")

    historical_basis = (case.get("historical", {}).get("usage") or {}).get("basis")
    target_basis = (target_result.get("usage") or {}).get("basis")
    notes = []
    if historical_basis != target_basis:
        notes.append(
            f"usage provenance differs: historical '{historical_basis}' vs target '{target_basis}'; "
            "token totals are reported separately by basis and are not directly comparable"
        )

    if target_error:
        verdict = "error"
    elif any(f["kind"] == "required_check_failed" for f in failures):
        verdict = "fail"
    elif uncertainty:
        verdict = "uncertain"
    else:
        verdict = "pass"

    return {
        "case_id": case["case_id"],
        "workflow_id": case.get("workflow_id"),
        "trace_id": case.get("trace_id"),
        "origins": case.get("origins") or [],
        "baseline": baseline_result,
        "target": target_result,
        "checks_target": checks_target,
        "checks_reference": checks_reference,
        "tool_findings": tool_findings,
        "failures": failures,
        "uncertainty": uncertainty,
        "notes": notes,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Checkpointed run
# ---------------------------------------------------------------------------


def load_checkpoint(run_dir):
    path = Path(run_dir) / "run_state.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except (ValueError, OSError):
        return None


def _checkpoint_path(run_dir) -> Path:
    return Path(run_dir) / "run_state.json"


def run_replay(cases_doc: dict, baseline_adapter, target_adapter, baseline_spec: dict,
               target_spec: dict, run_dir, resume: bool = True) -> dict:
    """Replay all cases with per-case checkpointing.

    The checkpoint records completed case results plus the run fingerprint.
    If the fingerprint changes (cases, adapter specs, or runner version), the
    previous state is refused: stale results must never masquerade as fresh.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = run_fingerprint(cases_doc, baseline_spec, target_spec)
    state = None
    if resume:
        state = load_checkpoint(run_dir)
        if state and state.get("fingerprint") != fingerprint:
            raise RuntimeError(
                "Run state does not match the current cases, adapters, or runner version. "
                "Use a fresh --run-dir or restore the matching cases.json and adapter settings."
            )
    if not state:
        state = {
            "runner_version": RUNNER_VERSION,
            "fingerprint": fingerprint,
            "started_at": utc_now_iso(),
            "baseline_spec": baseline_spec,
            "target_spec": target_spec,
            "completed": {},
        }

    results = []
    chain = bool((target_spec or {}).get("chain"))
    for case in cases_doc.get("cases") or []:
        case_id = case["case_id"]
        if case_id in state["completed"]:
            results.append(state["completed"][case_id])
            continue
        case_result = replay_case(case, baseline_adapter, target_adapter, chain=chain)
        state["completed"][case_id] = case_result
        state["updated_at"] = utc_now_iso()
        atomic_write_json(_checkpoint_path(run_dir), state)
        results.append(case_result)

    document = {
        "runner_version": RUNNER_VERSION,
        "fingerprint": fingerprint,
        "started_at": state.get("started_at"),
        "finished_at": utc_now_iso(),
        "baseline_spec": baseline_spec,
        "target_spec": target_spec,
        "case_results": results,
    }
    atomic_write_json(run_dir / "results.json", document)
    return document


# ---------------------------------------------------------------------------
# Aggregation: usage totals, failure groups, fixes
# ---------------------------------------------------------------------------


def _merge_usage_results(results: list) -> dict:
    merged = {
        basis: {"calls": 0, "input_tokens_known": 0, "output_tokens_known": 0, "input_missing_calls": 0,
                "output_missing_calls": 0, "calls_missing_either": 0}
        for basis in ("recorded", "estimated", "unknown")
    }
    without_usage = 0
    for result in results:
        if not result:
            continue
        usage = result.get("usage") or {}
        basis = usage.get("basis") or "unknown"
        if basis not in merged:
            basis = "unknown"
        bucket = merged[basis]
        bucket["calls"] += 1
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if input_tokens is None and output_tokens is None:
            without_usage += 1
        for key, value in (("input", input_tokens), ("output", output_tokens)):
            if value is None:
                bucket[f"{key}_missing_calls"] += 1
            else:
                bucket[f"{key}_tokens_known"] += int(value)
        if input_tokens is None or output_tokens is None:
            bucket["calls_missing_either"] += 1
    merged["requests_without_usage"] = without_usage
    return merged


def aggregate(results_doc: dict, cases_doc: dict, capabilities_baseline: dict,
              capabilities_target: dict) -> dict:
    """Summarize a run: verdict counts, failure groups, gaps, fixes, usage."""
    case_results = results_doc.get("case_results") or []
    cases_by_id = {case["case_id"]: case for case in cases_doc.get("cases") or []}

    verdict_counts = {verdict: 0 for verdict in VERDICTS}
    for result in case_results:
        verdict_counts[result["verdict"]] = verdict_counts.get(result["verdict"], 0) + 1

    groups = {}
    for result in case_results:
        for failure in result.get("failures") or []:
            kind = failure["kind"]
            if kind == "required_check_failed" and failure.get("check"):
                key = f"required_check_failed:{failure['check']['type']}"
            elif kind == "optional_check_failed" and failure.get("check"):
                key = f"optional_check_failed:{failure['check']['type']}"
            elif kind == "target_error":
                key = "target_error"
            else:
                key = kind
            group = groups.setdefault(key, {"count": 0, "case_ids": [], "kinds": set()})
            group["count"] += 1
            if failure["case_id"] not in group["case_ids"]:
                group["case_ids"].append(failure["case_id"])
            group["kinds"].add(kind)

    for result in case_results:
        for finding in result.get("tool_findings") or []:
            if finding["kind"] in ("missing_tool", "added_tool", "arg_drift"):
                key = f"tool_{finding['kind']}"
                group = groups.setdefault(key, {"count": 0, "case_ids": [], "kinds": set()})
                group["count"] += 1
                if result["case_id"] not in group["case_ids"]:
                    group["case_ids"].append(result["case_id"])
                group["kinds"].add(f"tool_{finding['kind']}")

    failure_groups = [
        {
            "key": key,
            "count": group["count"],
            "case_ids": group["case_ids"][:12],
            "kinds": sorted(group["kinds"]),
        }
        for key, group in sorted(groups.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    ]

    needed = {"tool_calling": False}
    for case in cases_doc.get("cases") or []:
        historical_tools = (case.get("historical") or {}).get("tool_calls") or []
        request_tools = (case.get("request") or {}).get("tools") or []
        if historical_tools or request_tools:
            needed["tool_calling"] = True
    capability_gaps = []
    if needed["tool_calling"] and capabilities_target.get("tool_calling") is False:
        capability_gaps.append(
            {
                "capability": "tool_calling",
                "detail": "cases replay tool-using workflows, but the target adapter declares no tool-calling support",
            }
        )

    fixes = []
    if verdict_counts["error"]:
        fixes.append({
            "priority": 1,
            "title": f"{verdict_counts['error']} case(s) errored on the target provider",
            "case_ids": [r["case_id"] for r in case_results if r["verdict"] == "error"][:12],
            "suggestion": "Inspect the recorded error strings and the adapter configuration (model id, endpoint, credentials, region) before anything else.",
        })
    if capability_gaps:
        fixes.append({
            "priority": 2,
            "title": "Declared target capabilities do not cover what the cases need",
            "case_ids": sorted({cid for gap in capability_gaps for cid in _ids_for_capability(case_results, cases_by_id)}),
            "suggestion": "Check the target provider's current documentation and either enable the capability or pick workflows that do not need it.",
        })
    required_failures = [g for g in failure_groups if g["key"].startswith("required_check_failed")]
    if required_failures:
        fixes.append({
            "priority": 3,
            "title": "Required success checks failed on the target",
            "case_ids": sorted({cid for group in required_failures for cid in group["case_ids"]}),
            "suggestion": "For each failing check, decide whether the prompt, the tool schema mapping, or the check itself needs to change; then replay.",
        })
    tool_groups = [g for g in failure_groups if g["key"].startswith("tool_missing_tool")]
    if tool_groups:
        fixes.append({
            "priority": 4,
            "title": "Target stopped proposing tools the reference proposed",
            "case_ids": sorted({cid for group in tool_groups for cid in group["case_ids"]}),
            "suggestion": "Map tool schemas and system prompts explicitly for the target; missing tool proposals usually trace to prompt or schema translation.",
        })
    mis_specified_cases = sorted({
        result["case_id"]
        for result in case_results
        for note in result.get("uncertainty") or []
        if "mis-specified" in note
    })
    if mis_specified_cases:
        fixes.append({
            "priority": 5,
            "title": "Some checks fail even against the historical reference",
            "case_ids": mis_specified_cases,
            "suggestion": "Fix or relax these checks in cases.json; they cannot distinguish a broken target from a broken expectation.",
        })
    latency_groups = [g for g in failure_groups if g["key"] == "optional_check_failed:latency_ms_max"]
    if latency_groups:
        fixes.append({
            "priority": 6,
            "title": "Latency exceeded the default budget on some cases",
            "case_ids": sorted({cid for group in latency_groups for cid in group["case_ids"]}),
            "suggestion": "Compare target latency against historical latency by workflow; tune the budget or investigate the provider.",
        })
    drift_groups = [g for g in failure_groups if g["key"] == "tool_arg_drift"]
    if drift_groups:
        fixes.append({
            "priority": 7,
            "title": "Tool arguments drifted from the reference",
            "case_ids": sorted({cid for group in drift_groups for cid in group["case_ids"]}),
            "suggestion": "Add explicit tool_args_include checks for the arguments that matter, then replay.",
        })

    usage = {
        "baseline": _merge_usage_results([r.get("baseline") for r in case_results]),
        "target": _merge_usage_results([r.get("target") for r in case_results]),
    }

    return {
        "verdicts": verdict_counts,
        "failure_groups": failure_groups,
        "capability_gaps": capability_gaps,
        "needed_capabilities": needed,
        "prioritized_fixes": fixes,
        "usage": usage,
        "cases_with_uncertainty": sorted({
            result["case_id"] for result in case_results if result.get("uncertainty")
        }),
    }


def _ids_for_capability(case_results, cases_by_id):
    for result in case_results:
        case = cases_by_id.get(result["case_id"]) or {}
        historical_tools = (case.get("historical") or {}).get("tool_calls") or []
        request_tools = (case.get("request") or {}).get("tools") or []
        if historical_tools or request_tools:
            yield result["case_id"]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def build_report(results_doc: dict, summary: dict, cases_doc: dict, meta: dict | None = None) -> dict:
    """Assemble the full migration report document."""
    meta = dict(meta or {})
    report = {
        "report_version": 1,
        "generated_at": utc_now_iso(),
        "runner_version": results_doc.get("runner_version"),
        "fingerprint": results_doc.get("fingerprint"),
        "baseline_spec": results_doc.get("baseline_spec"),
        "target_spec": results_doc.get("target_spec"),
        "verdicts": summary["verdicts"],
        "capability_gaps": summary["capability_gaps"],
        "failure_groups": summary["failure_groups"],
        "prioritized_fixes": summary["prioritized_fixes"],
        "usage": summary["usage"],
        "cases": [],
        "boundaries": BOUNDARIES,
        "notes": [],
        "meta": meta,
    }
    for result in results_doc.get("case_results") or []:
        case = meta.get("cases_by_id", {}).get(result["case_id"], {})
        report["cases"].append(
            {
                "case_id": result["case_id"],
                "workflow_id": result.get("workflow_id"),
                "trace_id": result.get("trace_id"),
                "origins": result.get("origins"),
                "verdict": result["verdict"],
                "failures": [
                    {
                        "kind": failure["kind"],
                        "check": failure.get("check"),
                        "detail": (failure.get("check") or {}).get("detail", ""),
                    }
                    for failure in result.get("failures") or []
                ],
                "uncertainty": result.get("uncertainty") or [],
                "notes": result.get("notes") or [],
                "tool_findings": result.get("tool_findings") or [],
                "baseline_excerpt": clip((result.get("baseline") or {}).get("text")),
                "target_excerpt": clip((result.get("target") or {}).get("text")),
                "baseline_usage": (result.get("baseline") or {}).get("usage"),
                "target_usage": (result.get("target") or {}).get("usage"),
                "baseline_latency_ms": (result.get("baseline") or {}).get("latency_ms"),
                "target_latency_ms": (result.get("target") or {}).get("latency_ms"),
                "target_error": (result.get("target") or {}).get("error"),
                "checks_target": result.get("checks_target") or [],
                "chain": result.get("chain"),
                "request_messages": [
                    {"role": message.get("role"), "content": clip(message.get("content"), 300)}
                    for message in (case.get("request") or {}).get("messages") or []
                ],
            }
        )
    report["notes"].append(
        "All historical content in this report was redacted at import time; excerpts are additionally clipped."
    )
    report["notes"].append(
        "Declared adapter capabilities are descriptions to verify against provider documentation, not measured results."
    )
    return report


def write_report(report: dict, run_dir) -> dict:
    """Write report.json and report.html into the run directory."""
    run_dir = Path(run_dir)
    json_path = run_dir / "report.json"
    html_path = run_dir / "report.html"
    atomic_write_json(json_path, report)
    html_path.write_text(render_html(report), encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}


# ---------------------------------------------------------------------------
# HTML rendering (self-contained, no external assets, no server)
# ---------------------------------------------------------------------------


def render_html(report: dict) -> str:
    esc = html.escape

    def verdict_class(verdict):
        return {
            "pass": "v-pass", "fail": "v-fail", "error": "v-error", "uncertain": "v-uncertain",
        }.get(verdict, "v-uncertain")

    verdicts = report.get("verdicts") or {}
    total_cases = sum(verdicts.get(v, 0) for v in VERDICTS)
    usage = report.get("usage") or {}

    def usage_rows(side):
        rows = []
        data = (usage.get(side) or {})
        for basis in ("recorded", "estimated", "unknown"):
            bucket = data.get(basis) or {}
            rows.append(
                f"<tr><td>{esc(basis)}</td><td>{bucket.get('calls', 0)}</td>"
                f"<td>{bucket.get('input_tokens_known', 0):,}</td>"
                f"<td>{bucket.get('output_tokens_known', 0):,}</td>"
                f"<td>{bucket.get('calls_missing_either', 0)}</td></tr>"
            )
        rows.append(
            f"<tr class='muted'><td>requests without any usage</td>"
            f"<td colspan='4'>{data.get('requests_without_usage', 0)}</td></tr>"
        )
        return "".join(rows)

    case_cards = []
    for case in report.get("cases") or []:
        checks = []
        for check in case.get("checks_target") or []:
            status_class = {"pass": "c-pass", "fail": "c-fail", "unknown": "c-unknown"}.get(check.get("status"), "c-unknown")
            checks.append(
                f"<li class='{status_class}'><code>{esc(str(check.get('type')))}</code> "
                f"{'required' if check.get('required') else 'optional'} — {esc(check.get('status', ''))}: "
                f"{esc(check.get('detail', ''))}</li>"
            )
        tools = []
        for finding in case.get("tool_findings") or []:
            tools.append(f"<li>{esc(finding.get('kind', ''))}: {esc(finding.get('detail', ''))}</li>")
        uncertainties = "".join(f"<li>{esc(note)}</li>" for note in case.get("uncertainty") or [])
        case_notes = "".join(f"<li>{esc(note)}</li>" for note in case.get("notes") or [])
        chain_info = ""
        if case.get("chain"):
            chain_meta = case["chain"]
            chain_info = (
                f"<p class='muted'>chained replay: {len(chain_meta.get('turns') or [])} turn(s), "
                f"fixtures used: {esc(', '.join(chain_meta.get('fixtures_used') or []) or 'none')}"
                + (f", missing: {esc(', '.join(chain_meta['fixtures_missing']))}" if chain_meta.get("fixtures_missing") else "")
                + "</p>"
            )
        errors = ""
        if case.get("target_error"):
            errors = f"<p class='error-text'>target error: {esc(case['target_error'])}</p>"
        request_lines = "".join(
            f"<li><strong>{esc(message.get('role', ''))}</strong>: {esc(message.get('content', ''))}</li>"
            for message in case.get("request_messages") or []
        )
        case_cards.append(
            f"<details class='case {verdict_class(case.get('verdict', ''))}'>"
            f"<summary><span class='verdict {verdict_class(case.get('verdict', ''))}'>"
            f"{esc(case.get('verdict', ''))}</span> {esc(case.get('case_id', ''))} "
            f"<span class='muted'>— workflow {esc(str(case.get('workflow_id')))}, "
            f"trace {esc(str(case.get('trace_id')))}, origins: {esc(', '.join(case.get('origins') or []))}</span></summary>"
            f"<div class='case-body'>"
            f"<h4>Request (redacted)</h4><ul class='messages'>{request_lines}</ul>"
            f"{errors}"
            f"<h4>Checks (target)</h4><ul class='checks'>{''.join(checks) or '<li>none</li>'}</ul>"
            f"<h4>Tool findings</h4><ul class='tools'>{''.join(tools) or '<li>none</li>'}</ul>"
            f"<h4>Uncertainty</h4><ul class='uncertainty'>{uncertainties or '<li>none</li>'}</ul>"
            f"<h4>Comparison notes</h4><ul class='notes'>{case_notes or '<li>none</li>'}</ul>"
            f"{chain_info}"
            f"<h4>Excerpts</h4>"
            f"<p><strong>baseline:</strong> {esc(case.get('baseline_excerpt') or '(empty)')}</p>"
            f"<p><strong>target:</strong> {esc(case.get('target_excerpt') or '(empty)')}</p>"
            f"<p class='muted'>latency baseline {case.get('baseline_latency_ms')} ms vs target "
            f"{case.get('target_latency_ms')} ms</p>"
            f"</div></details>"
        )

    fixes = []
    for fix in report.get("prioritized_fixes") or []:
        fixes.append(
            f"<li><strong>P{fix.get('priority')} — {esc(fix.get('title', ''))}</strong><br>"
            f"{esc(fix.get('suggestion', ''))}<br>"
            f"<span class='muted'>cases: {esc(', '.join(fix.get('case_ids') or []) or 'n/a')}</span></li>"
        )

    groups = []
    for group in report.get("failure_groups") or []:
        groups.append(
            f"<li><code>{esc(group.get('key', ''))}</code> × {group.get('count', 0)} "
            f"<span class='muted'>({esc(', '.join(group.get('case_ids') or []))})</span></li>"
        )

    gaps = report.get("capability_gaps") or []
    gaps_html = (
        "".join(f"<li><strong>{esc(gap.get('capability', ''))}</strong> — {esc(gap.get('detail', ''))}</li>" for gap in gaps)
        or "<li>none flagged</li>"
    )

    boundaries = "".join(f"<li>{esc(boundary)}</li>" for boundary in report.get("boundaries") or [])
    notes = "".join(f"<li>{esc(note)}</li>" for note in report.get("notes") or [])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SwitchIt! migration report</title>
<style>
  body {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; margin: 2rem auto; max-width: 1080px;
         padding: 0 1.5rem; color: #333; background: #fafafa; line-height: 1.55; }}
  h1 {{ font-size: 1.6rem; }} h2 {{ font-size: 1.2rem; margin-top: 2.2rem; }} h4 {{ margin: 0.6rem 0 0.2rem; }}
  .muted {{ color: #777; }} code {{ background: #eee; padding: 0 4px; border-radius: 3px; }}
  .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin: 1rem 0; }}
  .card {{ border: 2px solid #999; border-radius: 8px; padding: 0.7rem 1.1rem; background: #fff; min-width: 110px; }}
  .card .n {{ font-size: 1.5rem; font-weight: 700; }}
  .v-pass {{ color: #1e7d3c; }} .v-fail {{ color: #b3541e; }} .v-error {{ color: #b03030; }} .v-uncertain {{ color: #8a6d00; }}
  .c-pass {{ color: #1e7d3c; }} .c-fail {{ color: #b03030; }} .c-unknown {{ color: #8a6d00; }}
  table {{ border-collapse: collapse; margin: 0.5rem 0; }}
  th, td {{ border: 1px solid #ccc; padding: 4px 10px; text-align: left; font-size: 0.85rem; }}
  details.case {{ border: 1px solid #bbb; border-radius: 6px; padding: 0.5rem 0.9rem; margin: 0.5rem 0; background: #fff; }}
  summary {{ cursor: pointer; font-weight: 600; }}
  .verdict {{ border: 1px solid currentColor; border-radius: 4px; padding: 0 6px; font-size: 0.8rem; }}
  ul {{ padding-left: 1.2rem; }}
  .error-text {{ color: #b03030; }}
  footer {{ margin-top: 3rem; border-top: 1px solid #ccc; padding-top: 1rem; }}
</style>
</head>
<body>
<h1>SWITCHIT! / MIGRATION REPORT</h1>
<p class="muted">generated {esc(report.get('generated_at', ''))} · runner {esc(str(report.get('runner_version')))}
 · fingerprint {esc(str(report.get('fingerprint'))[:16])}…</p>
<p class="muted">baseline: {esc(json.dumps(report.get('baseline_spec') or {}))}<br>
target: {esc(json.dumps(report.get('target_spec') or {}))}</p>

<h2>Summary</h2>
<div class="cards">
  <div class="card"><div class="n">{total_cases}</div>cases</div>
  <div class="card"><div class="n v-pass">{verdicts.get('pass', 0)}</div>pass</div>
  <div class="card"><div class="n v-fail">{verdicts.get('fail', 0)}</div>fail</div>
  <div class="card"><div class="n v-error">{verdicts.get('error', 0)}</div>error</div>
  <div class="card"><div class="n v-uncertain">{verdicts.get('uncertain', 0)}</div>uncertain</div>
</div>

<h2>Capability gaps</h2>
<ul>{gaps_html}</ul>

<h2>Failure groups</h2>
<ul>{''.join(groups) or '<li>none</li>'}</ul>

<h2>Prioritized fixes</h2>
<ol>{''.join(fixes) or '<li>none — every case passed its checks</li>'}</ol>

<h2>Usage by provenance</h2>
<h4>Baseline ({esc(str((report.get('baseline_spec') or {}).get('kind')))})</h4>
<table><tr><th>basis</th><th>calls</th><th>input tokens</th><th>output tokens</th><th>missing either</th></tr>
{usage_rows('baseline')}</table>
<h4>Target ({esc(str((report.get('target_spec') or {}).get('kind')))})</h4>
<table><tr><th>basis</th><th>calls</th><th>input tokens</th><th>output tokens</th><th>missing either</th></tr>
{usage_rows('target')}</table>

<h2>Cases</h2>
{''.join(case_cards) or '<p>no cases</p>'}

<footer>
<h2>Boundaries</h2>
<ul>{boundaries}</ul>
<h2>Notes</h2>
<ul>{notes}</ul>
</footer>
</body>
</html>
"""
