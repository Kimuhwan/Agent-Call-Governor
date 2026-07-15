import copy
import hashlib
import inspect
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "skills" / "agent-call-governor" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_call_governor_runtime.ledger import CallLedger
from agent_call_governor_runtime.models import CallEvent, CallProposal
from agent_call_governor_runtime.policy import evaluate


def digest_reference(value):
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def policy_facts():
    return {
        "fingerprint": "sha256:" + "b" * 64,
        "fingerprint_version": 2,
        "budget_kind": "agent",
        "requested_budget_limit": 2,
        "profile": "balanced",
        "risk": "medium",
        "mandatory_reason": None,
        "changed_strategy_present": False,
        "required_fields_present": True,
        "duplicate_scope": "turn",
        "state_token_digest": None,
        "policy_facts_version": 1,
    }


class RuntimeLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "events.sqlite3"
        self.jsonl_path = self.root / "events.jsonl"
        self.ledger = CallLedger(self.db_path, self.jsonl_path)

    def event(
        self,
        phase,
        *,
        session_id="session-1",
        call_id="call-1",
        objective="Inspect authentication failure",
        fingerprint=digest_reference("fp-1"),
        progress=None,
        budget_kind="agent",
        metadata=None,
        **overrides,
    ):
        values = dict(
            session_id=session_id,
            call_id=call_id,
            phase=phase,
            objective=objective,
            route="specialist-agent",
            fingerprint=fingerprint,
            budget_kind=budget_kind,
            profile="balanced",
            quality_risk="medium",
            mode="observe",
            policy_allowed=phase != "blocked",
            execution_allowed=phase != "blocked",
            decision_reason="allowed" if phase != "blocked" else "duplicate_fingerprint",
            progress=progress,
            metadata=metadata or {},
        )
        values.update(overrides)
        return CallEvent.create(**values)

    def test_jsonl_path_emits_one_release_deprecation_warning(self):
        with self.assertWarnsRegex(DeprecationWarning, "jsonl_path"):
            CallLedger(self.root / "deprecated.sqlite3", self.root / "deprecated.jsonl")

    def assert_read_apis_reject_storage(self, ledger, call_id, field):
        read_operations = {
            "events": lambda: ledger.events("session-1"),
            "latest_event": lambda: ledger.latest_event("session-1", call_id),
            "history": lambda: ledger.history("session-1"),
        }
        for api_name, operation in read_operations.items():
            with self.subTest(field=field, api=api_name):
                with self.assertRaisesRegex(ValueError, field):
                    operation()

    def test_legacy_create_arguments_project_canonical_v2_fields(self):
        event = self.event("started")

        self.assertEqual(event.schema_version, 2)
        self.assertEqual(event.fingerprint_version, 2)
        self.assertEqual(event.event_type, "call.started")
        self.assertEqual(event.observed_at, event.occurred_at)
        self.assertEqual(event.trace_id, event.session_id)
        self.assertEqual(event.span_id, event.call_id)
        self.assertIsNone(event.turn_id)
        self.assertIsNone(event.parent_span_id)
        self.assertEqual(event.source_event, event.source)

    def test_call_event_rejects_invalid_v2_privacy_and_numeric_fields(self):
        invalid_values = {
            "event_type": "call.unknown",
            "raw_input_stored": True,
            "budget_before": -1,
            "budget_after": -1,
            "decision_latency_ms": -0.1,
            "execution_latency_ms": -0.1,
            "prompt_tokens": -1,
            "completion_tokens": -1,
            "total_tokens": -1,
            "estimated_cost_usd": -0.01,
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    self.event("started", **{field: value})

    def test_call_event_accepts_unknown_operational_progress(self):
        event = self.event("cancelled", progress="unknown")

        self.ledger.append(event)

        self.assertEqual(self.ledger.events("session-1")[0].progress, "unknown")

        completed = self.event("completed", call_id="call-2", progress="unknown")
        self.ledger.append(completed)
        history = self.ledger.history("session-1")
        self.assertEqual(
            history,
            [
                {
                    "fingerprint": digest_reference("fp-1"),
                    "fingerprint_version": 2,
                    "budget_kind": "agent",
                    "progress": "unknown",
                }
            ],
        )
        proposal = CallProposal(
            session_id="session-1",
            objective="Inspect a different failure",
            route="different-agent",
            capability_gap="New evidence is needed",
            expected_new_information="A distinct root cause",
            stop_condition="The new cause is identified",
            material_inputs={"scope": "different"},
        )
        self.assertTrue(evaluate(proposal.to_policy_document(history))["allowed"])

    def test_non_call_canonical_events_do_not_enter_policy_history(self):
        for index, (phase, event_type) in enumerate(
            (
                ("started", "session.started"),
                ("completed", "progress.observed"),
                ("completed", "session.stopped"),
            ),
            start=1,
        ):
            self.ledger.append(self.event(
                phase,
                call_id=f"non-call-{index}",
                fingerprint=digest_reference(f"non-call-{index}"),
                event_type=event_type,
            ))

        self.assertEqual(len(self.ledger.events("session-1")), 3)
        self.assertEqual(self.ledger.history("session-1"), [])

    def test_auxiliary_progress_does_not_replace_call_lifecycle_state(self):
        self.ledger.append(self.event(
            "completed",
            call_id="completed-call",
            fingerprint=digest_reference("completed-call"),
            progress="material_progress",
        ))
        self.ledger.append(self.event(
            "completed",
            call_id="completed-call",
            fingerprint=digest_reference("completed-call"),
            event_type="progress.observed",
            progress="material_progress",
        ))
        self.ledger.append(self.event(
            "started",
            call_id="cancelled-call",
            fingerprint=digest_reference("cancelled-call"),
        ))
        self.ledger.append(self.event(
            "cancelled",
            call_id="cancelled-call",
            fingerprint=digest_reference("cancelled-call"),
            progress="unknown",
        ))
        self.ledger.append(self.event(
            "completed",
            call_id="cancelled-call",
            fingerprint=digest_reference("cancelled-call"),
            event_type="progress.observed",
            progress="material_progress",
        ))

        self.assertEqual(
            self.ledger.history("session-1"),
            [
                {
                    "fingerprint": digest_reference("completed-call"),
                    "fingerprint_version": 2,
                    "budget_kind": "agent",
                    "progress": "material_progress",
                }
            ],
        )

    def test_history_strictly_decodes_auxiliary_rows_before_projection(self):
        self.ledger.append(self.event(
            "completed",
            call_id="call-auxiliary",
            event_type="progress.observed",
        ))
        self.ledger.append(self.event("completed", call_id="call-auxiliary"))
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE call_events SET route = ' padded-route ' "
                "WHERE event_type = 'progress.observed'"
            )
            connection.commit()

        with self.assertRaisesRegex(ValueError, "route"):
            self.ledger.history("session-1")

    def test_estimated_cost_requires_pricing_version(self):
        with self.assertRaisesRegex(ValueError, "pricing_version"):
            self.event("completed", estimated_cost_usd=0.01)

        event = self.event(
            "completed",
            estimated_cost_usd=0.01,
            pricing_version="openai-2026-07-14",
        )
        self.assertEqual(event.estimated_cost_usd, 0.01)

    def test_export_names_are_round_trip_compatible(self):
        event = self.event(
            "proposed",
            metadata={"exit_code": 0},
            policy_facts=policy_facts(),
            fingerprint_version=2,
        )

        encoded = event.to_dict()

        self.assertNotIn("metadata", encoded)
        self.assertNotIn("policy_facts", encoded)
        self.assertNotIn("quality_risk", encoded)
        self.assertEqual(encoded["risk"], "medium")
        self.assertEqual(encoded["safe_metadata_json"], {"exit_code": 0})
        self.assertEqual(encoded["policy_facts_json"], policy_facts())
        self.assertEqual(CallEvent.from_dict(encoded), event)
        legacy_encoded = dict(encoded)
        legacy_encoded["quality_risk"] = legacy_encoded.pop("risk")
        self.assertEqual(CallEvent.from_dict(legacy_encoded), event)

    def test_absent_policy_facts_round_trip_as_null(self):
        event = self.event("started")

        encoded = event.to_dict()

        self.assertIsNone(encoded["policy_facts_json"])
        self.assertEqual(CallEvent.from_dict(encoded), event)

    def test_policy_facts_reject_incomplete_or_raw_fields(self):
        with self.assertRaisesRegex(ValueError, "policy_facts must contain exactly"):
            self.event("proposed", policy_facts={"raw_prompt": "private prompt"})

        malformed = policy_facts()
        malformed["risk"] = ["medium"]
        with self.assertRaisesRegex(ValueError, "policy_facts.risk"):
            self.event("proposed", policy_facts=malformed)

    def test_policy_facts_are_immutable_and_export_is_detached(self):
        source_facts = policy_facts()
        event = self.event("proposed", policy_facts=source_facts)

        source_facts["risk"] = "high"
        with self.assertRaises(TypeError):
            event.policy_facts["risk"] = "high"

        exported = event.to_dict()
        exported["policy_facts_json"]["risk"] = "high"

        self.assertEqual(event.policy_facts["risk"], "medium")
        self.assertEqual(event.to_dict()["policy_facts_json"]["risk"], "medium")

    def test_tampered_policy_facts_cannot_be_exported_or_persisted(self):
        for boundary in ("export", "persistence"):
            with self.subTest(boundary=boundary):
                event = self.event(
                    "proposed",
                    call_id=f"call-{boundary}",
                    policy_facts=policy_facts(),
                )
                object.__setattr__(
                    event,
                    "policy_facts",
                    {"raw_prompt": "POLICY-FACTS-PLAINTEXT-CANARY"},
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "policy_facts must contain exactly",
                ):
                    if boundary == "export":
                        event.to_dict()
                    else:
                        self.ledger.append(event)

        self.assertEqual(self.ledger.events(), [])
        self.assertNotIn(b"POLICY-FACTS-PLAINTEXT-CANARY", self.db_path.read_bytes())

    def test_invalid_canonical_fields_are_rejected_at_construction(self):
        invalid_values = {
            "fingerprint": "not-a-digest",
            "input_digest": "sha256:short",
            "decision": "permit",
            "status": "done",
            "schema_version": 3,
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, field):
                    self.event("proposed", **{field: value})

        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.event(
                "proposed",
                fingerprint="b" * 64,
                fingerprint_version=2,
            )

    def test_tampered_canonical_fields_cannot_be_exported_or_persisted(self):
        invalid_values = {
            "fingerprint": "not-a-digest",
            "input_digest": "sha256:short",
            "decision": "permit",
            "status": "done",
            "schema_version": 1,
        }
        for field, value in invalid_values.items():
            for boundary in ("export", "persistence"):
                with self.subTest(field=field, boundary=boundary):
                    event = self.event(
                        "proposed",
                        call_id=f"{field}-{boundary}",
                    )
                    object.__setattr__(event, field, value)
                    with self.assertRaisesRegex(ValueError, field):
                        if boundary == "export":
                            event.to_dict()
                        else:
                            self.ledger.append(event)

        self.assertEqual(self.ledger.events(), [])

    def test_decision_fields_require_exact_canonical_types_at_construction(self):
        invalid_values = {
            "policy_allowed": (0, 1, "true", 2),
            "execution_allowed": (0, 1, "false", -1),
            "decision_reason": ("", "   ", 0, 1, 3.5, False),
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, field):
                        self.event("proposed", **{field: value})

        event = self.event(
            "proposed",
            policy_allowed=None,
            execution_allowed=None,
            decision_reason=None,
        )
        self.assertIsNone(event.policy_allowed)
        self.assertIsNone(event.execution_allowed)
        self.assertIsNone(event.decision_reason)

    def test_tampered_decision_fields_cannot_cross_any_output_boundary(self):
        invalid_values = {
            "policy_allowed": 1,
            "execution_allowed": 0,
            "decision_reason": 42,
        }
        for field, value in invalid_values.items():
            for boundary in ("export", "sqlite", "jsonl"):
                with self.subTest(field=field, value=value, boundary=boundary):
                    root = self.root / f"{field}-{boundary}"
                    ledger = CallLedger(root / "events.sqlite3", root / "events.jsonl")
                    event = self.event(
                        "proposed",
                        call_id=f"{field}-{boundary}",
                    )
                    object.__setattr__(event, field, value)

                    with self.assertRaisesRegex(ValueError, field):
                        if boundary == "export":
                            event.to_dict()
                        elif boundary == "sqlite":
                            ledger.append(event)
                        else:
                            ledger._append_jsonl((event,))

                    with closing(sqlite3.connect(root / "events.sqlite3")) as connection:
                        self.assertEqual(
                            connection.execute("SELECT COUNT(*) FROM call_events").fetchone()[0],
                            0,
                        )
                    jsonl_path = root / "events.jsonl"
                    self.assertFalse(jsonl_path.exists() and jsonl_path.read_bytes())

    def test_tampered_schema_coercions_cannot_be_exported(self):
        invalid_values = {
            "event_id": 1,
            "parent_call_id": 1,
            "occurred_at": 1,
            "objective": "plaintext objective",
            "event_type": 1,
            "turn_id": 1,
            "fingerprint_version": True,
            "failure_policy": 1,
            "reason_code": 1,
            "budget_before": True,
            "decision_latency_ms": True,
            "progress": 1,
            "estimated_cost_usd": float("inf"),
            "pricing_version": 1,
            "error_type": 1,
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field, value=value):
                event = self.event("proposed")
                object.__setattr__(event, field, value)
                with self.assertRaisesRegex(ValueError, field):
                    event.to_dict()

    def test_sqlite_and_jsonl_round_trip_preserve_decision_representation(self):
        event = self.event(
            "proposed",
            policy_allowed=True,
            execution_allowed=False,
            decision_reason="policy allowed in observe mode",
        )

        self.ledger.append(event)

        sqlite_value = self.ledger.events()[0].to_dict()
        jsonl_value = json.loads(self.jsonl_path.read_text(encoding="utf-8"))
        self.assertEqual(sqlite_value, event.to_dict())
        self.assertEqual(jsonl_value, event.to_dict())
        self.assertIs(sqlite_value["policy_allowed"], True)
        self.assertIs(sqlite_value["execution_allowed"], False)
        self.assertIs(jsonl_value["policy_allowed"], True)
        self.assertIs(jsonl_value["execution_allowed"], False)

    def test_legacy_schema_version_is_upgraded_before_export_and_persistence(self):
        event = self.event("started", schema_version=1)

        self.assertEqual(event.schema_version, 2)
        self.assertEqual(event.to_dict()["schema_version"], 2)
        self.ledger.append(event)
        self.assertEqual(self.ledger.events()[0].schema_version, 2)

    def test_public_constructor_preserves_v02_positional_binding(self):
        legacy_parameter_names = [
            "event_id",
            "session_id",
            "call_id",
            "phase",
            "occurred_at",
            "objective",
            "route",
            "fingerprint",
            "budget_kind",
            "profile",
            "quality_risk",
            "mode",
            "policy_allowed",
            "execution_allowed",
            "decision_reason",
            "progress",
            "parent_call_id",
            "duration_ms",
            "source",
            "metadata",
            "error_type",
            "schema_version",
        ]
        parameters = list(inspect.signature(CallEvent).parameters.values())
        self.assertEqual(
            [parameter.name for parameter in parameters[:22]],
            legacy_parameter_names,
        )

        event = CallEvent(
            "event-positional",
            "session-positional",
            "call-positional",
            "completed",
            "2026-07-14T00:00:00Z",
            "Legacy positional objective",
            "Bash",
            digest_reference("legacy-positional"),
            "direct-tool",
            "strict",
            "high",
            "warn",
            True,
            False,
            "legacy decision",
            "material_progress",
            "parent-positional",
            1.25,
            "legacy-runtime",
            {"exit_code": 0},
            "LegacyError",
            1,
        )

        self.assertEqual(event.schema_version, 2)
        self.assertEqual(event.event_type, "call.completed")
        self.assertEqual(event.observed_at, event.occurred_at)
        self.assertEqual(event.trace_id, event.session_id)
        self.assertEqual(event.span_id, event.call_id)
        self.assertEqual(event.parent_span_id, event.parent_call_id)
        self.assertEqual(event.source_event, event.source)
        self.assertEqual(event.fingerprint_version, 1)
        self.assertIs(event.policy_allowed, True)
        self.assertIs(event.execution_allowed, False)
        self.assertEqual(event.decision_reason, "legacy decision")
        self.assertEqual(event.error_type, "LegacyError")

        self.ledger.append(event)
        self.assertEqual(self.ledger.events()[0], event)

    def test_public_v02_positional_constructor_infers_bare_fingerprint_version(self):
        legacy_fingerprint = "b" * 64
        try:
            event = CallEvent(
                "legacy-event",
                "legacy-session",
                "legacy-call",
                "completed",
                "2026-07-14T00:00:00Z",
                "Legacy positional objective",
                "Bash",
                legacy_fingerprint,
                "direct-tool",
                "balanced",
                "medium",
                "observe",
                True,
                False,
                "legacy decision",
                "material_progress",
                None,
                1.25,
                "legacy-runtime",
                {},
                None,
                1,
            )
        except ValueError as exc:
            self.fail(f"released-v0.2 positional event was rejected: {exc}")

        self.assertEqual(event.schema_version, 2)
        self.assertEqual(event.fingerprint, legacy_fingerprint)
        self.assertEqual(event.fingerprint_version, 1)
        self.assertEqual(event.to_dict()["fingerprint_version"], 1)

    def test_sanitized_metadata_is_detached_copyable_and_json_safe(self):
        custom_key = "custom:" + "a" * 64
        source_metadata = {
            "exit_code": 0,
            custom_key: {"details": ["original-secret"]},
        }
        event = self.event(
            "started",
            metadata=source_metadata,
            policy_facts=policy_facts(),
        )
        initial_metadata = event.to_dict()["safe_metadata_json"]
        initial_json = json.dumps(initial_metadata, sort_keys=True)

        source_metadata["exit_code"] = 99
        source_metadata[custom_key]["details"].append("later-secret")

        self.assertEqual(json.dumps(event.metadata, sort_keys=True), initial_json)
        self.assertEqual(event.metadata["exit_code"], 0)

        copied = copy.deepcopy(event)
        self.assertEqual(copied, event)
        self.assertEqual(copied.to_dict(), event.to_dict())
        self.assertIsNot(copied.metadata, event.metadata)
        self.assertIsNot(copied.policy_facts, event.policy_facts)
        with self.assertRaises(TypeError):
            copied.policy_facts["risk"] = "low"
        copied.metadata["exit_code"] = 7
        self.assertEqual(event.metadata["exit_code"], 0)

        raw_key = "raw_prompt"
        canary = "MUTABLE-METADATA-CANARY-must-not-survive"
        event.metadata[raw_key] = canary
        nested = event.metadata[custom_key]
        nested_key = next(iter(nested))
        nested[nested_key].append(canary)

        self.assertIn(raw_key, event.metadata)
        direct_export = json.dumps(event.to_dict(), sort_keys=True)
        self.assertNotIn(raw_key, direct_export)
        self.assertNotIn(canary, direct_export)

    def test_mutable_and_object_setattr_metadata_tampering_is_resanitized_everywhere(self):
        mutable_event = self.event("started", call_id="mutable")
        mutable_key = "raw_prompt_mutable"
        mutable_canary = "MUTABLE-BOUNDARY-CANARY-must-not-survive"
        mutable_event.metadata[mutable_key] = mutable_canary

        bypassed_event = self.event("started", call_id="bypassed")
        bypassed_key = "raw_prompt_bypassed"
        bypassed_canary = "BYPASSED-BOUNDARY-CANARY-must-not-survive"
        object.__setattr__(
            bypassed_event,
            "metadata",
            {bypassed_key: bypassed_canary},
        )

        encoded = "\n".join(
            json.dumps(event.to_dict(), sort_keys=True)
            for event in (mutable_event, bypassed_event)
        )
        self.ledger.append(mutable_event)
        self.ledger.append(bypassed_event)
        with closing(sqlite3.connect(self.db_path)) as connection:
            sqlite_metadata = "\n".join(
                str(value)
                for row in connection.execute(
                    "SELECT metadata_json, safe_metadata_json FROM call_events"
                )
                for value in row
            )
        jsonl = self.jsonl_path.read_text(encoding="utf-8")

        for boundary, representation in {
            "direct": encoded,
            "sqlite": sqlite_metadata,
            "jsonl": jsonl,
        }.items():
            with self.subTest(boundary=boundary):
                self.assertNotIn(mutable_key, representation)
                self.assertNotIn(mutable_canary, representation)
                self.assertNotIn(bypassed_key, representation)
                self.assertNotIn(bypassed_canary, representation)

    def test_non_json_metadata_tampering_is_rejected_without_partial_writes(self):
        event = self.event("started")
        object.__setattr__(event, "metadata", {"raw_prompt": object()})

        with self.assertRaisesRegex(ValueError, "metadata must be JSON-compatible"):
            event.to_dict()
        with self.assertRaisesRegex(ValueError, "metadata must be JSON-compatible"):
            self.ledger.append(event)

        self.assertEqual(self.ledger.events(), [])
        self.assertFalse(self.jsonl_path.exists())

    def test_history_rejects_storage_type_coercions(self):
        invalid_values = {
            "fingerprint": sqlite3.Binary(b"not-a-fingerprint"),
            "budget_kind": sqlite3.Binary(b"agent"),
            "fingerprint_version": "bogus-version",
            "progress": sqlite3.Binary(b"sufficient"),
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field):
                root = self.root / f"history-{field}"
                ledger = CallLedger(root / "events.sqlite3")
                ledger.append(
                    self.event(
                        "completed",
                        call_id=f"history-{field}",
                        progress="sufficient",
                    )
                )
                with closing(sqlite3.connect(root / "events.sqlite3")) as connection:
                    connection.execute(
                        f'UPDATE call_events SET "{field}" = ?',
                        (value,),
                    )
                    connection.commit()

                with self.assertRaisesRegex(ValueError, field):
                    ledger.history("session-1")

    def test_read_apis_reject_constructor_normalized_storage(self):
        invalid_values = {
            "schema_version": 1,
            "event_type": None,
            "observed_at": None,
            "trace_id": None,
            "span_id": None,
            "source_event": None,
            "objective": "private plaintext objective",
            "event_id": " padded-event-id ",
        }
        for index, (field, value) in enumerate(invalid_values.items()):
            with self.subTest(field=field):
                root = self.root / f"normalized-storage-{index}"
                ledger = CallLedger(root / "events.sqlite3")
                call_id = f"normalized-storage-{index}"
                ledger.append(self.event("completed", call_id=call_id))
                with closing(sqlite3.connect(root / "events.sqlite3")) as connection:
                    connection.execute(
                        f'UPDATE call_events SET "{field}" = ?',
                        (value,),
                    )
                    connection.commit()

                self.assert_read_apis_reject_storage(ledger, call_id, field)

    def test_history_strictly_decodes_corrupt_latest_phase_before_counting(self):
        self.ledger.append(self.event("completed"))
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE call_events SET phase = 'corrupt-phase'")
            connection.commit()

        with self.assertRaisesRegex(ValueError, "phase"):
            self.ledger.history("session-1")

    def test_read_apis_reject_strict_sqlite_type_and_domain_corruption(self):
        invalid_values = (
            ("seq", 0),
            ("policy_allowed", sqlite3.Binary(b"1")),
            ("prompt_tokens", 1.5),
            ("route", " specialist-agent "),
            (
                "safe_metadata_json",
                json.dumps({"raw_prompt": "STORAGE-PLAINTEXT-CANARY"}),
            ),
        )
        for index, (field, value) in enumerate(invalid_values):
            with self.subTest(field=field):
                root = self.root / f"strict-storage-{index}"
                ledger = CallLedger(root / "events.sqlite3")
                call_id = f"strict-storage-{index}"
                ledger.append(self.event("completed", call_id=call_id))
                with closing(sqlite3.connect(root / "events.sqlite3")) as connection:
                    connection.execute(
                        f'UPDATE call_events SET "{field}" = ?',
                        (value,),
                    )
                    connection.commit()

                self.assert_read_apis_reject_storage(ledger, call_id, field)

    def test_read_apis_preserve_released_bare_v1_fingerprint(self):
        fingerprint = "b" * 64
        self.ledger.append(
            self.event(
                "completed",
                fingerprint=fingerprint,
                fingerprint_version=1,
                progress="sufficient",
            )
        )

        self.assertEqual(self.ledger.events()[0].fingerprint, fingerprint)
        self.assertEqual(
            self.ledger.latest_event("session-1", "call-1").fingerprint,
            fingerprint,
        )
        self.assertEqual(
            self.ledger.history("session-1"),
            [
                {
                    "fingerprint": fingerprint,
                    "fingerprint_version": 1,
                    "progress": "sufficient",
                    "budget_kind": "agent",
                }
            ],
        )

    def test_started_call_counts_once_and_blocked_call_does_not(self):
        self.ledger.append(
            self.event("blocked", call_id="blocked", fingerprint=digest_reference("fp-blocked"))
        )
        self.ledger.append(
            self.event("started", call_id="real", fingerprint=digest_reference("fp-real"))
        )
        self.ledger.append(
            self.event(
                "completed",
                call_id="real",
                fingerprint=digest_reference("fp-real"),
                progress="sufficient",
            )
        )

        self.assertEqual(
            self.ledger.history("session-1"),
            [
                {
                    "fingerprint": digest_reference("fp-real"),
                    "fingerprint_version": 2,
                    "progress": "sufficient",
                    "budget_kind": "agent",
                }
            ],
        )

    def test_package_exports_ledger_models(self):
        from agent_call_governor_runtime import CallEvent as ExportedEvent
        from agent_call_governor_runtime import CallLedger as ExportedLedger
        from agent_call_governor_runtime import CallProposal as ExportedProposal

        self.assertIs(ExportedEvent, CallEvent)
        self.assertIs(ExportedLedger, CallLedger)
        self.assertIs(ExportedProposal, CallProposal)

    def test_started_without_terminal_state_still_consumes_budget(self):
        self.ledger.append(self.event("started"))

        self.assertEqual(
            self.ledger.history("session-1"),
            [
                {
                    "fingerprint": digest_reference("fp-1"),
                    "fingerprint_version": 2,
                    "budget_kind": "agent",
                }
            ],
        )

    def test_explicit_fingerprint_version_is_exposed_in_history(self):
        self.ledger.append(self.event("started", fingerprint_version=2))

        self.assertEqual(
            self.ledger.history("session-1"),
            [
                {
                    "fingerprint": digest_reference("fp-1"),
                    "fingerprint_version": 2,
                    "budget_kind": "agent",
                }
            ],
        )

    def test_cancelled_before_execution_releases_started_reservation(self):
        self.ledger.append(self.event("started"))
        self.ledger.append(self.event("cancelled", progress="no_progress"))

        self.assertEqual(self.ledger.history("session-1"), [])

    def test_latest_terminal_progress_wins(self):
        self.ledger.append(self.event("started"))
        self.ledger.append(self.event("failed", progress="low_progress"))
        self.ledger.append(self.event("completed", progress="material_progress"))

        self.assertEqual(
            self.ledger.history("session-1")[0]["progress"],
            "material_progress",
        )

    def test_late_proposal_block_and_cancellation_do_not_release_consumed_call(self):
        self.ledger.append(self.event("started", call_id="logical-call"))
        self.ledger.append(
            self.event(
                "completed",
                call_id="logical-call",
                progress="unknown",
            )
        )
        self.ledger.append(self.event("proposed", call_id="logical-call"))
        self.ledger.append(self.event("blocked", call_id="logical-call"))
        self.ledger.append(
            self.event(
                "cancelled",
                call_id="logical-call",
                progress="unknown",
            )
        )

        history = self.ledger.history("session-1")

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["progress"], "unknown")

    def test_sessions_are_isolated(self):
        self.ledger.append(self.event("started", session_id="session-1"))
        self.ledger.append(
            self.event(
                "started",
                session_id="session-2",
                call_id="call-2",
                fingerprint=digest_reference("fp-2"),
                budget_kind="direct-tool",
            )
        )

        self.assertEqual(len(self.ledger.history("session-1")), 1)
        self.assertEqual(
            self.ledger.history("session-2")[0]["budget_kind"],
            "direct-tool",
        )

    def test_reopening_sqlite_preserves_events(self):
        self.ledger.append(self.event("started"))

        reopened = CallLedger(self.db_path)

        self.assertEqual(len(reopened.events("session-1")), 1)
        self.assertEqual(
            reopened.history("session-1")[0]["fingerprint"],
            digest_reference("fp-1"),
        )

    def test_jsonl_mirror_is_one_valid_object_per_line(self):
        self.ledger.append(self.event("started", metadata={"safe": "value"}))
        self.ledger.append(self.event("completed", progress="sufficient"))

        lines = self.jsonl_path.read_text(encoding="utf-8").splitlines()
        decoded = [json.loads(line) for line in lines]

        self.assertEqual(len(decoded), 2)
        key = "custom:" + hashlib.sha256(b"safe").hexdigest()
        value = "sha256:" + hashlib.sha256(b"value").hexdigest()
        self.assertEqual(decoded[0]["safe_metadata_json"], {key: value})
        self.assertNotIn("metadata", decoded[0])
        self.assertNotIn('"safe":', lines[0])
        self.assertNotIn("value", lines[0])

    def test_raw_proposal_text_is_not_part_of_events(self):
        objective_canary = "OBJECTIVE-CANARY-should-never-be-persisted"
        proposal = CallProposal(
            session_id="session-1",
            objective=objective_canary,
            route="specialist-agent",
            capability_gap="Repository evidence is missing",
            expected_new_information="A source-backed root cause",
            stop_condition="The failing path is identified",
            material_inputs={"prompt": "secret prompt"},
        )
        self.ledger.append(
            self.event(
                "started",
                objective=proposal.objective,
                fingerprint=proposal.fingerprint,
            )
        )

        stored = self.jsonl_path.read_text(encoding="utf-8")
        sqlite_bytes = self.db_path.read_bytes()

        self.assertNotIn(objective_canary, stored)
        self.assertNotIn(objective_canary.encode(), sqlite_bytes)
        self.assertNotIn("secret prompt", stored)
        self.assertNotIn("material_inputs", stored)
        self.assertRegex(self.ledger.events("session-1")[0].objective, r"^sha256:[0-9a-f]{64}$")

    def test_metadata_must_be_json_compatible(self):
        with self.assertRaisesRegex(ValueError, "metadata must be JSON-compatible"):
            self.event("started", metadata={"bad": object()})

    def test_concurrent_appends_keep_every_event(self):
        errors = []

        def append(index):
            try:
                self.ledger.append(
                    self.event(
                        "started",
                        call_id=f"call-{index}",
                        fingerprint=digest_reference(f"fp-{index}"),
                    )
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=append, args=(index,)) for index in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(self.ledger.history("session-1")), 20)


if __name__ == "__main__":
    unittest.main()
