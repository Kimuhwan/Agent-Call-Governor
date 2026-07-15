"""Application-owned pre-call, execution, and post-call governance wrappers."""

from __future__ import annotations

import asyncio
import time
import uuid
import warnings
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from .ledger import DuplicateCallIdError, StoredDecisionReplayError
from .models import (
    FAILURE_POLICIES,
    RUNTIME_MODES,
    CallEvent,
    CallHandle,
    CallProposal,
    RuntimeDecision,
    build_policy_facts,
)
from .policy import (
    POLICY_CONTEXT_FIELDS,
    POLICY_ALLOW_REASON_CODES,
    POLICY_REASON_CODES,
    POLICY_VERSION,
    PROFILE_LIMITS,
    PROFILE_NAMES,
    RISK_VALUES,
    evaluate,
    validate_policy_context,
)


T = TypeVar("T")

_EVENT_STATUS = {
    "call.proposed": "proposed",
    "policy.decided": "decided",
    "call.started": "running",
    "call.blocked": "blocked",
    "call.completed": "completed",
    "call.failed": "failed",
    "call.cancelled": "cancelled",
}


class GovernanceError(RuntimeError):
    """Base error for runtime governance failures."""


class GovernanceBlocked(GovernanceError):
    """Raised before execution when the active policy denies a call."""

    def __init__(self, decision: RuntimeDecision) -> None:
        self.decision = decision
        super().__init__(f"Agent call blocked: {decision.reason}")


class GovernanceInternalError(GovernanceError):
    """Raised when fail-closed cannot evaluate or persist governance state."""


