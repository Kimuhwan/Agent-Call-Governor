from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
import warnings
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from agent_call_governor_runtime import (
    CallLedger,
    CallProposal,
    GovernedRuntime,
    RuntimeDecision,
    build_policy_facts,
    evaluate,
)
from agent_call_governor_runtime.ledger import (
    DuplicateCallIdError,
    StoredDecisionReplayError,
)
from agent_call_governor_runtime.cli import export_jsonl
from agent_call_governor_runtime.runtime import GovernanceBlocked


def proposal() -> CallProposal:
    return CallProposal(
        session_id="sha256:" + "1" * 64,
        objective="private objective",
        route="Bash",
        capability_gap="private gap",
        expected_new_information="private expectation",
        stop_condition="private stop condition",
        material_inputs={"command": "git status"},
        budget_kind="direct-tool",
        budget_limit=3,
        profile="balanced",
        quality_risk="medium",
        metadata={"cwd": "C:/repo"},
    )


def policy_context() -> dict[str, object]:
    return {
        "profile": "balanced",
        "quality_risk": "medium",
        "budget_kind": "direct-tool",
        "effective_limit": 6,
        "budget_floor_applied": True,
        "matching_history_count": 0,
        "mandatory_reason": None,
    }


def canonical_atomic_events(
    runtime: GovernedRuntime,
    candidate: CallProposal,
    call_id: str,
    decision: RuntimeDecision,
):
    return [
        runtime._event(
            candidate,
            call_id,
            decision,
            "proposed",
            event_type="call.proposed",
            source="runtime",
            metadata={},
            policy_facts=build_policy_facts(candidate),
        ),
        runtime._event(
            candidate,
            call_id,
            decision,
            "proposed",
            event_type="policy.decided",
            source="runtime",
            metadata=decision.context,
            include_proposal_metadata=False,
        ),
        runtime._event(
            candidate,
            call_id,
            decision,
            "started",
            event_type="call.started",
            source="runtime",
            metadata={},
        ),
    ]


