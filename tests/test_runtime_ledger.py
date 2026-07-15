import hashlib
import inspect
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "skills" / "agent-call-governor" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_call_governor_runtime.ledger import CallLedger
from agent_call_governor_runtime.models import CallEvent, CallProposal


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

    def test_legacy_create_arguments_project_canonical_v2_fields(self):
        event = self.event("started")

        self.assertEqual(event.schema_version, 2)
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
        self.assertIs(event.policy_allowed, True)
        self.assertIs(event.execution_allowed, False)
        self.assertEqual(event.decision_reason, "legacy decision")
        self.assertEqual(event.error_type, "LegacyError")

        self.ledger.append(event)
        self.assertEqual(self.ledger.events()[0], event)

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
            [{"fingerprint": digest_reference("fp-1"), "budget_kind": "agent"}],
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
