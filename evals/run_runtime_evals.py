#!/usr/bin/env python3
"""Replay checked-in runtime workloads through the enforce-mode wrapper."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from agent_call_governor_runtime import (
    CallLedger,
    CallProposal,
    GovernedRuntime,
    GovernanceBlocked,
)


CASES_PATH = Path(__file__).with_name("runtime_workloads.json")
BASE_PROPOSAL: dict[str, Any] = {
    "objective": "Inspect a runtime workflow",
    "route": "agent:runtime-specialist",
    "capability_gap": "The workflow requires an external capability",
    "expected_new_information": "A result that closes the current gap",
    "stop_condition": "The current gap is closed",
    "material_inputs": {"scope": "runtime"},
    "budget_kind": "agent",
    "profile": "balanced",
    "quality_risk": "medium",
}


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def run() -> dict[str, Any]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("runtime_workloads.json must contain an array")

    failures: list[dict[str, Any]] = []
    category_totals: Counter[str] = Counter()
    category_passes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    necessary_total = 0
    necessary_executed = 0
    redundant_total = 0
    redundant_blocked = 0
    duplicate_total = 0
    duplicate_blocked = 0
    executed_total = 0
    blocked_total = 0

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                raise ValueError(f"case {index} must be an object")
            name = str(case["name"])
            category = str(case["category"])
            category_totals[category] += 1
            ledger = CallLedger(root / f"case-{index}.sqlite3")
            runtime = GovernedRuntime(ledger, mode="enforce", failure_policy="fail-closed")
            mismatches: list[dict[str, Any]] = []

            for step_index, step in enumerate(case["steps"]):
                values = {**BASE_PROPOSAL, **step.get("proposal", {})}
                proposal = CallProposal(session_id=name, **values)
                expected = step["expected"]
                label = step["label"]
                expected_reason = step.get("expected_reason")
                actual = "execute"
                reason = ""
                try:
                    runtime.run(
                        proposal,
                        lambda: "replayed-result",
                        progress=step.get("progress", "material_progress"),
                    )
                    executed_total += 1
                    reason = ledger.events(name)[-1].decision_reason or ""
                except GovernanceBlocked as exc:
                    actual = "block"
                    blocked_total += 1
                    reason = exc.decision.reason
                reasons[reason] += 1

                if label == "necessary":
                    necessary_total += 1
                    if actual == "execute":
                        necessary_executed += 1
                elif label == "redundant":
                    redundant_total += 1
                    if actual == "block":
                        redundant_blocked += 1
                    if category == "duplicate_blocking":
                        duplicate_total += 1
                        if actual == "block":
                            duplicate_blocked += 1
                else:
                    raise ValueError(f"case {name} step {step_index} has unknown label {label}")

                if actual != expected or (expected_reason is not None and reason != expected_reason):
                    mismatches.append(
                        {
                            "step": step_index,
                            "expected": expected,
                            "actual": actual,
                            "expected_reason": expected_reason,
                            "actual_reason": reason,
                        }
                    )

            if mismatches:
                failures.append({"name": name, "category": category, "mismatches": mismatches})
            else:
                category_passes[category] += 1

    total_cases = len(cases)
    return {
        "passed_cases": total_cases - len(failures),
        "total_cases": total_cases,
        "executed_steps": executed_total,
        "blocked_steps": blocked_total,
        "necessary_calls": necessary_total,
        "redundant_calls": redundant_total,
        "necessary_call_preservation_rate": _rate(necessary_executed, necessary_total),
        "redundant_call_block_rate": _rate(redundant_blocked, redundant_total),
        "duplicate_block_rate": _rate(duplicate_blocked, duplicate_total),
        "under_call_failures": necessary_total - necessary_executed,
        "unexpected_redundant_executions": redundant_total - redundant_blocked,
        "category_pass_rates": {
            category: _rate(category_passes[category], total)
            for category, total in sorted(category_totals.items())
        },
        "decision_reasons": dict(sorted(reasons.items())),
        "failures": failures,
    }


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
