"""SwitchIt! — evidence-based migration of AI workflows between providers.

The harness imports authorized traces, normalizes them into a canonical
store, maps provisional workflows, selects replay cases, replays them
through recorded/offline or live provider adapters, and produces a local
JSON + HTML migration report.

Boundaries kept honest throughout:
- Workflow labels and similarity scores are triage aids, not measured accuracy.
- Proposed tool calls are captured, never executed.
- A historical model response is reference behavior, not proof of success.
- Token provenance stays separated: recorded, estimated, unknown.
"""

__version__ = "0.1.0"

USAGE_BASES = ("recorded", "estimated", "unknown")

from . import core, providers, runner  # noqa: E402,F401

__all__ = ["__version__", "core", "providers", "runner", "USAGE_BASES"]
