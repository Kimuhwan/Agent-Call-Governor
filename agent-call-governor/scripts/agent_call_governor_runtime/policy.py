"""Deterministic quality floor, budget, progress, and duplicate policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence


PROGRESS_VALUES = {"sufficient", "material_progress", "low_progress", "no_progress"}
PROFILE_NAMES = {"strict", "balanced", "quality-first"}
RISK_VALUES = {"low", "medium", "high"}
BUDGET_KINDS = {"agent", "direct-tool"}
MANDATORY_REASONS = {
    "current_information",
    "explicit_verification",
    "high_stakes",
    "private_state",
    "missing_file",
    "user_requested_action",
    "safety",
    "system_instruction",
}
PROFILE_LIMITS = {
    "strict": {
        "agent": {"low": 0, "medium": 1, "high": 2},
        "direct-tool": {"low": 1, "medium": 3, "high": 5},
        "low_progress_retries": 0,
    },
    "balanced": {
        "agent": {"low": 0, "medium": 2, "high": 3},
        "direct-tool": {"low": 1, "medium": 6, "high": 8},
        "low_progress_retries": 1,
    },
    "quality-first": {
        "agent": {"low": 1, "medium": 3, "high": 4},
        "direct-tool": {"low": 2, "medium": 8, "high": 12},
        "low_progress_retries": 2,
    },
}
REQUIRED_FIELDS = (
    "objective",
    "route",
    "capability_gap",
    "expected_new_information",
    "stop_condition",
)


def fingerprint(proposal: dict[str, Any]) -> str:
    identity = {
        "objective": proposal.get("objective", ""),
        "route": proposal.get("route", ""),
        "material_inputs": proposal.get("material_inputs", {}),
    }
    canonical = json.dumps(
        identity,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _enum(value: Any, allowed: set[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{field} must be one of: {choices}")
    return value


def evaluate(document: dict[str, Any]) -> dict[str, Any]:
    proposal = document.get("proposal")
    if not isinstance(proposal, dict):
        raise ValueError("proposal must be an object")

    missing = [field for field in REQUIRED_FIELDS if not _nonempty(proposal.get(field))]
    if missing:
        raise ValueError("missing or empty proposal fields: " + ", ".join(missing))

    profile = _enum(document.get("profile", "balanced"), PROFILE_NAMES, "profile")
    quality_risk = _enum(document.get("quality_risk", "medium"), RISK_VALUES, "quality_risk")
    mandatory_reason = proposal.get("mandatory_reason")
    if mandatory_reason is not None:
        mandatory_reason = _enum(mandatory_reason, MANDATORY_REASONS, "mandatory_reason")

    history = document.get("history", [])
    budget = document.get("budget", {})
    if not isinstance(history, list) or not isinstance(budget, dict):
        raise ValueError("history must be an array and budget must be an object")

    budget_kind = _enum(budget.get("kind", "agent"), BUDGET_KINDS, "budget.kind")
    profile_limit = PROFILE_LIMITS[profile][budget_kind][quality_risk]
    requested_limit = budget.get("limit", profile_limit)
    if not isinstance(requested_limit, int):
        raise ValueError("budget limit must be an integer")
    if requested_limit < 0:
        raise ValueError("budget limit must be non-negative")

    effective_limit = max(requested_limit, profile_limit)
    floor_applied = effective_limit != requested_limit
    current = fingerprint(proposal)
    seen: set[str] = set()
    progress: list[str] = []
    matching_history_count = 0
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError("each history item must be an object")
        entry_budget_kind = _enum(
            entry.get("budget_kind", budget_kind),
            BUDGET_KINDS,
            "history.budget_kind",
        )
        if entry_budget_kind == budget_kind:
            matching_history_count += 1
        if isinstance(entry.get("fingerprint"), str):
            seen.add(entry["fingerprint"])
        if "progress" in entry:
            progress.append(_enum(entry["progress"], PROGRESS_VALUES, "history.progress"))

    used = budget.get("used", matching_history_count)
    if not isinstance(used, int):
        raise ValueError("budget used must be an integer")
    if used < 0:
        raise ValueError("budget used must be non-negative")
    if used < matching_history_count:
        raise ValueError("budget.used cannot be lower than matching history count")

    remaining = max(effective_limit - used, 0)
    context = {
        "profile": profile,
        "quality_risk": quality_risk,
        "budget_kind": budget_kind,
        "effective_limit": effective_limit,
        "budget_floor_applied": floor_applied,
        "matching_history_count": matching_history_count,
        "mandatory_reason": mandatory_reason,
    }

    if current in seen:
        return _decision(False, "duplicate_fingerprint", current, remaining, context)
    if mandatory_reason:
        return _decision(True, "mandatory_exception", current, max(remaining - 1, 0), context)
    if "sufficient" in progress:
        return _decision(False, "stop_condition_already_satisfied", current, remaining, context)
    if progress and progress[-1] == "no_progress":
        return _decision(False, "no_progress_stop", current, remaining, context)

    low_progress_count = progress.count("low_progress")
    risk_retry_floor = 1 if quality_risk == "high" else 0
    allowed_retries = max(PROFILE_LIMITS[profile]["low_progress_retries"], risk_retry_floor)
    if low_progress_count > allowed_retries:
        return _decision(False, "low_progress_retry_exhausted", current, remaining, context)
    if progress and progress[-1] == "low_progress" and not _nonempty(proposal.get("changed_strategy")):
        return _decision(False, "changed_strategy_required", current, remaining, context)
    if used >= effective_limit:
        return _decision(False, "budget_exhausted", current, remaining, context)
    return _decision(True, "allowed", current, max(remaining - 1, 0), context)


def _decision(
    allowed: bool,
    reason: str,
    current: str,
    remaining: int,
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "reason": reason,
        "fingerprint": current,
        "remaining_after_call": remaining,
        **context,
    }


def _read(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("evaluate", "fingerprint"))
    parser.add_argument("input", help="JSON file path, or - for stdin")
    args = parser.parse_args(argv)

    try:
        document = _read(args.input)
        if args.command == "fingerprint":
            proposal = document.get("proposal", document)
            if not isinstance(proposal, dict):
                raise ValueError("proposal must be an object")
            result: Any = {"fingerprint": fingerprint(proposal)}
            exit_code = 0
        else:
            result = evaluate(document)
            exit_code = 0 if result["allowed"] else 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return exit_code
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
