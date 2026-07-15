"""Normalize Codex hook deliveries without retaining host payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .ledger import CallLedger, DuplicateCallIdError
from .models import CallEvent, CallProposal, utc_now
from .runtime import GovernedRuntime, GovernanceInternalError


SUPPORTED_EVENTS = frozenset({
    "SessionStart",
    "PreToolUse",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
})
_START_EVENTS = frozenset({"PreToolUse", "SubagentStart"})
_PROFILES = frozenset({"strict", "balanced", "quality-first"})
_RISKS = frozenset({"low", "medium", "high"})
_PARENT_FIELDS = ("parent_tool_use_id", "parent_agent_id", "parent_id")


def host_reference(value: str) -> str:
    """Return the only representation allowed for a host identifier."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("host identifier must be a non-empty string")
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def handle_codex_hook(
    payload: Mapping[str, Any],
    runtime: GovernedRuntime,
    *,
    observed_at: str | None = None,
    retention_days: int = 7,
    stale_reservation_seconds: int = 86400,
) -> dict[str, str] | None:
    """Record one of the six supported Codex lifecycle deliveries."""
    if runtime.mode == "enforce":
        raise ValueError("Codex hooks support observe or warn mode; use GovernedRuntime for enforcement")
    if not isinstance(payload, Mapping):
        raise ValueError("Codex hook payload must be a JSON object")

    event_name = _exact_optional_string(payload.get("hook_event_name"))
    if event_name not in SUPPORTED_EVENTS:
        return None
    timestamp = _timestamp(observed_at)
    host_session_id = _exact_required_string(payload, "session_id")
    trace_id = host_reference(host_session_id)

    if event_name == "SessionStart":
        _best_effort(lambda: runtime.ledger.prune_expired_sessions(retention_days))
        if stale_reservation_seconds > 0:
            _best_effort(lambda: runtime.ledger.recover_stale_reservations(
                trace_id,
                stale_reservation_seconds,
                now=_datetime(timestamp),
            ))
        event = _session_event(
            event_type="session.started",
            trace_id=trace_id,
            turn_id=None,
            runtime=runtime,
            observed_at=timestamp,
            delivery_digest=_hash_json(payload),
            source_event=event_name,
        )
        runtime.ledger.append_bounded_delivery_if_new(
            event,
            event.input_digest,
            window_seconds=5,
        )
        return None

    if event_name == "Stop":
        host_turn_id = _exact_optional_string(payload.get("turn_id"))
        turn_id = None if host_turn_id is None else host_reference(host_turn_id)
        event = _session_event(
            event_type="session.stopped",
            trace_id=trace_id,
            turn_id=turn_id,
            runtime=runtime,
            observed_at=timestamp,
            delivery_digest=_hash_json(payload),
            source_event=event_name,
        )
        if turn_id is None:
            runtime.ledger.append_bounded_delivery_if_new(
                event,
                event.input_digest,
                window_seconds=5,
            )
        else:
            runtime.ledger.append_event_if_new(event)
        _best_effort(lambda: runtime.ledger.prune_expired_sessions(retention_days))
        return None

    call_id = _call_reference(payload, event_name, trace_id)
    session_id, turn_id = _policy_scope(payload, trace_id, call_id)
    if event_name in _START_EVENTS:
        proposal = _proposal_for_start(
            payload,
            event_name,
            session_id,
            call_id,
            trace_id,
            turn_id,
            runtime,
        )
        try:
            handle = runtime.begin(
                proposal,
                call_id=call_id,
                source="codex-hook",
                source_event=event_name,
            )
        except DuplicateCallIdError as exc:
            raise ValueError(
                "Codex hook call ID was reused with different material inputs"
            ) from exc
        if runtime.mode == "warn" and not handle.decision.policy_allowed:
            return _warning_output(handle.decision.reason)
        return None

    started = runtime.ledger.latest_event(session_id, call_id)
    if started is None or started.phase not in {"proposed", "started"}:
        return None
    _append_terminal(runtime, started, payload, event_name, timestamp)
    return None


