"""Deterministic tool-aware fingerprints for governed calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


FINGERPRINT_VERSION = 2


def _digest(value: str, field_name: str) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must contain only UTF-8-encodable text") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _canonical_json(value: Any, field_name: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-compatible") from exc


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    _digest(value, field_name)
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    _digest(value, field_name)
    return value


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
    objective = _required_text(objective, "objective")
    route = _required_text(route, "route")
    cwd = _optional_text(cwd, "cwd")
    tool_version = _optional_text(tool_version, "tool_version")
    state_token = _optional_text(state_token, "state_token")
    if not isinstance(material_inputs, Mapping):
        raise ValueError("material_inputs must be an object")
    input_digest = _digest(
        _canonical_json(dict(material_inputs), "material_inputs"),
        "material_inputs",
    )
    objective_digest = _digest(objective, "objective")
    state_token_digest = _digest(state_token, "state_token") if state_token is not None else None
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
        digest=_digest(_canonical_json(identity, "fingerprint identity"), "fingerprint identity"),
        input_digest=input_digest,
        objective_digest=objective_digest,
        canonicalizer_version=canonicalizer_version,
        state_token_digest=state_token_digest,
    )