class EventV2RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger = CallLedger(Path(self.tempdir.name) / "events.sqlite3")
        self.runtime = GovernedRuntime(self.ledger, mode="observe")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_begin_is_one_atomic_three_event_sequence(self) -> None:
        handle = self.runtime.begin(proposal(), call_id="host-call-ref")
        events = self.ledger.events(proposal().session_id)
        self.assertEqual(
            [event.event_type for event in events],
            ["call.proposed", "policy.decided", "call.started"],
        )
        self.assertEqual(events[0].policy_facts["fingerprint_version"], 2)
        self.assertEqual(events[1].policy_version, "2026-07-14.1")
        self.assertIsNotNone(events[1].decision_latency_ms)
        self.assertEqual(events[1].budget_before, 6)
        self.assertEqual(events[1].budget_after, 5)
        self.assertEqual(handle.decision.remaining_after_call, 5)

    def test_budget_fields_follow_effective_quality_floor(self) -> None:
        candidate = replace(
            proposal(),
            budget_kind="agent",
            budget_limit=0,
            profile="strict",
            quality_risk="high",
        )

        handle = self.runtime.begin(candidate, call_id="quality-floor-call")
        decision_event = self.ledger.events(candidate.session_id)[1]

        self.assertTrue(handle.decision.policy_allowed)
        self.assertEqual(decision_event.budget_before, 2)
        self.assertEqual(decision_event.budget_after, 1)
        self.assertEqual(handle.decision.remaining_after_call, 1)

    def test_same_call_id_and_fingerprint_returns_persisted_decision(self) -> None:
        first = self.runtime.begin(proposal(), call_id="host-call-ref")
        second = self.runtime.begin(proposal(), call_id="host-call-ref")
        expected_context = policy_context()
        self.assertEqual(first.decision, second.decision)
        self.assertEqual(first.decision.context, expected_context)
        self.assertEqual(second.decision.context, expected_context)
        self.assertEqual(len(self.ledger.events(proposal().session_id)), 3)
        events = self.ledger.events(proposal().session_id)
        self.assertEqual(events[1].metadata, expected_context)
        self.assertTrue(set(expected_context).isdisjoint(events[0].metadata))
        self.assertTrue(set(expected_context).isdisjoint(events[2].metadata))

    def test_same_call_id_with_different_fingerprint_is_rejected(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        changed = replace(proposal(), material_inputs={"command": "git diff"})
        with self.assertRaises(DuplicateCallIdError):
            self.runtime.begin(changed, call_id="host-call-ref")

    def test_same_call_id_with_legacy_fingerprint_epoch_is_not_v2_replay(self) -> None:
        candidate = proposal()
        legacy = self.runtime._event(
            candidate,
            "host-call-ref",
            RuntimeDecision(
                True,
                True,
                "allowed",
                candidate.fingerprint,
                2,
            ),
            "started",
            event_type="call.started",
            source="runtime",
            metadata={},
        )
        object.__setattr__(legacy, "fingerprint_version", 1)
        self.ledger.append(legacy)

        with self.assertRaises(DuplicateCallIdError):
            self.runtime.begin(candidate, call_id="host-call-ref")

    def test_policy_facts_and_database_exclude_raw_proposal_text(self) -> None:
        database_path = Path(self.tempdir.name) / "privacy.sqlite3"
        mirror_path = Path(self.tempdir.name) / "privacy.jsonl"
        export_path = Path(self.tempdir.name) / "privacy-export.jsonl"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ledger = CallLedger(database_path, mirror_path)
        runtime = GovernedRuntime(ledger, mode="observe")
        candidate = replace(
            proposal(),
            material_inputs={"command": "private material input"},
            metadata={
                "cwd": "C:/private-policy-repo",
                "state_token": "private state token",
            },
        )
        with closing(sqlite3.connect(database_path)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM call_events").fetchone()
            runtime.begin(candidate, call_id="host-call-ref")

            wal_path = Path(str(database_path) + "-wal")
            shm_path = Path(str(database_path) + "-shm")
            self.assertTrue(wal_path.exists(), "privacy probe must exercise SQLite WAL")
            self.assertTrue(shm_path.exists(), "privacy probe must exercise SQLite SHM")

            events = ledger.events()
            export_jsonl(events, export_path, database_path)
            encoded = json.dumps(
                [event.to_dict() for event in events],
                sort_keys=True,
            )
            raw_boundaries = b"".join(
                path.read_bytes()
                for path in (
                    database_path,
                    wal_path,
                    shm_path,
                    mirror_path,
                    export_path,
                )
            ).decode("utf-8", errors="ignore")
            for canary in (
                "private objective",
                "private gap",
                "private expectation",
                "private stop condition",
                "private material input",
                "C:/private-policy-repo",
                "private state token",
            ):
                self.assertNotIn(canary, encoded)
                self.assertNotIn(canary, raw_boundaries)

    def test_untrusted_evaluator_reason_never_crosses_event_boundaries(self) -> None:
        for label in ("reason", "private_context"):
            with self.subTest(label=label):
                canary = f"PRIVATE-{label.upper()}-CANARY-must-not-survive"
                database_path = Path(self.tempdir.name) / f"{label}.sqlite3"
                mirror_path = Path(self.tempdir.name) / f"{label}.jsonl"
                export_path = Path(self.tempdir.name) / f"{label}-export.jsonl"
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    ledger = CallLedger(database_path, mirror_path)

                evaluator_calls = 0

                def unsafe_evaluator(document):
                    nonlocal evaluator_calls
                    evaluator_calls += 1
                    result = evaluate(document)
                    result[label] = canary
                    return result

                runtime = GovernedRuntime(
                    ledger,
                    mode="observe",
                    failure_policy="fail-open",
                    policy_evaluator=unsafe_evaluator,
                )
                with closing(sqlite3.connect(database_path)) as reader:
                    reader.execute("BEGIN")
                    reader.execute("SELECT COUNT(*) FROM call_events").fetchone()
                    first = runtime.begin(proposal(), call_id=f"unsafe-{label}")
                    second = runtime.begin(proposal(), call_id=f"unsafe-{label}")

                    wal_path = Path(str(database_path) + "-wal")
                    shm_path = Path(str(database_path) + "-shm")
                    self.assertTrue(wal_path.exists())
                    self.assertTrue(shm_path.exists())

                    events = ledger.events()
                    export_jsonl(events, export_path, database_path)
                    self.assertEqual(evaluator_calls, 1)
                    self.assertEqual(first.decision.reason, "internal_error_fail_open")
                    self.assertEqual(first.decision.context, {})
                    self.assertEqual(first.decision, second.decision)
                    self.assertEqual(events[1].decision, "internal_error")
                    encoded = json.dumps(
                        [event.to_dict() for event in events],
                        sort_keys=True,
                    )
                    raw_boundaries = b"".join(
                        path.read_bytes()
                        for path in (
                            database_path,
                            wal_path,
                            shm_path,
                            mirror_path,
                            export_path,
                        )
                    ).decode("utf-8", errors="ignore")
                    self.assertNotIn(canary, encoded)
                    self.assertNotIn(canary, raw_boundaries)

    def test_inconsistent_canonical_context_becomes_persisted_internal_error(self) -> None:
        def inconsistent_evaluator(document):
            result = evaluate(document)
            result["profile"] = "strict"
            return result

        runtime = GovernedRuntime(
            self.ledger,
            mode="observe",
            failure_policy="fail-open",
            policy_evaluator=inconsistent_evaluator,
        )

        handle = runtime.begin(proposal(), call_id="inconsistent-context")
        events = self.ledger.events(proposal().session_id)

        self.assertEqual(handle.decision.reason, "internal_error_fail_open")
        self.assertEqual(handle.decision.context, {})
        self.assertEqual(
            [event.event_type for event in events],
            ["call.proposed", "policy.decided", "call.started"],
        )
        self.assertEqual(events[1].decision, "internal_error")
        self.assertEqual(events[1].metadata, {})

    def test_inconsistent_allowed_reason_pair_becomes_internal_error(self) -> None:
        def inconsistent_evaluator(document):
            result = evaluate(document)
            result["reason"] = "budget_exhausted"
            return result

        runtime = GovernedRuntime(
            self.ledger,
            mode="observe",
            failure_policy="fail-open",
            policy_evaluator=inconsistent_evaluator,
        )

        handle = runtime.begin(proposal(), call_id="inconsistent-reason")
        events = self.ledger.events(proposal().session_id)

        self.assertEqual(handle.decision.reason, "internal_error_fail_open")
        self.assertEqual(events[1].decision, "internal_error")

    def test_policy_facts_are_exact_and_trace_references_are_copied(self) -> None:
        trace_id = "sha256:" + "2" * 64
        turn_id = "sha256:" + "3" * 64
        candidate = replace(
            proposal(),
            trace_id=trace_id,
            turn_id=turn_id,
            metadata={"cwd": "C:/private-repo", "state_token": "private-state"},
        )

        facts = build_policy_facts(candidate)
        self.assertEqual(
            set(facts),
            {
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
            },
        )
        self.runtime.begin(candidate, call_id="trace-call")
        events = self.ledger.events(candidate.session_id)
        self.assertTrue(all(event.trace_id == trace_id for event in events))
        self.assertTrue(all(event.turn_id == turn_id for event in events))
        self.assertEqual(events[0].policy_facts, facts)
        self.assertIsNone(events[1].policy_facts)
        self.assertIsNone(events[2].policy_facts)

    def test_runtime_decision_alias_preserves_legacy_positional_shape(self) -> None:
        fingerprint = proposal().fingerprint
        legacy = RuntimeDecision(
            True,
            True,
            "allowed",
            fingerprint,
            2,
            {"profile": "balanced"},
        )
        self.assertEqual(legacy.remaining_after_call, 2)
        self.assertEqual(legacy.budget_after, 2)

        released_v1 = RuntimeDecision(
            True,
            True,
            "allowed",
            "f" * 64,
            1,
            {"profile": "balanced"},
        )
        self.assertEqual(released_v1.fingerprint, "f" * 64)
        self.assertEqual(released_v1.budget_after, 1)

        canonical = RuntimeDecision(
            policy_allowed=True,
            execution_allowed=True,
            reason="allowed",
            fingerprint=fingerprint,
            budget_after=1,
        )
        self.assertEqual(canonical.remaining_after_call, 1)
        with self.assertRaisesRegex(ValueError, "must match"):
            RuntimeDecision(
                True,
                True,
                "allowed",
                fingerprint,
                2,
                {},
                budget_after=1,
            )

    def test_replay_after_terminal_never_reevaluates_or_remirrors(self) -> None:
        calls = 0

        def counted_evaluator(document):
            nonlocal calls
            calls += 1
            return evaluate(document)

        jsonl_path = Path(self.tempdir.name) / "events.jsonl"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ledger = CallLedger(
                Path(self.tempdir.name) / "replay.sqlite3",
                jsonl_path,
            )
        runtime = GovernedRuntime(
            ledger,
            mode="observe",
            policy_evaluator=counted_evaluator,
        )
        first = runtime.begin(proposal(), call_id="replayed-call")
        runtime.complete(first, progress="unknown")
        mirrored_before = jsonl_path.read_text(encoding="utf-8")

        second = runtime.begin(proposal(), call_id="replayed-call")

        self.assertEqual(calls, 1)
        self.assertEqual(first.decision, second.decision)
        self.assertEqual(jsonl_path.read_text(encoding="utf-8"), mirrored_before)
        self.assertEqual(len(ledger.events(proposal().session_id)), 4)

    def test_incomplete_stored_decision_is_rejected_instead_of_reevaluated(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        with closing(sqlite3.connect(self.ledger.sqlite_path)) as connection:
            connection.execute(
                "UPDATE call_events SET status = NULL WHERE event_type = 'policy.decided'"
            )
            connection.commit()

        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(proposal(), call_id="host-call-ref")

    def test_orphan_stored_decision_is_rejected_instead_of_replayed(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        with closing(sqlite3.connect(self.ledger.sqlite_path)) as connection:
            connection.execute(
                "DELETE FROM call_events WHERE event_type != 'policy.decided'"
            )
            connection.commit()

        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(proposal(), call_id="host-call-ref")

    def test_corrupt_stored_row_is_rejected_instead_of_reevaluated(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        with closing(sqlite3.connect(self.ledger.sqlite_path)) as connection:
            connection.execute(
                "UPDATE call_events SET route = route || ' ' WHERE event_type = 'call.started'"
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "stored event row"):
            self.runtime.begin(proposal(), call_id="host-call-ref")

    def test_multiple_stored_decisions_are_rejected(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        decision_event = self.ledger.events(proposal().session_id)[1]
        self.ledger.append(replace(decision_event, event_id=str(uuid.uuid4())))

        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(proposal(), call_id="host-call-ref")

    def test_replay_rejects_mode_inconsistent_would_block_decision(self) -> None:
        first = self.runtime.begin(proposal(), call_id="seed-call")
        self.runtime.complete(first)
        self.runtime.begin(proposal(), call_id="host-call-ref")
        with closing(sqlite3.connect(self.ledger.sqlite_path)) as connection:
            connection.execute(
                """
                UPDATE call_events
                SET mode = 'enforce'
                WHERE call_id = 'host-call-ref' AND event_type = 'policy.decided'
                """
            )
            connection.commit()

        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(proposal(), call_id="host-call-ref")

    def test_replay_requires_applicable_decision_digest_fields(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        with closing(sqlite3.connect(self.ledger.sqlite_path)) as connection:
            connection.execute(
                """
                UPDATE call_events
                SET input_digest = NULL
                WHERE event_type = 'policy.decided'
                """
            )
            connection.commit()

        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(proposal(), call_id="host-call-ref")

    def test_replay_requires_current_policy_version_and_budget_after(self) -> None:
        for index, assignment in enumerate((
            "policy_version = 'tampered-version'",
            "budget_after = 123",
        )):
            with self.subTest(assignment=assignment):
                ledger = CallLedger(
                    Path(self.tempdir.name) / f"decision-corruption-{index}.sqlite3"
                )
                runtime = GovernedRuntime(ledger, mode="observe")
                runtime.begin(proposal(), call_id="host-call-ref")
                with closing(sqlite3.connect(ledger.sqlite_path)) as connection:
                    connection.execute(
                        f"UPDATE call_events SET {assignment} "
                        "WHERE event_type = 'policy.decided'"
                    )
                    connection.commit()

                with self.assertRaises(StoredDecisionReplayError):
                    runtime.begin(proposal(), call_id="host-call-ref")

    def test_internal_error_replay_requires_current_version_and_null_budgets(self) -> None:
        for index, assignment in enumerate((
            "policy_version = 'tampered-version'",
            "budget_before = 123",
            "budget_after = 123",
        )):
            with self.subTest(assignment=assignment):
                ledger = CallLedger(
                    Path(self.tempdir.name) / f"internal-corruption-{index}.sqlite3"
                )

                def broken_evaluator(_document):
                    raise RuntimeError("private evaluator failure")

                runtime = GovernedRuntime(
                    ledger,
                    mode="observe",
                    failure_policy="fail-open",
                    policy_evaluator=broken_evaluator,
                )
                runtime.begin(proposal(), call_id="host-call-ref")
                with closing(sqlite3.connect(ledger.sqlite_path)) as connection:
                    connection.execute(
                        f"UPDATE call_events SET {assignment} "
                        "WHERE event_type = 'policy.decided'"
                    )
                    connection.commit()

                with self.assertRaises(StoredDecisionReplayError):
                    runtime.begin(proposal(), call_id="host-call-ref")

    def test_atomic_transition_rolls_back_inconsistent_callback_decision(self) -> None:
        candidate = proposal()
        decision = RuntimeDecision(
            True,
            True,
            "allowed",
            candidate.fingerprint,
            5,
            policy_context(),
            budget_before=6,
            budget_after=5,
            decision_latency_ms=0.1,
        )
        facts = build_policy_facts(candidate)
        events = [
            self.runtime._event(
                candidate,
                "bad-call",
                decision,
                "proposed",
                event_type="call.proposed",
                source="runtime",
                metadata={},
                policy_facts=facts,
            ),
            self.runtime._event(
                candidate,
                "bad-call",
                decision,
                "proposed",
                event_type="policy.decided",
                source="runtime",
                metadata=decision.context,
                include_proposal_metadata=False,
            ),
            self.runtime._event(
                candidate,
                "bad-call",
                decision,
                "started",
                event_type="call.started",
                source="runtime",
                metadata={},
            ),
        ]
        events[1] = replace(
            events[1],
            policy_allowed=False,
            execution_allowed=True,
            decision="would_block",
            decision_reason="budget_exhausted",
            reason_code="budget_exhausted",
        )
        events[2] = replace(
            events[2],
            policy_allowed=False,
            decision_reason="budget_exhausted",
        )

        with self.assertRaisesRegex(ValueError, "callback decision"):
            self.ledger.atomic_transition(
                candidate.session_id,
                "bad-call",
                candidate.fingerprint,
                lambda _history: (decision, events),
            )
        self.assertEqual(self.ledger.events(candidate.session_id), [])

    def test_atomic_transition_rejects_semantically_inconsistent_sequence(self) -> None:
        candidate = proposal()
        decision = RuntimeDecision(
            True,
            True,
            "allowed",
            candidate.fingerprint,
            5,
            policy_context(),
            budget_before=6,
            budget_after=5,
            decision_latency_ms=0.1,
        )

        corruptions = {
            "proposal phase": lambda events: events.__setitem__(
                0,
                replace(events[0], phase="started"),
            ),
            "started status": lambda events: events.__setitem__(
                2,
                replace(events[2], status="completed"),
            ),
            "trace identity": lambda events: events.__setitem__(
                2,
                replace(events[2], trace_id="other-trace"),
            ),
            "span identity": lambda events: events.__setitem__(
                2,
                replace(events[2], span_id="other-span"),
            ),
            "policy facts fingerprint": lambda events: events.__setitem__(
                0,
                replace(
                    events[0],
                    policy_facts={
                        **dict(events[0].policy_facts),
                        "fingerprint": "sha256:" + "f" * 64,
                    },
                ),
            ),
            "policy facts requested budget": lambda events: events.__setitem__(
                0,
                replace(
                    events[0],
                    policy_facts={
                        **dict(events[0].policy_facts),
                        "requested_budget_limit": 7,
                    },
                ),
            ),
            "policy facts required fields": lambda events: events.__setitem__(
                0,
                replace(
                    events[0],
                    policy_facts={
                        **dict(events[0].policy_facts),
                        "required_fields_present": False,
                    },
                ),
            ),
        }
        for index, (label, corrupt) in enumerate(corruptions.items()):
            with self.subTest(label=label):
                ledger = CallLedger(
                    Path(self.tempdir.name) / f"invalid-sequence-{index}.sqlite3"
                )
                runtime = GovernedRuntime(ledger, mode="observe")
                call_id = f"invalid-call-{index}"
                events = canonical_atomic_events(runtime, candidate, call_id, decision)
                corrupt(events)

                with self.assertRaisesRegex(ValueError, "atomic transition"):
                    ledger.atomic_transition(
                        candidate.session_id,
                        call_id,
                        candidate.fingerprint,
                        lambda _history: (decision, events),
                    )
                self.assertEqual(ledger.events(candidate.session_id), [])

        ledger = CallLedger(Path(self.tempdir.name) / "four-row-sequence.sqlite3")
        runtime = GovernedRuntime(ledger, mode="observe")
        events = canonical_atomic_events(
            runtime,
            candidate,
            "four-row-call",
            decision,
        )
        events.append(
            runtime._event(
                candidate,
                "four-row-call",
                decision,
                "completed",
                event_type="call.completed",
                progress="material_progress",
                duration_ms=0.1,
                source="runtime",
                metadata={},
            )
        )
        with self.assertRaisesRegex(ValueError, "exactly three"):
            ledger.atomic_transition(
                candidate.session_id,
                "four-row-call",
                candidate.fingerprint,
                lambda _history: (decision, events),
            )
        self.assertEqual(ledger.events(candidate.session_id), [])

        handle = runtime.begin(candidate, call_id="replay-terminal-call")
        runtime.complete(handle, progress="material_progress")
        replayed = runtime.begin(candidate, call_id="replay-terminal-call")
        self.assertEqual(replayed.decision, handle.decision)
        self.assertEqual(len(ledger.events(candidate.session_id)), 4)

    def test_atomic_transition_rejects_nonapplicable_prefix_fields(self) -> None:
        candidate = proposal()
        decision = RuntimeDecision(
            True,
            True,
            "allowed",
            candidate.fingerprint,
            5,
            policy_context(),
            budget_before=6,
            budget_after=5,
            decision_latency_ms=0.1,
        )
        invalid_fields = (
            ("progress", {"progress": "material_progress"}),
            ("duration", {"duration_ms": 0.1}),
            ("execution latency", {"execution_latency_ms": 0.1}),
            ("error", {"error_type": "RuntimeError"}),
            ("prompt tokens", {"prompt_tokens": 1}),
            ("completion tokens", {"completion_tokens": 1}),
            ("total tokens", {"total_tokens": 2}),
            (
                "estimated cost",
                {"estimated_cost_usd": 0.01, "pricing_version": "test-pricing"},
            ),
            ("pricing version", {"pricing_version": "test-pricing"}),
        )

        for row_index, event_type in enumerate(
            ("call.proposed", "policy.decided", "call.started")
        ):
            for field_index, (label, changes) in enumerate(invalid_fields):
                with self.subTest(event_type=event_type, field=label):
                    ledger = CallLedger(
                        Path(self.tempdir.name)
                        / f"invalid-prefix-{row_index}-{field_index}.sqlite3"
                    )
                    runtime = GovernedRuntime(ledger, mode="observe")
                    call_id = f"invalid-prefix-{row_index}-{field_index}"
                    events = canonical_atomic_events(
                        runtime,
                        candidate,
                        call_id,
                        decision,
                    )
                    events[row_index] = replace(events[row_index], **changes)

                    with self.assertRaisesRegex(ValueError, "atomic transition"):
                        ledger.atomic_transition(
                            candidate.session_id,
                            call_id,
                            candidate.fingerprint,
                            lambda _history: (decision, events),
                        )
                    self.assertEqual(ledger.events(candidate.session_id), [])
                    if event_type == "call.started" and label == "progress":
                        self.assertEqual(ledger.history(candidate.session_id), [])

    def test_replay_rejects_semantically_corrupt_companion_row(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        with closing(sqlite3.connect(self.ledger.sqlite_path)) as connection:
            connection.execute(
                """
                UPDATE call_events
                SET status = 'completed'
                WHERE event_type = 'call.started'
                """
            )
            connection.commit()

        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(proposal(), call_id="host-call-ref")

    def test_replay_rejects_terminal_trace_corruption(self) -> None:
        handle = self.runtime.begin(proposal(), call_id="host-call-ref")
        self.runtime.complete(handle)
        with closing(sqlite3.connect(self.ledger.sqlite_path)) as connection:
            connection.execute(
                """
                UPDATE call_events
                SET trace_id = 'other-trace'
                WHERE event_type = 'call.completed'
                """
            )
            connection.commit()

        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(proposal(), call_id="host-call-ref")

    def test_replay_rejects_semantically_tampered_policy_facts(self) -> None:
        corruptions = {
            "requested budget": {"requested_budget_limit": 7},
            "required fields": {"required_fields_present": False},
        }
        for index, (label, changes) in enumerate(corruptions.items()):
            with self.subTest(field=label):
                ledger = CallLedger(
                    Path(self.tempdir.name) / f"tampered-facts-{index}.sqlite3"
                )
                runtime = GovernedRuntime(ledger, mode="observe")
                call_id = f"tampered-facts-{index}"
                runtime.begin(proposal(), call_id=call_id)
                with closing(sqlite3.connect(ledger.sqlite_path)) as connection:
                    row = connection.execute(
                        """
                        SELECT policy_facts_json FROM call_events
                        WHERE call_id = ? AND event_type = 'call.proposed'
                        """,
                        (call_id,),
                    ).fetchone()
                    facts = json.loads(row[0])
                    facts.update(changes)
                    connection.execute(
                        """
                        UPDATE call_events SET policy_facts_json = ?
                        WHERE call_id = ? AND event_type = 'call.proposed'
                        """,
                        (json.dumps(facts, sort_keys=True), call_id),
                    )
                    connection.commit()

                with self.assertRaises(StoredDecisionReplayError):
                    runtime.begin(proposal(), call_id=call_id)

    def test_replay_binds_changed_strategy_fact_when_reason_is_derivable(self) -> None:
        first = self.runtime.begin(proposal(), call_id="first-low-progress")
        self.runtime.complete(first, progress="low_progress")
        candidate = replace(proposal(), route="different-agent")
        second = self.runtime.begin(candidate, call_id="changed-strategy-required")
        self.assertEqual(second.decision.reason, "changed_strategy_required")

        with closing(sqlite3.connect(self.ledger.sqlite_path)) as connection:
            row = connection.execute(
                """
                SELECT policy_facts_json FROM call_events
                WHERE call_id = 'changed-strategy-required'
                  AND event_type = 'call.proposed'
                """
            ).fetchone()
            facts = json.loads(row[0])
            facts["changed_strategy_present"] = True
            connection.execute(
                """
                UPDATE call_events SET policy_facts_json = ?
                WHERE call_id = 'changed-strategy-required'
                  AND event_type = 'call.proposed'
                """,
                (json.dumps(facts, sort_keys=True),),
            )
            connection.commit()

        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(candidate, call_id="changed-strategy-required")

    def test_atomic_transition_rejects_impossible_mandatory_reason_ordering(self) -> None:
        candidate = replace(proposal(), mandatory_reason="safety")
        context = {**policy_context(), "mandatory_reason": "safety"}
        decision = RuntimeDecision(
            True,
            True,
            "allowed",
            candidate.fingerprint,
            5,
            context,
            budget_before=6,
            budget_after=5,
            decision_latency_ms=0.1,
        )
        events = canonical_atomic_events(
            self.runtime,
            candidate,
            "invalid-mandatory-ordering",
            decision,
        )

        with self.assertRaisesRegex(ValueError, "mandatory"):
            self.ledger.atomic_transition(
                candidate.session_id,
                "invalid-mandatory-ordering",
                candidate.fingerprint,
                lambda _history: (decision, events),
            )
        self.assertEqual(self.ledger.events(candidate.session_id), [])

    def test_replay_rejects_noncanonical_terminal_metrics_and_shape(self) -> None:
        corruptions = (
            ("completed latency", "completed", "execution_latency_ms = 999"),
            ("completed progress", "completed", "progress = NULL"),
            ("completed error", "completed", "error_type = 'RuntimeError'"),
            ("failed latency", "failed", "execution_latency_ms = 999"),
            ("failed progress", "failed", "progress = NULL"),
            ("failed error", "failed", "error_type = NULL"),
            ("cancelled latency", "cancelled", "execution_latency_ms = 999"),
            ("cancelled progress", "cancelled", "progress = NULL"),
            ("cancelled error", "cancelled", "error_type = 'RuntimeError'"),
        )
        for index, (label, terminal, assignment) in enumerate(corruptions):
            with self.subTest(case=label):
                ledger = CallLedger(
                    Path(self.tempdir.name) / f"terminal-shape-{index}.sqlite3"
                )
                runtime = GovernedRuntime(ledger, mode="observe")
                call_id = f"terminal-shape-{index}"
                handle = runtime.begin(proposal(), call_id=call_id)
                if terminal == "completed":
                    runtime.complete(handle)
                elif terminal == "failed":
                    runtime.fail(handle, RuntimeError("private failure"))
                else:
                    runtime.cancel(handle)
                with closing(sqlite3.connect(ledger.sqlite_path)) as connection:
                    connection.execute(
                        f"UPDATE call_events SET {assignment} "
                        "WHERE call_id = ? AND event_type = ?",
                        (call_id, f"call.{terminal}"),
                    )
                    connection.commit()

                with self.assertRaises(StoredDecisionReplayError):
                    runtime.begin(proposal(), call_id=call_id)

    def test_replay_accepts_unmeasured_host_terminal(self) -> None:
        candidate = proposal()
        handle = self.runtime.begin(candidate, call_id="host-terminal")
        self.ledger.append(
            self.runtime._event(
                candidate,
                "host-terminal",
                handle.decision,
                "completed",
                event_type="call.completed",
                progress="unknown",
                source="codex",
                metadata={},
            )
        )

        replayed = self.runtime.begin(candidate, call_id="host-terminal")
        terminal = self.ledger.events(candidate.session_id)[-1]
        self.assertEqual(replayed.decision, handle.decision)
        self.assertIsNone(terminal.duration_ms)
        self.assertIsNone(terminal.execution_latency_ms)

    def test_same_call_replays_after_stale_reservation_recovery(self) -> None:
        candidate = proposal()
        first = self.runtime.begin(candidate, call_id="stale-call")
        recovered = self.ledger.recover_stale_reservations(
            candidate.trace_id,
            1,
            now=datetime(2100, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(recovered, 1)

        replayed = self.runtime.begin(candidate, call_id="stale-call")
        terminal = self.ledger.events(candidate.session_id)[-1]
        self.assertEqual(replayed.decision, first.decision)
        self.assertEqual(terminal.source_event, "stale_reservation_recovered")
        self.assertEqual(terminal.progress, "unknown")
        self.assertIsNone(terminal.duration_ms)
        self.assertIsNone(terminal.execution_latency_ms)

    def test_policy_fact_budget_relation_accepts_default_and_explicit_limits(self) -> None:
        cases = (
            (None, 6, False),
            (3, 6, True),
            (9, 9, False),
        )
        for index, (requested, effective, floor_applied) in enumerate(cases):
            with self.subTest(requested=requested):
                ledger = CallLedger(
                    Path(self.tempdir.name) / f"budget-relation-{index}.sqlite3"
                )
                runtime = GovernedRuntime(ledger, mode="observe")
                candidate = replace(proposal(), budget_limit=requested)
                handle = runtime.begin(candidate, call_id=f"budget-relation-{index}")
                self.assertEqual(handle.decision.context["effective_limit"], effective)
                self.assertEqual(
                    handle.decision.context["budget_floor_applied"],
                    floor_applied,
                )

    def test_terminal_metadata_cannot_repeat_or_inject_policy_context(self) -> None:
        handle = self.runtime.begin(proposal(), call_id="host-call-ref")
        self.runtime.complete(handle, metadata=policy_context())
        terminal = self.ledger.events(proposal().session_id)[-1]
        self.assertTrue(set(policy_context()).isdisjoint(terminal.metadata))

        canonical_json = json.dumps(
            policy_context(),
            ensure_ascii=False,
            sort_keys=True,
        )
        with closing(sqlite3.connect(self.ledger.sqlite_path)) as connection:
            connection.execute(
                """
                UPDATE call_events
                SET safe_metadata_json = ?, metadata_json = ?
                WHERE event_type = 'call.completed'
                """,
                (canonical_json, canonical_json),
            )
            connection.commit()

        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(proposal(), call_id="host-call-ref")

    def test_direct_append_rejects_policy_context_on_non_decision_row(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        started = self.ledger.events(proposal().session_id)[-1]
        injected = replace(
            started,
            event_id=str(uuid.uuid4()),
            call_id="injected-call",
            span_id="injected-call",
            metadata=policy_context(),
        )

        with self.assertRaisesRegex(ValueError, "policy context"):
            self.ledger.append(injected)

    def test_replay_rejects_blocked_or_multiple_terminal_suffixes(self) -> None:
        blocked_candidate = replace(
            proposal(),
            budget_kind="agent",
            budget_limit=0,
            profile="strict",
            quality_risk="low",
        )
        blocked_ledger = CallLedger(Path(self.tempdir.name) / "blocked-suffix.sqlite3")
        enforcing = GovernedRuntime(blocked_ledger, mode="enforce")
        with self.assertRaises(GovernanceBlocked) as captured:
            enforcing.begin(blocked_candidate, call_id="blocked-call")
        blocked_ledger.append(
            enforcing._event(
                blocked_candidate,
                "blocked-call",
                captured.exception.decision,
                "completed",
                event_type="call.completed",
                progress="material_progress",
                duration_ms=0.1,
                source="runtime",
                metadata={},
            )
        )
        with self.assertRaises(StoredDecisionReplayError):
            enforcing.begin(blocked_candidate, call_id="blocked-call")

        handle = self.runtime.begin(proposal(), call_id="multiple-terminal-call")
        self.runtime.complete(handle)
        self.ledger.append(
            self.runtime._event(
                proposal(),
                "multiple-terminal-call",
                handle.decision,
                "failed",
                event_type="call.failed",
                progress="low_progress",
                duration_ms=0.2,
                source="runtime",
                metadata={},
                error_type="RuntimeError",
            )
        )
        with self.assertRaises(StoredDecisionReplayError):
            self.runtime.begin(proposal(), call_id="multiple-terminal-call")

    def test_canonical_decision_status_and_terminal_latency_fields(self) -> None:
        first = self.runtime.begin(proposal(), call_id="first-call")
        self.runtime.complete(first, progress="unknown")
        allowed = self.ledger.events(proposal().session_id)
        self.assertEqual(
            [(event.event_type, event.status) for event in allowed],
            [
                ("call.proposed", "proposed"),
                ("policy.decided", "decided"),
                ("call.started", "running"),
                ("call.completed", "completed"),
            ],
        )
        self.assertEqual(allowed[1].decision, "allow")
        self.assertEqual(allowed[1].reason_code, "allowed")
        self.assertEqual(allowed[1].reason_code, allowed[1].decision_reason)
        self.assertIsNotNone(allowed[-1].execution_latency_ms)
        self.assertEqual(allowed[-1].execution_latency_ms, allowed[-1].duration_ms)

        second = self.runtime.begin(proposal(), call_id="second-call")
        observed = self.ledger.events(proposal().session_id)[-2:]
        self.assertEqual(observed[0].decision, "would_block")
        self.assertEqual(observed[1].event_type, "call.started")
        self.assertFalse(second.decision.policy_allowed)

        enforcing_ledger = CallLedger(Path(self.tempdir.name) / "enforce.sqlite3")
        enforcing = GovernedRuntime(enforcing_ledger, mode="enforce")
        seed = enforcing.begin(proposal(), call_id="seed")
        enforcing.complete(seed)
        with self.assertRaises(GovernanceBlocked):
            enforcing.begin(proposal(), call_id="blocked")
        blocked = enforcing_ledger.events(proposal().session_id)[-2:]
        self.assertEqual(blocked[0].decision, "block")
        self.assertEqual(blocked[1].status, "blocked")


if __name__ == "__main__":
    unittest.main()
