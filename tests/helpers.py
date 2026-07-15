from __future__ import annotations

from agent_call_governor_runtime import CallEvent


PHASES = {
    "call.proposed": "proposed", "policy.decided": "proposed",
    "call.started": "started", "call.blocked": "blocked",
    "call.completed": "completed", "call.failed": "failed",
    "call.cancelled": "cancelled", "session.started": "started",
    "progress.observed": "completed", "session.stopped": "completed",
}


def make_event(
    *,
    session_id: str,
    call_id: str,
    event_type: str = "call.completed",
    observed_at: str = "2026-07-14T00:00:00Z",
) -> CallEvent:
    return CallEvent.create(
        session_id=session_id,
        call_id=call_id,
        phase=PHASES[event_type],
        occurred_at=observed_at,
        objective="sha256:" + "a" * 64,
        route="test:fixture",
        fingerprint="sha256:" + "b" * 64,
        budget_kind="agent",
        profile="balanced",
        quality_risk="medium",
        mode="observe",
        event_type=event_type,
        observed_at=observed_at,
        trace_id=session_id,
        turn_id=None,
        span_id=call_id,
        parent_span_id=None,
        source="runtime",
        source_event="test",
        fingerprint_version=2,
        failure_policy="fail-open",
        progress="unknown" if event_type == "call.completed" else None,
        raw_input_stored=False,
    )
