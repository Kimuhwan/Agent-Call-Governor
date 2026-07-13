#!/usr/bin/env python3
"""Deterministic budget and duplicate gate for Agent Call Governor."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROGRESS_VALUES = {"sufficient", "material_progress", "low_progress", "no_progress"}
REQUIRED_FIELDS = (
    "objective",
    "route",
    "capability_gap",
    "expected_new_information",
    "stop_condition",
)


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key).strip().lower(): _normalize(value[key]) for key in sorted(value, key=lambda item: str(item).lower())}
    if isinstance(value, list):
        normalized = [_normalize(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    return value


def fingerprint(proposal: dict[str, Any]) -> str:
    identity = {
        "objective": proposal.get("objective", ""),
        "route": proposal.get("route", ""),
        "material_inputs": proposal.get("material_inputs", {}),
    }
    canonical = json.dumps(_normalize(identity), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def evaluate(document: dict[str, Any]) -> dict[str, Any]:
    proposal = document.get("proposal")
    if not isinstance(proposal, dict):
        raise ValueError("proposal must be an object")

    missing = [field for field in REQUIRED_FIELDS if not _nonempty(proposal.get(field))]
    if missing:
        raise ValueError("missing or empty proposal fields: " + ", ".join(missing))

    history = document.get("history", [])
    budget = document.get("budget", {})
    if not isinstance(history, list) or not isinstance(budget, dict):
        raise ValueError("history must be an array and budget must be an object")

    limit = budget.get("limit", 1)
    used = budget.get("used", 0)
    if not isinstance(limit, int) or not isinstance(used, int) or limit < 0 or used < 0:
        raise ValueError("budget limit and used must be non-negative integers")

    current = fingerprint(proposal)
    remaining = max(limit - used, 0)
    seen: set[str] = set()
    progress: list[str] = []
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError("each history item must be an object")
        if isinstance(entry.get("fingerprint"), str):
            seen.add(entry["fingerprint"])
        if "progress" in entry:
            if entry["progress"] not in PROGRESS_VALUES:
                raise ValueError(f"invalid progress value: {entry['progress']}")
            progress.append(entry["progress"])

    if current in seen:
        return _decision(False, "duplicate_fingerprint", current, remaining)
    if "sufficient" in progress:
        return _decision(False, "stop_condition_already_satisfied", current, remaining)
    if progress and progress[-1] == "no_progress":
        return _decision(False, "no_progress_stop", current, remaining)
    if progress.count("low_progress") >= 2:
        return _decision(False, "low_progress_retry_exhausted", current, remaining)
    if used >= limit:
        return _decision(False, "budget_exhausted", current, remaining)
    return _decision(True, "allowed", current, max(remaining - 1, 0))


def _decision(allowed: bool, reason: str, current: str, remaining: int) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "reason": reason,
        "fingerprint": current,
        "remaining_after_call": remaining,
    }


def _read(path: str) -> dict[str, Any]:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("evaluate", "fingerprint"))
    parser.add_argument("input", help="JSON file path, or - for stdin")
    args = parser.parse_args()

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


if __name__ == "__main__":
    raise SystemExit(main())