class GovernedRuntime:
    """Evaluate a proposal, execute it when permitted, and record the outcome."""

    def __init__(
        self,
        ledger: Any,
        *,
        mode: str = "observe",
        failure_policy: str = "fail-open",
        policy_evaluator: Callable[[dict[str, Any]], dict[str, Any]] = evaluate,
        warning_handler: Callable[[str], None] | None = None,
        source: str = "runtime",
        default_profile: str = "balanced",
        default_risk: str = "medium",
    ) -> None:
        if mode not in RUNTIME_MODES:
            raise ValueError(f"mode must be one of: {', '.join(sorted(RUNTIME_MODES))}")
        if failure_policy not in FAILURE_POLICIES:
            raise ValueError(
                f"failure_policy must be one of: {', '.join(sorted(FAILURE_POLICIES))}"
            )
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")
        if not isinstance(default_profile, str) or default_profile not in PROFILE_NAMES:
            raise ValueError(
                f"default_profile must be one of: {', '.join(sorted(PROFILE_NAMES))}"
            )
        if not isinstance(default_risk, str) or default_risk not in RISK_VALUES:
            raise ValueError(
                f"default_risk must be one of: {', '.join(sorted(RISK_VALUES))}"
            )
        self.ledger = ledger
        self.mode = mode
        self.failure_policy = failure_policy
        self.policy_evaluator = policy_evaluator
        self.warning_handler = warning_handler or self._default_warning
        self.source = source.strip()
        self.default_profile = default_profile
        self.default_risk = default_risk

    def begin(
        self,
        proposal: CallProposal,
        *,
        call_id: str | None = None,
        source: str | None = None,
        source_event: str | None = None,
    ) -> CallHandle:
        call_id = call_id or str(uuid.uuid4())
        if not isinstance(call_id, str) or not call_id.strip():
            raise ValueError("call_id must be a non-empty string")
        call_id = call_id.strip()
        event_source = (source or self.source).strip()
        host_source_event = source_event or event_source
        if not isinstance(host_source_event, str) or not host_source_event.strip():
            raise ValueError("source_event must be a non-empty string")
        host_source_event = host_source_event.strip()
        atomic_transition = getattr(self.ledger, "atomic_transition", None)
        if callable(atomic_transition):
            decision = self._atomic_begin(
                proposal,
                call_id,
                event_source,
                host_source_event,
                atomic_transition,
            )
        else:
            decision = self._non_atomic_begin(
                proposal,
                call_id,
                event_source,
                host_source_event,
            )

        if not decision.execution_allowed:
            raise GovernanceBlocked(decision)

        if self.mode == "warn" and not decision.policy_allowed:
            try:
                self.warning_handler(
                    f"Agent Call Governor would block this call: {decision.reason} "
                    f"(fingerprint={decision.fingerprint})"
                )
            except Exception:
                pass
        return CallHandle(proposal=proposal, call_id=call_id, decision=decision)

    def _atomic_begin(
        self,
        proposal: CallProposal,
        call_id: str,
        event_source: str,
        host_source_event: str,
        atomic_transition: Callable[..., RuntimeDecision],
    ) -> RuntimeDecision:
        proposal_fingerprint = proposal.fingerprint
        policy_facts = build_policy_facts(proposal)

        def operation(history: list[dict[str, str]]) -> tuple[RuntimeDecision, list[CallEvent]]:
            decision = self._decide(
                proposal,
                history=history,
                proposal_fingerprint=proposal_fingerprint,
            )
            decision_metadata = dict(decision.context)
            events = [
                self._event(
                    proposal,
                    call_id,
                    decision,
                    "proposed",
                    event_type="call.proposed",
                    source=event_source,
                    source_event=host_source_event,
                    metadata={},
                    policy_facts=policy_facts,
                )
            ]
            events.append(
                self._event(
                    proposal,
                    call_id,
                    decision,
                    "proposed",
                    event_type="policy.decided",
                    source=event_source,
                    source_event=host_source_event,
                    metadata=decision_metadata,
                    include_proposal_metadata=False,
                )
            )
            events.append(
                self._event(
                    proposal,
                    call_id,
                    decision,
                    "started" if decision.execution_allowed else "blocked",
                    event_type=(
                        "call.started" if decision.execution_allowed else "call.blocked"
                    ),
                    source=event_source,
                    source_event=host_source_event,
                    metadata={},
                )
            )
            return decision, events

        try:
            return atomic_transition(
                proposal.session_id,
                call_id,
                proposal_fingerprint,
                operation,
            )
        except (DuplicateCallIdError, StoredDecisionReplayError):
            raise
        except Exception as exc:
            if self.failure_policy == "fail-closed":
                raise GovernanceInternalError(
                    f"Agent Call Governor ledger failed during call reservation: {type(exc).__name__}"
                ) from exc
            return self._internal_decision(proposal_fingerprint, exc, fail_open=True)

    def _non_atomic_begin(
        self,
        proposal: CallProposal,
        call_id: str,
        event_source: str,
        host_source_event: str,
    ) -> RuntimeDecision:
        decision = self._decide(proposal)
        policy_facts = build_policy_facts(proposal)
        decision_metadata = dict(decision.context)

        self._record(
            self._event(
                proposal,
                call_id,
                decision,
                "proposed",
                event_type="call.proposed",
                source=event_source,
                source_event=host_source_event,
                metadata={},
                policy_facts=policy_facts,
            ),
            "proposal",
        )

        self._record(
            self._event(
                proposal,
                call_id,
                decision,
                "proposed",
                event_type="policy.decided",
                source=event_source,
                source_event=host_source_event,
                metadata=decision_metadata,
                include_proposal_metadata=False,
            ),
            "policy decision",
        )

        if not decision.execution_allowed:
            self._record(
                self._event(
                    proposal,
                    call_id,
                    decision,
                    "blocked",
                    event_type="call.blocked",
                    source=event_source,
                    source_event=host_source_event,
                    metadata={},
                ),
                "blocked decision",
            )
            return decision

        self._record(
            self._event(
                proposal,
                call_id,
                decision,
                "started",
                event_type="call.started",
                source=event_source,
                source_event=host_source_event,
                metadata={},
            ),
            "call start",
        )
        return decision

    def complete(
        self,
        handle: CallHandle,
        *,
        progress: str = "material_progress",
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
    ) -> None:
        self._record(
            self._event(
                handle.proposal,
                handle.call_id,
                handle.decision,
                "completed",
                progress=progress,
                duration_ms=_duration_ms(handle),
                source=source or self.source,
                metadata=dict(metadata or {}),
            ),
            "call completion",
        )

    async def begin_async(
        self,
        proposal: CallProposal,
        *,
        call_id: str | None = None,
        source: str | None = None,
        source_event: str | None = None,
    ) -> CallHandle:
        """Reserve a call without blocking the event loop or leaking on cancellation."""
        begin_task = asyncio.create_task(
            asyncio.to_thread(
                self.begin,
                proposal,
                call_id=call_id,
                source=source,
                source_event=source_event,
            )
        )
        try:
            return await asyncio.shield(begin_task)
        except asyncio.CancelledError as cancellation:
            try:
                handle = await _await_ignoring_cancellations(begin_task)
            except Exception:
                pass
            else:
                try:
                    await _to_thread_resilient(
                        self.cancel,
                        handle,
                        metadata={"cancelled_before_execution": True},
                        source=source,
                    )
                except Exception as recording_error:
                    if hasattr(cancellation, "add_note"):
                        cancellation.add_note(str(recording_error))
            raise

    async def complete_async(
        self,
        handle: CallHandle,
        *,
        progress: str = "material_progress",
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
    ) -> None:
        await _to_thread_resilient(
            self.complete,
            handle,
            progress=progress,
            metadata=metadata,
            source=source,
        )

    def fail(
        self,
        handle: CallHandle,
        error: BaseException,
        *,
        progress: str = "low_progress",
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
    ) -> None:
        self._record(
            self._event(
                handle.proposal,
                handle.call_id,
                handle.decision,
                "failed",
                progress=progress,
                duration_ms=_duration_ms(handle),
                source=source or self.source,
                metadata=dict(metadata or {}),
                error_type=type(error).__name__,
            ),
            "call failure",
        )

    def cancel(
        self,
        handle: CallHandle,
        *,
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
    ) -> None:
        """Release a reservation when async cancellation wins before execution."""
        self._record(
            self._event(
                handle.proposal,
                handle.call_id,
                handle.decision,
                "cancelled",
                progress="no_progress",
                duration_ms=_duration_ms(handle),
                source=source or self.source,
                metadata=dict(metadata or {}),
                error_type="CancelledError",
            ),
            "pre-execution cancellation",
        )

    def run(
        self,
        proposal: CallProposal,
        func: Callable[..., T],
        *args: Any,
        progress: str = "material_progress",
        result_metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> T:
        handle = self.begin(proposal)
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:
            self._record_failure_without_masking(handle, exc)
            raise
        self.complete(handle, progress=progress, metadata=result_metadata)
        return result

    async def run_async(
        self,
        proposal: CallProposal,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        progress: str = "material_progress",
        result_metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> T:
        handle = await self.begin_async(proposal)
        try:
            result = await func(*args, **kwargs)
        except BaseException as exc:
            await _to_thread_resilient(self._record_failure_without_masking, handle, exc)
            raise
        await self.complete_async(
            handle,
            progress=progress,
            metadata=result_metadata,
        )
        return result

    def _decide(
        self,
        proposal: CallProposal,
        *,
        history: list[dict[str, str]] | None = None,
        proposal_fingerprint: str | None = None,
    ) -> RuntimeDecision:
        if proposal_fingerprint is None:
            proposal_fingerprint = proposal.fingerprint
        decision_latency_ms: float | None = None
        try:
            if history is None:
                history = self.ledger.history(proposal.session_id)
            policy_document = proposal.to_policy_document(history)
            evaluation_started_at = time.perf_counter_ns()
            try:
                raw = self.policy_evaluator(policy_document)
            finally:
                decision_latency_ms = max(
                    (time.perf_counter_ns() - evaluation_started_at) / 1_000_000,
                    0.0,
                )
            if not isinstance(raw, Mapping):
                raise TypeError("policy result must be an object")
            policy_allowed = raw.get("allowed")
            if type(policy_allowed) is not bool:
                raise TypeError("policy result allowed must be a boolean")
            reason = raw.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise TypeError("policy result reason must be a non-empty string")
            reason = reason.strip()
            if reason not in POLICY_REASON_CODES:
                raise ValueError("policy result reason must be a stable reason code")
            if policy_allowed != (reason in POLICY_ALLOW_REASON_CODES):
                raise ValueError("policy result allowed and reason are inconsistent")
            policy_fp = raw.get("fingerprint")
            if not isinstance(policy_fp, str) or policy_fp != proposal_fingerprint:
                raise ValueError("policy fingerprint must match the canonical proposal fingerprint")
            remaining = raw.get("remaining_after_call")
            if remaining is not None and (
                type(remaining) is not int or remaining < 0
            ):
                raise TypeError("policy result remaining_after_call must be a non-negative integer")
            raw_context = {
                key: value
                for key, value in raw.items()
                if key not in {"allowed", "reason", "fingerprint", "remaining_after_call"}
            }
            context = validate_policy_context(raw_context)
            _validate_context_for_proposal(proposal, history, context)
            effective_limit = context.get("effective_limit")
            matching_history_count = context.get("matching_history_count")
            budget_before = (
                max(effective_limit - matching_history_count, 0)
                if (
                    type(effective_limit) is int
                    and effective_limit >= 0
                    and type(matching_history_count) is int
                    and matching_history_count >= 0
                )
                else None
            )
            execution_allowed = policy_allowed or self.mode in {"observe", "warn"}
            budget_after = (
                max(budget_before - int(execution_allowed), 0)
                if budget_before is not None
                else remaining
            )
            return RuntimeDecision(
                policy_allowed=policy_allowed,
                execution_allowed=execution_allowed,
                reason=reason,
                fingerprint=policy_fp,
                remaining_after_call=(
                    budget_after if isinstance(budget_after, int) else None
                ),
                context=context,
                budget_before=budget_before,
                budget_after=budget_after if isinstance(budget_after, int) else None,
                decision_latency_ms=decision_latency_ms,
            )
        except Exception as exc:
            fail_open = self.failure_policy == "fail-open"
            return self._internal_decision(
                proposal_fingerprint,
                exc,
                fail_open=fail_open,
                decision_latency_ms=decision_latency_ms,
            )

    @staticmethod
    def _internal_decision(
        proposal_fingerprint: str,
        error: BaseException,
        *,
        fail_open: bool,
        decision_latency_ms: float | None = None,
    ) -> RuntimeDecision:
        return RuntimeDecision(
            policy_allowed=False,
            execution_allowed=fail_open,
            reason="internal_error_fail_open" if fail_open else "internal_error_fail_closed",
            fingerprint=proposal_fingerprint,
            context={},
            decision_latency_ms=decision_latency_ms,
        )

    def _event(
        self,
        proposal: CallProposal,
        call_id: str,
        decision: RuntimeDecision,
        phase: str,
        *,
        event_type: str | None = None,
        progress: str | None = None,
        duration_ms: float | None = None,
        source: str,
        source_event: str | None = None,
        metadata: Mapping[str, Any],
        error_type: str | None = None,
        policy_facts: Mapping[str, Any] | None = None,
        include_proposal_metadata: bool = True,
    ) -> CallEvent:
        event_type = event_type or f"call.{phase}"
        is_policy_decision = event_type == "policy.decided"
        carries_compatibility_decision = is_policy_decision or event_type in {
            "call.started",
            "call.blocked",
        }
        execution_latency_ms = (
            duration_ms
            if event_type in {"call.completed", "call.failed", "call.cancelled"}
            else None
        )
        combined_metadata = (
            {
                key: value
                for key, value in proposal.metadata.items()
                if key not in POLICY_CONTEXT_FIELDS
            }
            if include_proposal_metadata
            else {}
        )
        combined_metadata.update(
            metadata
            if is_policy_decision
            else {
                key: value
                for key, value in metadata.items()
                if key not in POLICY_CONTEXT_FIELDS
            }
        )
        return CallEvent.create(
            session_id=proposal.session_id,
            call_id=call_id,
            parent_call_id=proposal.parent_call_id,
            phase=phase,
            objective=proposal.fingerprint_result.objective_digest,
            route=proposal.route,
            fingerprint=decision.fingerprint,
            budget_kind=proposal.budget_kind,
            profile=proposal.profile,
            quality_risk=proposal.quality_risk,
            mode=self.mode,
            event_type=event_type,
            trace_id=proposal.trace_id,
            turn_id=proposal.turn_id,
            span_id=call_id,
            parent_span_id=proposal.parent_call_id,
            source_event=source_event or source,
            input_digest=proposal.fingerprint_result.input_digest,
            fingerprint_version=proposal.fingerprint_result.version,
            failure_policy=self.failure_policy,
            policy_allowed=(
                decision.policy_allowed if carries_compatibility_decision else None
            ),
            execution_allowed=(
                decision.execution_allowed if carries_compatibility_decision else None
            ),
            decision_reason=(
                decision.reason if carries_compatibility_decision else None
            ),
            decision=_decision_value(decision, self.mode) if is_policy_decision else None,
            reason_code=decision.reason if is_policy_decision else None,
            policy_version=POLICY_VERSION if is_policy_decision else None,
            budget_before=decision.budget_before if is_policy_decision else None,
            budget_after=decision.budget_after if is_policy_decision else None,
            decision_latency_ms=(
                decision.decision_latency_ms if is_policy_decision else None
            ),
            execution_latency_ms=execution_latency_ms,
            status=_EVENT_STATUS[event_type],
            progress=progress,
            duration_ms=duration_ms,
            source=source,
            metadata=combined_metadata,
            error_type=error_type,
            policy_facts=policy_facts,
        )

    def _record(self, event: CallEvent, operation: str) -> None:
        try:
            self.ledger.append(event)
        except Exception as exc:
            if self.failure_policy == "fail-closed":
                raise GovernanceInternalError(
                    f"Agent Call Governor ledger failed during {operation}: {type(exc).__name__}"
                ) from exc

    def _record_failure_without_masking(self, handle: CallHandle, error: BaseException) -> None:
        try:
            self.fail(handle, error)
        except GovernanceInternalError as recording_error:
            if hasattr(error, "add_note"):
                error.add_note(str(recording_error))

    @staticmethod
    def _default_warning(message: str) -> None:
        warnings.warn(message, RuntimeWarning, stacklevel=3)


def _decision_value(decision: RuntimeDecision, mode: str) -> str:
    if decision.reason.startswith("internal_error_"):
        return "internal_error"
    if decision.policy_allowed:
        return "allow"
    if mode in {"observe", "warn"}:
        return "would_block"
    return "block"


def _validate_context_for_proposal(
    proposal: CallProposal,
    history: list[dict[str, str]],
    context: Mapping[str, Any],
) -> None:
    profile_floor = PROFILE_LIMITS[proposal.profile][proposal.budget_kind][
        proposal.quality_risk
    ]
    requested_limit = (
        proposal.budget_limit
        if proposal.budget_limit is not None
        else profile_floor
    )
    effective_limit = max(requested_limit, profile_floor)
    matching_history_count = sum(
        1
        for entry in history
        if entry.get("budget_kind", proposal.budget_kind) == proposal.budget_kind
    )
    expected = {
        "profile": proposal.profile,
        "quality_risk": proposal.quality_risk,
        "budget_kind": proposal.budget_kind,
        "effective_limit": effective_limit,
        "budget_floor_applied": effective_limit != requested_limit,
        "matching_history_count": matching_history_count,
        "mandatory_reason": proposal.mandatory_reason,
    }
    if dict(context) != expected:
        raise ValueError("policy result context is inconsistent with the proposal")


def _duration_ms(handle: CallHandle) -> float:
    return max((time.perf_counter_ns() - handle.started_at_ns) / 1_000_000, 0.0)


async def _to_thread_resilient(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Finish authoritative recording even if the awaiting task is cancelled."""
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancellation:
        try:
            await _await_ignoring_cancellations(task)
        except Exception as recording_error:
            if hasattr(cancellation, "add_note"):
                cancellation.add_note(str(recording_error))
        raise


async def _await_ignoring_cancellations(task: asyncio.Task[T]) -> T:
    """Wait for a protected task through any number of outer cancellations."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()