def deterministic_progress(
    payload: Mapping[str, Any],
) -> tuple[str, dict[str, bool | int | str]] | None:
    """Extract one strict, privacy-safe progress signal with fixed precedence."""
    if not isinstance(payload, Mapping):
        return None
    sources: list[Mapping[str, Any]] = [payload]
    nested = payload.get("tool_response")
    if isinstance(nested, Mapping):
        sources.append(nested)

    candidates: list[tuple[int, int, int, str, bool | int | str]] = []
    order = {
        "acceptance_criterion_status": 0,
        "file_changed": 1,
        "result_digest_changed": 2,
        "new_unique_source_count": 3,
        "test_status": 4,
        "exit_code": 5,
    }
    for source_index, source in enumerate(sources):
        acceptance = source.get("acceptance_criterion_status")
        if isinstance(acceptance, str) and acceptance == "satisfied":
            candidates.append((3, order["acceptance_criterion_status"], source_index,
                               "acceptance_criterion_status", acceptance))
        file_changed = source.get("file_changed")
        if file_changed is True:
            candidates.append((2, order["file_changed"], source_index,
                               "file_changed", True))
        digest_changed = source.get("result_digest_changed")
        if digest_changed is True:
            candidates.append((2, order["result_digest_changed"], source_index,
                               "result_digest_changed", True))
        source_count = source.get("new_unique_source_count")
        if type(source_count) is int and source_count > 0:
            candidates.append((2, order["new_unique_source_count"], source_index,
                               "new_unique_source_count", source_count))
        test_status = source.get("test_status")
        if isinstance(test_status, str) and test_status == "passed":
            candidates.append((2, order["test_status"], source_index,
                               "test_status", test_status))
        exit_code = source.get("exit_code")
        if type(exit_code) is int:
            candidates.append((2 if exit_code == 0 else 1, order["exit_code"], source_index,
                               "exit_code", exit_code))

    if not candidates:
        return None
    priority, _, _, key, value = min(
        candidates,
        key=lambda item: (-item[0], item[1], item[2]),
    )
    progress = {3: "sufficient", 2: "material_progress", 1: "low_progress"}[priority]
    return progress, {key: value}


def _proposal_for_start(
    payload: Mapping[str, Any],
    event_name: str,
    session_id: str,
    call_id: str,
    trace_id: str,
    turn_id: str | None,
    runtime: GovernedRuntime,
) -> CallProposal:
    if event_name == "PreToolUse":
        route = _normalized_required_string(payload, "tool_name")
        input_digest = _hash_json(payload.get("tool_input", {}))
        budget_kind = "direct-tool"
        objective = f"Codex tool call: {route}"
        capability_gap = "Codex selected an external tool capability"
        expected = f"A result from {route}"
        stop_condition = "One tool response is received"
        material_inputs = {"tool_name": route, "input_digest": input_digest}
    else:
        agent_type = _normalized_required_string(payload, "agent_type")
        route = f"subagent:{agent_type}"
        budget_kind = "agent"
        objective = f"Codex subagent call: {agent_type}"
        capability_gap = "Codex selected delegated agent work"
        expected = f"A result from the {agent_type} subagent"
        stop_condition = "The subagent stops"
        delegated_task = next(
            (
                value
                for key in ("task", "prompt", "description")
                if (value := _exact_optional_string(payload.get(key))) is not None
            ),
            None,
        )
        if delegated_task is not None:
            material_inputs = {"delegated_task_digest": host_reference(delegated_task)}
        else:
            host_agent_id = _exact_optional_string(payload.get("agent_id"))
            material_inputs = {
                "opaque_invocation_digest": (
                    host_reference(host_agent_id) if host_agent_id is not None else call_id
                )
            }

    return CallProposal(
        session_id=session_id,
        objective=objective,
        route=route,
        capability_gap=capability_gap,
        expected_new_information=expected,
        stop_condition=stop_condition,
        material_inputs=material_inputs,
        budget_kind=budget_kind,
        profile=runtime.default_profile,
        quality_risk=runtime.default_risk,
        parent_call_id=_parent_reference(payload),
        metadata={},
        trace_id=trace_id,
        turn_id=turn_id,
    )


