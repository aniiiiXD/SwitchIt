"""Devin (Cognition) cloud-session importer — via the official API.

Devin runs in Cognition's cloud, so nothing lands on disk. The official v3
API (docs.devin.ai) exposes the session list per organization: ids, titles,
prompts, status, pull requests, and ACU consumption. This importer reads that
list and converts each session into a task-level trace — like Aider, weaker
than model-level telemetry, and labeled as such:

- What we get: what was asked (prompt/title), what state it reached (status),
  what it shipped (pull request URLs), and ACU consumption (ACUs are compute
  units, not tokens, so usage stays out of the token totals).
- What we do not get: message-level transcripts or tool calls — the API does
  not expose them, so these traces support workflow discovery, not replay.

Safety model: this is a network call to a third-party API, so it is an
explicit opt-in. The CLI only runs it when ``--devin`` is passed AND
``DEVIN_API_KEY`` + ``DEVIN_ORG_ID`` are in the environment. The key is never
written to files, logs, or the store.
"""

from __future__ import annotations

import json
import urllib.request

from pathlib import Path

API_BASE = "https://api.devin.ai/v3"


class DevinError(Exception):
    pass


def _fetch_json(url: str, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def fetch_devin_sessions(api_key: str, org_id: str, limit: int = 50) -> list:
    """List sessions from the official API, following the end cursor."""
    sessions = []
    cursor = None
    while len(sessions) < limit:
        url = f"{API_BASE}/organizations/{org_id}/sessions?limit={min(100, limit - len(sessions))}"
        if cursor:
            url += f"&after={cursor}"
        payload = _fetch_json(url, api_key)
        items = payload.get("items") or payload.get("sessions") or []
        if not isinstance(items, list):
            break
        sessions.extend(item for item in items if isinstance(item, dict))
        cursor = payload.get("end_cursor")
        if not payload.get("has_more") or not cursor:
            break
    return sessions[:limit]


def _outcome_text(session: dict) -> str:
    """A metadata-derived outcome summary, clearly labeled as metadata."""
    parts = [f"[session outcome] status={session.get('status') or 'unknown'}"]
    prs = session.get("pull_requests") or []
    if isinstance(prs, list) and prs:
        urls = [pr.get("pr_url") or pr.get("url") for pr in prs if isinstance(pr, dict)]
        urls = [u for u in urls if u]
        if urls:
            parts.append(f"pull_requests={len(urls)}: " + ", ".join(urls[:5]))
    acu = session.get("acus_consumed") or session.get("acu_consumed")
    if acu is not None:
        parts.append(f"acus_consumed={acu} (compute units, not tokens)")
    return "; ".join(parts)


def import_devin(sessions: list) -> list:
    """Convert API session objects into raw canonical calls (task-level)."""
    raw_calls = []
    for session in sessions:
        session_id = str(session.get("session_id") or session.get("id") or "").strip()
        if not session_id:
            continue
        trace_id = f"devin:{session_id}"
        prompt = (session.get("prompt") or session.get("title")
                  or session.get("display_title") or "")
        raw_calls.append(
            {
                "trace_id": trace_id,
                "call_id": f"{session_id}:task",
                "session_id": session_id,
                "call_index": 0,
                "provider": "devin",
                "model": "cloud-agent",
                "request": {"messages": (
                    [{"role": "user", "content": prompt}] if prompt else []
                )},
                "response": {"text": _outcome_text(session),
                             "tool_calls": [], "finish_reason": "stop"},
                "usage": None,
                "usage_basis": None,
                "origin": "cloud (Devin)",
            }
        )
    return raw_calls


def import_devin_remote(limit: int = 50, api_key=None, org_id=None) -> list:
    """Fetch and import Devin sessions. Explicit opt-in enforced by the caller:
    both credentials must come from the environment."""
    import os

    api_key = api_key or os.environ.get("DEVIN_API_KEY")
    org_id = org_id or os.environ.get("DEVIN_ORG_ID")
    if not api_key or not org_id:
        raise DevinError(
            "Devin import needs DEVIN_API_KEY and DEVIN_ORG_ID in the environment "
            "(a cog_ service-user key from docs.devin.ai). Keys are never accepted "
            "on the command line or stored."
        )
    return import_devin(fetch_devin_sessions(api_key, org_id, limit=limit))


def read_devin_state_hint() -> str:
    """Best-effort hint about whether Devin credentials look present."""
    import os

    if os.environ.get("DEVIN_API_KEY") and os.environ.get("DEVIN_ORG_ID"):
        return "Devin credentials present in the environment."
    return "No Devin credentials in the environment (DEVIN_API_KEY, DEVIN_ORG_ID)."
