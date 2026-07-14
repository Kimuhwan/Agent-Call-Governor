"""Deterministic tool-aware fingerprints for governed calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


FINGERPRINT_VERSION = 2


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonicalizer(route: str) -> str:
    if route == "Bash":
        return "bash-v1"
    if route in {"apply_patch", "Edit", "Write"}:
        return "workspace-write-v1"
    if route.startswith("mcp:") or route.startswith("mcp__"):
        return "mcp-json-v1"
    return "generic-json-v1"


@dataclass(frozen=True)
class FingerprintResult:
    digest: str
    input_digest: str
    objective_digest: str
    canonicalizer_version: str
    state_token_digest: str | None
    version: int = FINGERPRINT_VERSION


def build_fingerprint(
    *,
    objective: str,
    route: str,
    material_inputs: Mapping[str, Any],
    cwd: str | None = None,
    tool_version: str | None = None,
    state_token: str | None = None,
) -> FingerprintResult:
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("objective must be a non-empty string")
    if not isinstance(route, str) or not route.strip():
        raise ValueError("route must be a non-empty string")
    input_digest = _digest(_canonical_json(dict(material_inputs)))
    objective_digest = _digest(objective)
    state_token_digest = _digest(state_token) if state_token is not None else None
    canonicalizer_version = _canonicalizer(route)
    identity = {
        "canonicalizer_version": canonicalizer_version,
        "cwd": cwd,
        "fingerprint_version": FINGERPRINT_VERSION,
        "input_digest": input_digest,
        "objective_digest": objective_digest,
        "route": route,
        "state_token_digest": state_token_digest,
        "tool_version": tool_version,
    }
    return FingerprintResult(
        digest=_digest(_canonical_json(identity)),
        input_digest=input_digest,
        objective_digest=objective_digest,
        canonicalizer_version=canonicalizer_version,
        state_token_digest=state_token_digest,
    )
