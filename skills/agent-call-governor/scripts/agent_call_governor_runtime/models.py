"""Validated public data models for governed calls and lifecycle records."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .fingerprint import FINGERPRINT_VERSION, FingerprintResult, build_fingerprint
from .policy import (
    BUDGET_KINDS,
    MANDATORY_REASONS,
    PROFILE_NAMES,
    PROGRESS_VALUES,
    RISK_VALUES,
)
from .redaction import sanitize_metadata


EVENT_PHASES = {"proposed", "blocked", "started", "completed", "failed", "cancelled"}
EVENT_TYPES = {
    "session.started",
    "call.proposed",
    "policy.decided",
    "call.started",
    "call.blocked",
    "call.completed",
    "call.failed",
    "call.cancelled",
    "progress.observed",
    "session.stopped",
}
LEGACY_EVENT_TYPES = {
    "proposed": "call.proposed",
    "blocked": "call.blocked",
    "started": "call.started",
    "completed": "call.completed",
    "failed": "call.failed",
    "cancelled": "call.cancelled",
}
RUNTIME_MODES = {"observe", "warn", "enforce"}
FAILURE_POLICIES = {"fail-open", "fail-closed"}
EVENT_FAILURE_POLICIES = FAILURE_POLICIES | {"legacy-unknown"}
POLICY_FACTS_VERSION = 1
_POLICY_FACT_FIELDS = frozenset({
    "fingerprint",
    "fingerprint_version",
    "budget_kind",
    "requested_budget_limit",
    "profile",
    "risk",
    "mandatory_reason",
    "changed_strategy_present",
    "required_fields_present",
    "duplicate_scope",
    "state_token_digest",
    "policy_facts_version",
})


def _nonempty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _choice(value: Any, allowed: set[str], field_name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field_name} must be one of: {', '.join(sorted(allowed))}")
    return value


def _objective_reference(value: str) -> str:
    """Return a stable non-plaintext reference for an event objective."""
    objective = _nonempty(value, "objective")
    if (
        objective.startswith("sha256:")
        and len(objective) == 71
        and all(character in "0123456789abcdef" for character in objective[7:])
    ):
        return objective
    digest = hashlib.sha256(objective.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _json_object(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        encoded.encode("utf-8")
        decoded = json.loads(encoded)
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must contain only UTF-8-encodable text") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-compatible") from exc
    if not isinstance(decoded, dict):  # defensive; Mapping always encodes as an object
        raise ValueError(f"{field_name} must be an object")
    return decoded


def _is_sha256_reference(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _validate_policy_facts(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != _POLICY_FACT_FIELDS:
        raise ValueError("policy_facts must contain exactly the approved privacy-safe fields")
    if not _is_sha256_reference(value["fingerprint"]):
        raise ValueError("policy_facts.fingerprint must be a SHA-256 reference")
    if (
        not isinstance(value["fingerprint_version"], int)
        or isinstance(value["fingerprint_version"], bool)
        or value["fingerprint_version"] != FINGERPRINT_VERSION
    ):
        raise ValueError(
            f"policy_facts.fingerprint_version must be {FINGERPRINT_VERSION}"
        )
    _choice(value["budget_kind"], BUDGET_KINDS, "policy_facts.budget_kind")
    requested_limit = value["requested_budget_limit"]
    if requested_limit is not None and (
        not isinstance(requested_limit, int)
        or isinstance(requested_limit, bool)
        or requested_limit < 0
    ):
        raise ValueError(
            "policy_facts.requested_budget_limit must be null or a non-negative integer"
        )
    _choice(value["profile"], PROFILE_NAMES, "policy_facts.profile")
    _choice(value["risk"], RISK_VALUES, "policy_facts.risk")
    mandatory_reason = value["mandatory_reason"]
    if mandatory_reason is not None:
        _choice(mandatory_reason, MANDATORY_REASONS, "policy_facts.mandatory_reason")
    for field_name in ("changed_strategy_present", "required_fields_present"):
        if type(value[field_name]) is not bool:
            raise ValueError(f"policy_facts.{field_name} must be a boolean")
    if value["duplicate_scope"] != "turn":
        raise ValueError("policy_facts.duplicate_scope must be turn")
    state_token_digest = value["state_token_digest"]
    if state_token_digest is not None and not _is_sha256_reference(state_token_digest):
        raise ValueError(
            "policy_facts.state_token_digest must be null or a SHA-256 reference"
        )
    if (
        not isinstance(value["policy_facts_version"], int)
        or isinstance(value["policy_facts_version"], bool)
        or value["policy_facts_version"] != POLICY_FACTS_VERSION
    ):
        raise ValueError(
            f"policy_facts.policy_facts_version must be {POLICY_FACTS_VERSION}"
        )
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class CallProposal:
    session_id: str
    objective: str
    route: str
    capability_gap: str
    expected_new_information: str
    stop_condition: str
    material_inputs: Mapping[str, Any] = field(default_factory=dict, repr=False)
    budget_kind: str = "agent"
    profile: str = "balanced"
    quality_risk: str = "medium"
    budget_limit: int | None = None
    mandatory_reason: str | None = None
    changed_strategy: str | None = None
    parent_call_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)
    _fingerprint_result: FingerprintResult = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in (
            "session_id",
            "objective",
            "route",
            "capability_gap",
            "expected_new_information",
            "stop_condition",
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))
        object.__setattr__(self, "budget_kind", _choice(self.budget_kind, BUDGET_KINDS, "budget_kind"))
        object.__setattr__(self, "profile", _choice(self.profile, PROFILE_NAMES, "profile"))
        object.__setattr__(self, "quality_risk", _choice(self.quality_risk, RISK_VALUES, "quality_risk"))
        if self.budget_limit is not None and (
            not isinstance(self.budget_limit, int) or isinstance(self.budget_limit, bool) or self.budget_limit < 0
        ):
            raise ValueError("budget_limit must be a non-negative integer")
        if self.mandatory_reason is not None:
            object.__setattr__(
                self,
                "mandatory_reason",
                _choice(self.mandatory_reason, MANDATORY_REASONS, "mandatory_reason"),
            )
        if self.changed_strategy is not None:
            object.__setattr__(self, "changed_strategy", _nonempty(self.changed_strategy, "changed_strategy"))
        if self.parent_call_id is not None:
            object.__setattr__(self, "parent_call_id", _nonempty(self.parent_call_id, "parent_call_id"))
        object.__setattr__(self, "material_inputs", _json_object(self.material_inputs, "material_inputs"))
        object.__setattr__(self, "metadata", _json_object(self.metadata, "metadata"))
        object.__setattr__(
            self,
            "_fingerprint_result",
            build_fingerprint(
                objective=self.objective,
                route=self.route,
                material_inputs=self.material_inputs,
                cwd=self.metadata.get("cwd"),
                tool_version=self.metadata.get("tool_version"),
                state_token=self.metadata.get("state_token"),
            ),
        )

    @property
    def fingerprint(self) -> str:
        return self.fingerprint_result.digest

    @property
    def fingerprint_result(self) -> FingerprintResult:
        return self._fingerprint_result

    def to_policy_proposal(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "objective": self.objective,
            "route": self.route,
            "material_inputs": dict(self.material_inputs),
            "capability_gap": self.capability_gap,
            "expected_new_information": self.expected_new_information,
            "stop_condition": self.stop_condition,
        }
        if self.mandatory_reason is not None:
            value["mandatory_reason"] = self.mandatory_reason
        if self.changed_strategy is not None:
            value["changed_strategy"] = self.changed_strategy
        fingerprint_metadata = {
            key: self.metadata[key]
            for key in ("cwd", "tool_version", "state_token")
            if key in self.metadata
        }
        if fingerprint_metadata:
            value["metadata"] = fingerprint_metadata
        return value

    def to_policy_document(self, history: Iterable[dict[str, Any]]) -> dict[str, Any]:
        budget: dict[str, Any] = {"kind": self.budget_kind}
        if self.budget_limit is not None:
            budget["limit"] = self.budget_limit
        return {
            "proposal": self.to_policy_proposal(),
            "profile": self.profile,
            "quality_risk": self.quality_risk,
            "budget": budget,
            "history": list(history),
        }


@dataclass(frozen=True)
class RuntimeDecision:
    policy_allowed: bool
    execution_allowed: bool
    reason: str
    fingerprint: str
    remaining_after_call: int | None = None
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CallHandle:
    proposal: CallProposal
    call_id: str
    decision: RuntimeDecision
    started_at_ns: int = field(default_factory=time.perf_counter_ns)


@dataclass(frozen=True)
class CallEvent:
    event_id: str
    session_id: str
    call_id: str
    phase: str
    occurred_at: str
    objective: str
    route: str
    fingerprint: str
    budget_kind: str
    profile: str
    quality_risk: str
    mode: str
    event_type: str
    observed_at: str
    trace_id: str
    turn_id: str | None
    span_id: str
    parent_span_id: str | None
    source_event: str
    policy_allowed: bool | None = None
    execution_allowed: bool | None = None
    decision_reason: str | None = None
    progress: str | None = None
    parent_call_id: str | None = None
    duration_ms: float | None = None
    source: str = "runtime"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    agent_id: str | None = None
    tool_name: str | None = None
    input_digest: str | None = None
    fingerprint_version: int | None = None
    failure_policy: str = "fail-open"
    decision: str | None = None
    reason_code: str | None = None
    policy_version: str | None = None
    budget_before: int | None = None
    budget_after: int | None = None
    decision_latency_ms: float | None = None
    execution_latency_ms: float | None = None
    status: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    pricing_version: str | None = None
    raw_input_stored: bool = False
    policy_facts: Mapping[str, Any] | None = None
    schema_version: int = 2

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "session_id",
            "call_id",
            "occurred_at",
            "objective",
            "route",
            "fingerprint",
            "source",
            "event_type",
            "observed_at",
            "trace_id",
            "span_id",
            "source_event",
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))
        object.__setattr__(self, "objective", _objective_reference(self.objective))
        object.__setattr__(self, "phase", _choice(self.phase, EVENT_PHASES, "phase"))
        object.__setattr__(self, "event_type", _choice(self.event_type, EVENT_TYPES, "event_type"))
        object.__setattr__(self, "budget_kind", _choice(self.budget_kind, BUDGET_KINDS, "budget_kind"))
        object.__setattr__(self, "profile", _choice(self.profile, PROFILE_NAMES, "profile"))
        object.__setattr__(self, "quality_risk", _choice(self.quality_risk, RISK_VALUES, "quality_risk"))
        object.__setattr__(self, "mode", _choice(self.mode, RUNTIME_MODES, "mode"))
        object.__setattr__(
            self,
            "failure_policy",
            _choice(self.failure_policy, EVENT_FAILURE_POLICIES, "failure_policy"),
        )
        if self.progress is not None:
            object.__setattr__(self, "progress", _choice(self.progress, PROGRESS_VALUES, "progress"))
        for name in (
            "parent_call_id",
            "turn_id",
            "parent_span_id",
            "agent_id",
            "tool_name",
            "input_digest",
            "decision",
            "reason_code",
            "policy_version",
            "status",
            "pricing_version",
            "error_type",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _nonempty(value, name))
        for name in ("budget_before", "budget_after", "prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        for name in (
            "duration_ms",
            "decision_latency_ms",
            "execution_latency_ms",
            "estimated_cost_usd",
        ):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative finite number")
        if self.fingerprint_version is not None and (
            not isinstance(self.fingerprint_version, int)
            or isinstance(self.fingerprint_version, bool)
            or self.fingerprint_version not in {1, FINGERPRINT_VERSION}
        ):
            raise ValueError(
                f"fingerprint_version must be null, 1, or {FINGERPRINT_VERSION}"
            )
        if self.raw_input_stored is not False:
            raise ValueError("raw_input_stored must be false")
        if self.estimated_cost_usd is not None and self.pricing_version is None:
            raise ValueError("pricing_version is required when estimated_cost_usd is set")
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")
        object.__setattr__(
            self,
            "metadata",
            sanitize_metadata(
                _json_object(self.metadata, "metadata"),
                source=self.source,
            ),
        )
        if self.policy_facts is not None:
            object.__setattr__(
                self,
                "policy_facts",
                _validate_policy_facts(_json_object(self.policy_facts, "policy_facts")),
            )

    @classmethod
    def create(cls, **values: Any) -> "CallEvent":
        resolved = dict(values)
        resolved["event_id"] = resolved.get("event_id", str(uuid.uuid4()))
        resolved["occurred_at"] = resolved.get("occurred_at", utc_now())
        phase = resolved.get("phase")
        resolved.setdefault("event_type", LEGACY_EVENT_TYPES.get(phase, f"call.{phase}"))
        resolved.setdefault("observed_at", resolved["occurred_at"])
        resolved.setdefault("trace_id", resolved.get("session_id"))
        resolved.setdefault("turn_id", None)
        resolved.setdefault("span_id", resolved.get("call_id"))
        resolved.setdefault("parent_span_id", resolved.get("parent_call_id"))
        resolved.setdefault("source_event", resolved.get("source", "runtime"))
        return cls(**resolved)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "call_id": self.call_id,
            "parent_call_id": self.parent_call_id,
            "phase": self.phase,
            "occurred_at": self.occurred_at,
            "objective": self.objective,
            "route": self.route,
            "fingerprint": self.fingerprint,
            "budget_kind": self.budget_kind,
            "profile": self.profile,
            "risk": self.quality_risk,
            "mode": self.mode,
            "event_type": self.event_type,
            "observed_at": self.observed_at,
            "trace_id": self.trace_id,
            "turn_id": self.turn_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "source_event": self.source_event,
            "policy_allowed": self.policy_allowed,
            "execution_allowed": self.execution_allowed,
            "decision_reason": self.decision_reason,
            "progress": self.progress,
            "duration_ms": self.duration_ms,
            "source": self.source,
            "safe_metadata_json": dict(self.metadata),
            "error_type": self.error_type,
            "agent_id": self.agent_id,
            "tool_name": self.tool_name,
            "input_digest": self.input_digest,
            "fingerprint_version": self.fingerprint_version,
            "failure_policy": self.failure_policy,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "budget_before": self.budget_before,
            "budget_after": self.budget_after,
            "decision_latency_ms": self.decision_latency_ms,
            "execution_latency_ms": self.execution_latency_ms,
            "status": self.status,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "pricing_version": self.pricing_version,
            "raw_input_stored": self.raw_input_stored,
            "policy_facts_json": (
                None if self.policy_facts is None else dict(self.policy_facts)
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CallEvent":
        resolved = dict(value)
        if "risk" in resolved:
            risk = resolved.pop("risk")
            if "quality_risk" in resolved and resolved["quality_risk"] != risk:
                raise ValueError("risk and quality_risk must match when both are provided")
            resolved.setdefault("quality_risk", risk)
        if "safe_metadata_json" in resolved:
            resolved.setdefault("metadata", resolved.pop("safe_metadata_json"))
        if "policy_facts_json" in resolved:
            resolved.setdefault("policy_facts", resolved.pop("policy_facts_json"))
        return cls.create(**resolved)
