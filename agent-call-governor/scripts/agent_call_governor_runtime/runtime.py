"""Application-owned pre-call, execution, and post-call governance wrappers."""

from __future__ import annotations

import asyncio
import time
import uuid
import warnings
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from .ledger import DuplicateCallIdError
from .models import (
    FAILURE_POLICIES,
    RUNTIME_MODES,
    CallEvent,
    CallHandle,
    CallProposal,
    RuntimeDecision,
)
from .policy import evaluate


T = TypeVar("T")


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
    ) -> None:
        if mode not in RUNTIME_MODES:
            raise ValueError(f"mode must be one of: {', '.join(sorted(RUNTIME_MODES))}")
        if failure_policy not in FAILURE_POLICIES:
            raise ValueError(
                f"failure_policy must be one of: {', '.join(sorted(FAILURE_POLICIES))}"
            )
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")
        self.ledger = ledger
        self.mode = mode
        self.failure_policy = failure_policy
        self.policy_evaluator = policy_evaluator
        self.warning_handler = warning_handler or self._default_warning
        self.source = source.strip()

    def begin(
        self,
        proposal: CallProposal,
        *,
        call_id: str | None = None,
        source: str | None = None,
    ) -> CallHandle:
        call_id = call_id or str(uuid.uuid4())
        if not isinstance(call_id, str) or not call_id.strip():
            raise ValueError("call_id must be a non-empty string")
        call_id = call_id.strip()
        event_source = (source or self.source).strip()
        atomic_transition = getattr(self.ledger, "atomic_transition", None)
        if callable(atomic_transition):
            decision = self._atomic_begin(
                proposal,
                call_id,
                event_source,
                atomic_transition,
            )
        else:
            decision = self._non_atomic_begin(proposal, call_id, event_source)

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
        atomic_transition: Callable[..., RuntimeDecision],
    ) -> RuntimeDecision:
        def operation(history: list[dict[str, str]]) -> tuple[RuntimeDecision, list[CallEvent]]:
            decision = self._decide(proposal, history=history)
            decision_metadata = dict(decision.context)
            events = [
                self._event(
                    proposal,
                    call_id,
                    decision,
                    "proposed",
                    source=event_source,
                    metadata=decision_metadata,
                )
            ]
            events.append(
                self._event(
                    proposal,
                    call_id,
                    decision,
                    "started" if decision.execution_allowed else "blocked",
                    source=event_source,
                    metadata=decision_metadata,
                )
            )
            return decision, events

        try:
            return atomic_transition(proposal.session_id, operation)
        except DuplicateCallIdError:
            raise
        except Exception as exc:
            if self.failure_policy == "fail-closed":
                raise GovernanceInternalError(
                    f"Agent Call Governor ledger failed during call reservation: {type(exc).__name__}"
                ) from exc
            return self._internal_decision(proposal, exc, fail_open=True)

    def _non_atomic_begin(
        self,
        proposal: CallProposal,
        call_id: str,
        event_source: str,
    ) -> RuntimeDecision:
        decision = self._decide(proposal)
        decision_metadata = dict(decision.context)

        self._record(
            self._event(
                proposal,
                call_id,
                decision,
                "proposed",
                source=event_source,
                metadata=decision_metadata,
            ),
            "proposal",
        )

        if not decision.execution_allowed:
            self._record(
                self._event(
                    proposal,
                    call_id,
                    decision,
                    "blocked",
                    source=event_source,
                    metadata=decision_metadata,
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
                source=event_source,
                metadata=decision_metadata,
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
    ) -> CallHandle:
        """Reserve a call without blocking the event loop or leaking on cancellation."""
        begin_task = asyncio.create_task(
            asyncio.to_thread(self.begin, proposal, call_id=call_id, source=source)
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
    ) -> RuntimeDecision:
        try:
            if history is None:
                history = self.ledger.history(proposal.session_id)
            raw = self.policy_evaluator(proposal.to_policy_document(history))
            if not isinstance(raw, Mapping):
                raise TypeError("policy result must be an object")
            policy_allowed = raw.get("allowed")
            if type(policy_allowed) is not bool:
                raise TypeError("policy result allowed must be a boolean")
            reason = raw.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise TypeError("policy result reason must be a non-empty string")
            reason = reason.strip()
            policy_fp = raw.get("fingerprint")
            if not isinstance(policy_fp, str) or policy_fp != proposal.fingerprint:
                raise ValueError("policy fingerprint must match the canonical proposal fingerprint")
            remaining = raw.get("remaining_after_call")
            if remaining is not None and (
                type(remaining) is not int or remaining < 0
            ):
                raise TypeError("policy result remaining_after_call must be a non-negative integer")
            context = {
                key: value
                for key, value in raw.items()
                if key not in {"allowed", "reason", "fingerprint", "remaining_after_call"}
            }
            execution_allowed = policy_allowed or self.mode in {"observe", "warn"}
            return RuntimeDecision(
                policy_allowed=policy_allowed,
                execution_allowed=execution_allowed,
                reason=reason,
                fingerprint=policy_fp,
                remaining_after_call=remaining if isinstance(remaining, int) else None,
                context=context,
            )
        except Exception as exc:
            fail_open = self.failure_policy == "fail-open"
            return self._internal_decision(proposal, exc, fail_open=fail_open)

    @staticmethod
    def _internal_decision(
        proposal: CallProposal,
        error: BaseException,
        *,
        fail_open: bool,
    ) -> RuntimeDecision:
        return RuntimeDecision(
            policy_allowed=False,
            execution_allowed=fail_open,
            reason="internal_error_fail_open" if fail_open else "internal_error_fail_closed",
            fingerprint=proposal.fingerprint,
            context={"internal_error_type": type(error).__name__},
        )

    def _event(
        self,
        proposal: CallProposal,
        call_id: str,
        decision: RuntimeDecision,
        phase: str,
        *,
        progress: str | None = None,
        duration_ms: float | None = None,
        source: str,
        metadata: Mapping[str, Any],
        error_type: str | None = None,
    ) -> CallEvent:
        combined_metadata = dict(proposal.metadata)
        combined_metadata.update(metadata)
        return CallEvent.create(
            session_id=proposal.session_id,
            call_id=call_id,
            parent_call_id=proposal.parent_call_id,
            phase=phase,
            objective=proposal.objective,
            route=proposal.route,
            fingerprint=decision.fingerprint,
            budget_kind=proposal.budget_kind,
            profile=proposal.profile,
            quality_risk=proposal.quality_risk,
            mode=self.mode,
            policy_allowed=decision.policy_allowed,
            execution_allowed=decision.execution_allowed,
            decision_reason=decision.reason,
            progress=progress,
            duration_ms=duration_ms,
            source=source,
            metadata=combined_metadata,
            error_type=error_type,
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