def _policy_scope(
    payload: Mapping[str, Any],
    trace_id: str,
    call_id: str,
) -> tuple[str, str | None]:
    host_turn_id = _exact_optional_string(payload.get("turn_id"))
    if host_turn_id is not None:
        turn_id = host_reference(host_turn_id)
        return f"codex:trace:{trace_id[7:]}:turn:{turn_id[7:]}", turn_id
    return f"codex:trace:{trace_id[7:]}:call:{call_id[7:]}", None


def _call_reference(
    payload: Mapping[str, Any],
    event_name: str,
    trace_id: str,
) -> str:
    is_agent = event_name in {"SubagentStart", "SubagentStop"}
    supplied = _exact_optional_string(
        payload.get("agent_id" if is_agent else "tool_use_id")
    )
    namespace = "agent" if is_agent else "tool-use"
    if supplied is not None:
        return host_reference(f"{namespace}:{supplied}")
    route = _normalized_required_string(
        payload,
        "agent_type" if is_agent else "tool_name",
    )
    host_turn_id = _exact_optional_string(payload.get("turn_id"))
    turn_reference = "none" if host_turn_id is None else host_reference(host_turn_id)
    input_digest = "none" if is_agent else _hash_json(payload.get("tool_input", {}))
    return host_reference(
        "\x1f".join((namespace, trace_id, turn_reference, route, input_digest))
    )


def _parent_reference(payload: Mapping[str, Any]) -> str | None:
    namespaces = {
        "parent_tool_use_id": "tool-use",
        "parent_agent_id": "agent",
        "parent_id": "parent",
    }
    for key in _PARENT_FIELDS:
        value = _exact_optional_string(payload.get(key))
        if value is not None:
            return host_reference(f"{namespaces[key]}:{value}")
    return None


def _append_terminal(
    runtime: GovernedRuntime,
    started: CallEvent,
    payload: Mapping[str, Any],
    event_name: str,
    observed_at: str,
) -> None:
    failed = event_name == "PostToolUse" and payload.get("is_error") is True
    event_type = "call.failed" if failed else "call.completed"
    phase = "failed" if failed else "completed"
    progress_signal = deterministic_progress(payload)
    progress = "unknown" if progress_signal is None else progress_signal[0]
    metadata: Mapping[str, Any] = {} if progress_signal is None else progress_signal[1]
    terminal = CallEvent.create(
        session_id=started.session_id,
        call_id=started.call_id,
        parent_call_id=started.parent_call_id,
        phase=phase,
        occurred_at=observed_at,
        objective=started.objective,
        route=started.route,
        fingerprint=started.fingerprint,
        budget_kind=started.budget_kind,
        profile=started.profile,
        quality_risk=started.quality_risk,
        mode=started.mode,
        event_type=event_type,
        observed_at=observed_at,
        trace_id=started.trace_id,
        turn_id=started.turn_id,
        span_id=started.span_id,
        parent_span_id=started.parent_span_id,
        source_event=event_name,
        agent_id=started.agent_id,
        tool_name=started.tool_name,
        input_digest=started.input_digest,
        fingerprint_version=started.fingerprint_version,
        failure_policy=started.failure_policy,
        progress=progress,
        status="failed" if failed else "completed",
        source="codex-hook",
        metadata=metadata,
        error_type="CodexToolError" if failed else None,
    )
    progress_event = None
    if progress_signal is not None:
        progress_id = str(uuid.uuid4())
        progress_event = CallEvent.create(
            session_id=started.session_id,
            call_id=progress_id,
            parent_call_id=started.call_id,
            phase="completed",
            occurred_at=observed_at,
            objective=started.objective,
            route=started.route,
            fingerprint=started.fingerprint,
            budget_kind=started.budget_kind,
            profile=started.profile,
            quality_risk=started.quality_risk,
            mode=started.mode,
            event_type="progress.observed",
            observed_at=observed_at,
            trace_id=started.trace_id,
            turn_id=started.turn_id,
            span_id=progress_id,
            parent_span_id=started.span_id,
            source_event=event_name,
            input_digest=started.input_digest,
            fingerprint_version=started.fingerprint_version,
            failure_policy=started.failure_policy,
            progress=progress,
            status="observed",
            source="codex-hook",
            metadata=metadata,
        )
    try:
        runtime.ledger.append_terminal_with_progress_if_open(terminal, progress_event)
    except Exception as exc:
        if runtime.failure_policy == "fail-closed":
            raise GovernanceInternalError(
                f"Agent Call Governor ledger failed during hook completion: {type(exc).__name__}"
            ) from exc


