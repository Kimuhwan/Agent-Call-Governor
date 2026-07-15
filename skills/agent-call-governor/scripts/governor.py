#!/usr/bin/env python3
"""Backward-compatible CLI for the Agent Call Governor policy."""

from agent_call_governor_runtime.policy import (
    BUDGET_KINDS,
    MANDATORY_REASONS,
    PROFILE_LIMITS,
    PROFILE_NAMES,
    PROGRESS_VALUES,
    REQUIRED_FIELDS,
    RISK_VALUES,
    evaluate,
    fingerprint,
    main,
)

__all__ = [
    "BUDGET_KINDS",
    "MANDATORY_REASONS",
    "PROFILE_LIMITS",
    "PROFILE_NAMES",
    "PROGRESS_VALUES",
    "REQUIRED_FIELDS",
    "RISK_VALUES",
    "evaluate",
    "fingerprint",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
