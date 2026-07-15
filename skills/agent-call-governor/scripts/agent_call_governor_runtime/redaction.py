"""Source-aware redaction for persisted and exported event metadata."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .policy import (
    BUDGET_KINDS,
    MANDATORY_REASONS,
    POLICY_CONTEXT_FIELDS,
    PROFILE_NAMES,
    RISK_VALUES,
)


SAFE_METADATA_BY_SOURCE = {
    "runtime": frozenset({"cancelled_before_execution", "exit_code", "file_changed", "test_status"}),
    "codex-hook": frozenset({
        "acceptance_criterion_status", "cancelled_before_execution", "exit_code",
        "file_changed", "new_unique_source_count", "result_digest_changed", "test_status",
    }),
    "agents-sdk": frozenset({"exit_code", "file_changed", "test_status"}),
    "migration": frozenset({"legacy_schema_version"}),
}
SAFE_ENUMS = {
    "acceptance_criterion_status": frozenset({"satisfied", "not_satisfied", "unknown"}),
    "budget_kind": frozenset(BUDGET_KINDS),
    "mandatory_reason": frozenset(MANDATORY_REASONS),
    "profile": frozenset(PROFILE_NAMES),
    "quality_risk": frozenset(RISK_VALUES),
    "test_status": frozenset({"passed", "failed", "unknown"}),
}
SAFE_NULLABLE_ENUMS = frozenset({"mandatory_reason"})
SAFE_SCALAR_TYPES = {
    "budget_floor_applied": bool,
    "cancelled_before_execution": bool,
    "effective_limit": int,
    "exit_code": int,
    "file_changed": bool,
    "legacy_schema_version": int,
    "matching_history_count": int,
    "new_unique_source_count": int,
    "result_digest_changed": bool,
}
SENSITIVE_KEYS = frozenset({
    "authorization", "exception", "exception_message", "input", "objective", "output",
    "prompt", "raw_input", "raw_output", "tool_input", "tool_output",
})
_SHA256_REFERENCE = re.compile(r"sha256:[0-9a-f]{64}")
_CUSTOM_METADATA_KEY = re.compile(r"custom:[0-9a-f]{64}")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_API_KEY = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _hash(value: str) -> str:
    if _SHA256_REFERENCE.fullmatch(value):
        return value
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _custom_key(value: str) -> str:
    return "custom:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def redact_text(value: str, *, home_directory: str | None = None) -> str:
    safe = _BEARER.sub("[REDACTED_AUTHORIZATION]", value)
    safe = _API_KEY.sub("[REDACTED_API_KEY]", safe)
    safe = _EMAIL.sub("[REDACTED_EMAIL]", safe)
    home = str(Path(home_directory).expanduser()) if home_directory else str(Path.home())
    if home:
        home_variants = {home, home.replace("\\", "/"), home.replace("/", "\\")}
        for candidate in home_variants:
            safe = re.sub(
                re.escape(candidate),
                "[REDACTED_HOME]",
                safe,
                flags=re.IGNORECASE,
            )
    return safe


def _safe_value(value: Any, *, known: bool, home_directory: str | None) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value if known else _hash(str(value))
    if isinstance(value, str):
        redacted = redact_text(value, home_directory=home_directory)
        return redacted if known else _hash(redacted)
    if isinstance(value, list):
        return [_safe_value(item, known=known, home_directory=home_directory) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name in SENSITIVE_KEYS:
                result[name] = "[REDACTED]"
                continue
            safe_name = name if _CUSTOM_METADATA_KEY.fullmatch(name) else _custom_key(name)
            result[safe_name] = _safe_value(
                item,
                known=False,
                home_directory=home_directory,
            )
        return result
    return _hash(repr(type(value).__name__))


def sanitize_metadata(
    metadata: Mapping[str, Any],
    *,
    source: str,
    home_directory: str | None = None,
) -> dict[str, Any]:
    allowed = SAFE_METADATA_BY_SOURCE.get(source, frozenset()) | POLICY_CONTEXT_FIELDS
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        name = str(key)
        if _CUSTOM_METADATA_KEY.fullmatch(name):
            result[name] = _safe_value(value, known=False, home_directory=home_directory)
        elif name in SENSITIVE_KEYS:
            result[name] = "[REDACTED]"
        elif name in allowed:
            if name in SAFE_ENUMS and value is None and name in SAFE_NULLABLE_ENUMS:
                result[name] = None
            elif name in SAFE_ENUMS and (
                not isinstance(value, str) or value not in SAFE_ENUMS[name]
            ):
                result[name] = _hash(str(value))
            elif name in SAFE_SCALAR_TYPES and type(value) is not SAFE_SCALAR_TYPES[name]:
                result[name] = _hash(str(value))
            else:
                result[name] = _safe_value(value, known=True, home_directory=home_directory)
        else:
            result[_custom_key(name)] = _safe_value(
                value, known=False, home_directory=home_directory
            )
    return result


def sanitize_event_dict(
    value: Mapping[str, Any], *, home_directory: str | None = None
) -> dict[str, Any]:
    event = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    source = str(event.get("source", "runtime"))
    metadata = event.pop("metadata", event.get("safe_metadata_json", {}))
    event["safe_metadata_json"] = sanitize_metadata(
        metadata if isinstance(metadata, Mapping) else {},
        source=source,
        home_directory=home_directory,
    )
    for key in SENSITIVE_KEYS:
        event.pop(key, None)
    event["raw_input_stored"] = False
    return event