def _session_event(
    *,
    event_type: str,
    trace_id: str,
    turn_id: str | None,
    runtime: GovernedRuntime,
    observed_at: str,
    delivery_digest: str,
    source_event: str,
) -> CallEvent:
    if event_type == "session.stopped" and turn_id is not None:
        call_id = turn_id
    else:
        call_id = str(uuid.uuid4())
    return CallEvent.create(
        session_id=trace_id,
        call_id=call_id,
        phase="started" if event_type == "session.started" else "completed",
        occurred_at=observed_at,
        objective=host_reference(event_type),
        route="codex:session",
        fingerprint=delivery_digest,
        budget_kind="agent",
        profile=runtime.default_profile,
        quality_risk=runtime.default_risk,
        mode=runtime.mode,
        event_type=event_type,
        observed_at=observed_at,
        trace_id=trace_id,
        turn_id=turn_id,
        span_id=call_id,
        source_event=source_event,
        input_digest=delivery_digest,
        failure_policy=runtime.failure_policy,
        status="started" if event_type == "session.started" else "stopped",
        source="codex-hook",
        metadata={},
    )


def _warning_output(reason: str) -> dict[str, str]:
    return {
        "systemMessage": (
            "Agent Call Governor warning: policy would block this call "
            f"({reason}). Codex hooks are observe/warn only."
        )
    }


def _best_effort(operation: Callable[[], Any]) -> None:
    try:
        operation()
    except Exception:
        pass


def _hash_json(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("tool_input must be JSON-compatible") from exc
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _timestamp(value: str | None) -> str:
    if value is None:
        return utc_now()
    _datetime(value)
    return value


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return parsed


def _exact_required_string(payload: Mapping[str, Any], key: str) -> str:
    value = _exact_optional_string(payload.get(key))
    if value is None:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _exact_optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _normalized_required_string(payload: Mapping[str, Any], key: str) -> str:
    return _exact_required_string(payload, key).strip()


def _default_db_path() -> Path:
    configured = os.environ.get("AGENT_CALL_GOVERNOR_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex" / "agent-call-governor" / "events.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record one Codex lifecycle hook event")
    parser.add_argument("--mode", choices=("observe", "warn"), default="observe")
    parser.add_argument("--failure-policy", choices=("fail-open", "fail-closed"), default="fail-open")
    parser.add_argument("--profile", choices=tuple(sorted(_PROFILES)), default="balanced")
    parser.add_argument("--risk", choices=tuple(sorted(_RISKS)), default="medium")
    parser.add_argument("--db", type=Path, default=_default_db_path())
    parser.add_argument("--jsonl", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Codex hook payload must be a JSON object")
        ledger = CallLedger(args.db, args.jsonl)
        runtime = GovernedRuntime(
            ledger,
            mode=args.mode,
            failure_policy=args.failure_policy,
            warning_handler=lambda _message: None,
            source="codex-hook",
            default_profile=args.profile,
            default_risk=args.risk,
        )
        output = handle_codex_hook(payload, runtime)
    except (json.JSONDecodeError, OSError, ValueError, GovernanceInternalError) as exc:
        print(f"agent-call-governor hook error: {type(exc).__name__}", file=sys.stderr)
        return 2
    if output is not None:
        json.dump(output, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
