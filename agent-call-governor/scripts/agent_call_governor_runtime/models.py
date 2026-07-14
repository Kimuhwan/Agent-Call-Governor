"""Validated public data models for governed calls and lifecycle records."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .policy import (
    BUDGET_KINDS,
    MANDATORY_REASONS,
    PROFILE_NAMES,
    PROGRESS_VALUES,
    RISK_VALUES,
    fingerprint as policy_fingerprint,
)


EVENT_PHASES = {"proposed", "blocked", "started", "completed", "failed"}
RUNTIME_MODES = {"observe", "warn", "enforce"}
FAILURE_POLICIES = {"fail-open", "fail-closed"}


def _nonempty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _choice(value: str, allowed: set[str], field_name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{field_name} must be one of: {', '.join(sorted(allowed))}")
    return value


def _json_object(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    try:
        encoded = json.dumps(dict(value), ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-compatible") from exc
    if not isinstance(decoded, dict):  # defensive; Mapping always encodes as an object
        raise ValueError(f"{field_name} must be an object")
    return decoded


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

    @property
    def fingerprint(self) -> str:
        return policy_fingerprint(self.to_policy_proposal())

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
    policy_allowed: bool | None = None
    execution_allowed: bool | None = None
    decision_reason: str | None = None
    progress: str | None = None
    parent_call_id: str | None = None
    duration_ms: float | None = None
    source: str = "runtime"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    schema_version: int = 1

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
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))
        object.__setattr__(self, "phase", _choice(self.phase, EVENT_PHASES, "phase"))
        object.__setattr__(self, "budget_kind", _choice(self.budget_kind, BUDGET_KINDS, "budget_kind"))
        object.__setattr__(self, "profile", _choice(self.profile, PROFILE_NAMES, "profile"))
        object.__setattr__(self, "quality_risk", _choice(self.quality_risk, RISK_VALUES, "quality_risk"))
        object.__setattr__(self, "mode", _choice(self.mode, RUNTIME_MODES, "mode"))
        if self.progress is not None:
            object.__setattr__(self, "progress", _choice(self.progress, PROGRESS_VALUES, "progress"))
        if self.parent_call_id is not None:
            object.__setattr__(self, "parent_call_id", _nonempty(self.parent_call_id, "parent_call_id"))
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("schema_version must be a positive integer")
        object.__setattr__(self, "metadata", _json_object(self.metadata, "metadata"))

    @classmethod
    def create(cls, **values: Any) -> "CallEvent":
        return cls(
            event_id=values.pop("event_id", str(uuid.uuid4())),
            occurred_at=values.pop("occurred_at", utc_now()),
            **values,
        )

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
            "quality_risk": self.quality_risk,
            "mode": self.mode,
            "policy_allowed": self.policy_allowed,
            "execution_allowed": self.execution_allowed,
            "decision_reason": self.decision_reason,
            "progress": self.progress,
            "duration_ms": self.duration_ms,
            "source": self.source,
            "metadata": dict(self.metadata),
            "error_type": self.error_type,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CallEvent":
        return cls(**dict(value))
