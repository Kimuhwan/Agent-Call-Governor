"""Observe Codex lifecycle hooks without persisting tool inputs or outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .ledger import CallLedger, DuplicateCallIdError
from .models import PROGRESS_VALUES, CallEvent, CallProposal
from .runtime import GovernedRuntime, GovernanceInternalError


_START_EVENTS = {"PreToolUse", "SubagentStart"}
_END_EVENTS = {"PostToolUse", "SubagentStop"}
_SUPPORTED_EVENTS = _START_EVENTS | _END_EVENTS


def handle_codex_hook(
    payload: Mapping[str, Any],
    runtime: GovernedRuntime,
) -> dict[str, Any] | None:
    """Map one official Codex hook payload to the runtime lifecycle ledger.

    Codex currently exposes these hooks as observation/context surfaces, not as
    a reliable pre-execution veto. For that reason this adapter accepts only
    ``observe`` and ``warn`` runtimes.
    """
    if runtime.mode == "enforce":
        raise ValueError("Codex hooks support observe or warn mode; use GovernedRuntime for enforcement")
    if not isinstance(payload, Mapping):
        raise ValueError("Codex hook payload must be a JSON object")

    event_name = _optional_string(payload.get("hook_event_name"))
    if event_name not in _SUPPORTED_EVENTS:
        return None
    host_session_id = _required_string(payload, "session_id")
    call_id = _call_id(payload, event_name, host_session_id)
    session_id = _governance_scope_id(payload, host_session_id, call_id)

    if event_name in _START_EVENTS:
        proposal = _proposal_for_start(payload, event_name, session_id, call_id)
        existing = runtime.ledger.latest_event(session_id, call_id)
        if existing is not None:
            return _replayed_start_output(existing, proposal, runtime.mode)
        try:
            handle = runtime.begin(proposal, call_id=call_id, source="codex-hook")
        except DuplicateCallIdError:
            # Another hook process may have committed the same host event after
            # our precheck. Re-read the authoritative decision and treat an
            # identical fingerprint as at-least-once delivery.
            existing = runtime.ledger.latest_event(session_id, call_id)
            if existing is None:
                raise
            return _replayed_start_output(existing, proposal, runtime.mode)
        if runtime.mode == "warn" and not handle.decision.policy_allowed:
            return _warning_output(handle.decision.reason)
        return None

    started = runtime.ledger.latest_event(session_id, call_id)
    if started is None or started.phase not in {"proposed", "started"}:
        return None
    _append_terminal(runtime, started, payload, event_name)
    return None


def _proposal_for_start(
    payload: Mapping[str, Any],
    event_name: str,
    session_id: str,
    call_id: str,
) -> CallProposal:
    if event_name == "PreToolUse":
        route = _required_string(payload, "tool_name")
        input_value = payload.get("tool_input", {})
        input_hash = _hash_json(input_value)
        budget_kind = "direct-tool"
        objective = f"Codex tool call: {route}"
        capability_gap = "Codex selected an external tool capability"
        expected = f"A result from {route}"
        stop_condition = "One tool response is received"
        material_inputs = {"tool_name": route, "input_sha256": input_hash}
    else:
        agent_type = _required_string(payload, "agent_type")
        route = f"subagent:{agent_type}"
        budget_kind = "agent"
        objective = f"Codex subagent call: {agent_type}"
        capability_gap = "Codex selected delegated agent work"
        expected = f"A result from the {agent_type} subagent"
        stop_condition = "The subagent stops"
        # The hook does not expose the delegated task body. Use the unique host
        # invocation identity to avoid merging legitimate same-type subagents.
        material_inputs = {
            "agent_type": agent_type,
            "invocation_sha256": hashlib.sha256(call_id.encode("utf-8")).hexdigest(),
        }

    metadata = _safe_metadata(payload, event_name)
    proposal = CallProposal(
        session_id=session_id,
        objective=objective,
        route=route,
        capability_gap=capability_gap,
        expected_new_information=expected,
        stop_condition=stop_condition,
        material_inputs=material_inputs,
        budget_kind=budget_kind,
        profile=_safe_choice(payload.get("governor_profile"), {"strict", "balanced", "quality-first"}, "balanced"),
        quality_risk=_safe_choice(payload.get("governor_quality_risk"), {"low", "medium", "high"}, "medium"),
        metadata=metadata,
    )
    return proposal


def _governance_scope_id(
    payload: Mapping[str, Any],
    host_session_id: str,
    call_id: str,
) -> str:
    turn_id = _optional_string(payload.get("turn_id"))
    session_ref = _hash_identifier(host_session_id)
    if turn_id is not None:
        return f"codex:session:{session_ref}:turn:{_hash_identifier(turn_id)}"
    # Older payloads without turn identity get a call-local scope. This keeps
    # lifecycle accounting without carrying budgets across unrelated tasks.
    return f"codex:session:{session_ref}:call:{call_id}"


def _warning_output(reason: str) -> dict[str, str]:
    return {
        "systemMessage": (
            "Agent Call Governor warning: this call would be blocked by the active "
            f"policy ({reason}). Codex hooks are observe/warn only; the call will continue."
        )
    }


def _replayed_start_output(
    existing: CallEvent,
    proposal: CallProposal,
    mode: str,
) -> dict[str, str] | None:
    if existing.fingerprint != proposal.fingerprint:
        raise ValueError("Codex hook call ID was reused with different material inputs")
    if mode == "warn" and not existing.policy_allowed:
        return _warning_output(existing.decision_reason or "policy_rejected")
    return None


def _call_id(payload: Mapping[str, Any], event_name: str, session_id: str) -> str:
    is_agent = event_name in {"SubagentStart", "SubagentStop"}
    supplied = _optional_string(payload.get("agent_id" if is_agent else "tool_use_id"))
    prefix = "codex-agent" if is_agent else "codex-tool"
    if supplied is not None:
        return f"{prefix}:host:{_hash_identifier(supplied)}"

    route_key = "agent_type" if is_agent else "tool_name"
    route = _required_string(payload, route_key)
    turn_id = _optional_string(payload.get("turn_id")) or "unknown-turn"
    input_hash = ""
    if not is_agent:
        input_hash = _hash_json(payload.get("tool_input", {}))
    material = "\x1f".join((session_id, turn_id, route, input_hash))
    return f"{prefix}:derived:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _append_terminal(
    runtime: GovernedRuntime,
    started: CallEvent,
    payload: Mapping[str, Any],
    event_name: str,
) -> None:
    failed = bool(payload.get("is_error")) or payload.get("tool_error") is not None or payload.get("error") is not None
    phase = "failed" if failed else "completed"
    default_progress = "low_progress" if failed else "material_progress"
    progress = _safe_choice(payload.get("governor_progress"), PROGRESS_VALUES, default_progress)
    terminal = CallEvent.create(
        session_id=started.session_id,
        call_id=started.call_id,
        parent_call_id=started.parent_call_id,
        phase=phase,
        objective=started.objective,
        route=started.route,
        fingerprint=started.fingerprint,
        budget_kind=started.budget_kind,
        profile=started.profile,
        quality_risk=started.quality_risk,
        mode=started.mode,
        policy_allowed=started.policy_allowed,
        execution_allowed=started.execution_allowed,
        decision_reason=started.decision_reason,
        progress=progress,
        source="codex-hook",
        metadata=_safe_metadata(payload, event_name),
        error_type="CodexToolError" if failed and event_name == "PostToolUse" else (
            "CodexSubagentError" if failed else None
        ),
    )
    try:
        append_terminal = getattr(runtime.ledger, "append_terminal_if_open", None)
        if callable(append_terminal):
            append_terminal(terminal)
        else:
            runtime.ledger.append(terminal)
    except Exception as exc:
        if runtime.failure_policy == "fail-closed":
            raise GovernanceInternalError(
                f"Agent Call Governor ledger failed during hook completion: {type(exc).__name__}"
            ) from exc


def _safe_metadata(payload: Mapping[str, Any], event_name: str) -> dict[str, str]:
    metadata = {"hook_event_name": event_name}
    host_session_id = _optional_string(payload.get("session_id"))
    if host_session_id is not None:
        metadata["codex_session_sha256"] = _hash_identifier(host_session_id)
    turn_id = _optional_string(payload.get("turn_id"))
    if turn_id is not None:
        metadata["codex_turn_sha256"] = _hash_identifier(turn_id)
    for key in ("tool_name", "agent_type", "model", "permission_mode"):
        value = _optional_string(payload.get(key))
        if value is not None:
            metadata[key] = value
    return metadata


def _hash_json(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("tool_input must be JSON-compatible") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = _optional_string(payload.get(key))
    if value is None:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _safe_choice(value: Any, allowed: set[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _default_db_path() -> Path:
    configured = os.environ.get("AGENT_CALL_GOVERNOR_DB")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex" / "agent-call-governor" / "events.sqlite3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record one Codex lifecycle hook event")
    parser.add_argument("--mode", choices=("observe", "warn"), default="observe")
    parser.add_argument("--failure-policy", choices=("fail-open", "fail-closed"), default="fail-open")
    parser.add_argument("--db", type=Path, default=_default_db_path())
    parser.add_argument("--jsonl", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw_stdin = sys.stdin.buffer.read()
        payload = json.loads(raw_stdin.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Codex hook payload must be a JSON object")
        ledger = CallLedger(args.db, args.jsonl)
        runtime = GovernedRuntime(
            ledger,
            mode=args.mode,
            failure_policy=args.failure_policy,
            warning_handler=lambda _message: None,
            source="codex-hook",
        )
        output = handle_codex_hook(payload, runtime)
    except (json.JSONDecodeError, OSError, ValueError, GovernanceInternalError) as exc:
        print(f"agent-call-governor hook error: {exc}", file=sys.stderr)
        return 2
    if output is not None:
        json.dump(output, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
