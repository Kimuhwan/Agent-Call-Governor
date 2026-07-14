#!/usr/bin/env python3
"""Run deterministic quality-preservation and waste-control evaluations."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]
GOVERNOR_PATH = ROOT / "skills" / "agent-call-governor" / "scripts" / "governor.py"
CASES_PATH = Path(__file__).with_name("cases.json")
SPEC = importlib.util.spec_from_file_location("governor", GOVERNOR_PATH)
assert SPEC and SPEC.loader
governor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(governor)

BASE_PROPOSAL = {
    "objective": "Inspect authentication failures",
    "route": "specialist-agent",
    "material_inputs": {"scope": "server/auth"},
    "capability_gap": "Several modules require inspection",
    "expected_new_information": "A source-backed root cause",
    "stop_condition": "A failing path is identified",
}


def _merge_case(raw: dict[str, Any]) -> dict[str, Any]:
    document = deepcopy(raw.get("document", {}))
    proposal = {**BASE_PROPOSAL, **document.get("proposal", {})}
    document["proposal"] = proposal
    for entry in document.get("history", []):
        if entry.get("fingerprint") == "$proposal":
            entry["fingerprint"] = governor.fingerprint(proposal)
    return document


def run() -> dict[str, Any]:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"]
    category_totals: Counter[str] = Counter()
    category_passes: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []

    for case in cases:
        result = governor.evaluate(_merge_case(case))
        expected = case["expected"]
        mismatches = {
            key: {"expected": value, "actual": result.get(key)}
            for key, value in expected.items()
            if result.get(key) != value
        }
        category = case["category"]
        category_totals[category] += 1
        if mismatches:
            failures.append({"name": case["name"], "mismatches": mismatches})
        else:
            category_passes[category] += 1

    total = len(cases)
    passed = total - len(failures)
    return {
        "passed": passed,
        "total": total,
        "policy_accuracy": round(passed / total, 4) if total else 0,
        "quality_preservation_rate": round(
            category_passes["quality_preservation"] / category_totals["quality_preservation"],
            4,
        ),
        "waste_control_rate": round(
            category_passes["waste_control"] / category_totals["waste_control"],
            4,
        ),
        "failures": failures,
    }


def main() -> int:
    result = run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not result["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
