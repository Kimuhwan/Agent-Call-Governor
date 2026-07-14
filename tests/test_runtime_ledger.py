import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "agent-call-governor" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_call_governor_runtime.ledger import CallLedger
from agent_call_governor_runtime.models import CallEvent, CallProposal


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
        fingerprint="fp-1",
        progress=None,
        budget_kind="agent",
        metadata=None,
    ):
        return CallEvent.create(
            session_id=session_id,
            call_id=call_id,
            phase=phase,
            objective="Inspect authentication failure",
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

    def test_started_call_counts_once_and_blocked_call_does_not(self):
        self.ledger.append(self.event("blocked", call_id="blocked", fingerprint="fp-blocked"))
        self.ledger.append(self.event("started", call_id="real", fingerprint="fp-real"))
        self.ledger.append(
            self.event(
                "completed",
                call_id="real",
                fingerprint="fp-real",
                progress="sufficient",
            )
        )

        self.assertEqual(
            self.ledger.history("session-1"),
            [
                {
                    "fingerprint": "fp-real",
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
            [{"fingerprint": "fp-1", "budget_kind": "agent"}],
        )

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
                fingerprint="fp-2",
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
        self.assertEqual(reopened.history("session-1")[0]["fingerprint"], "fp-1")

    def test_jsonl_mirror_is_one_valid_object_per_line(self):
        self.ledger.append(self.event("started", metadata={"safe": "value"}))
        self.ledger.append(self.event("completed", progress="sufficient"))

        lines = self.jsonl_path.read_text(encoding="utf-8").splitlines()
        decoded = [json.loads(line) for line in lines]

        self.assertEqual(len(decoded), 2)
        self.assertEqual(decoded[0]["metadata"], {"safe": "value"})

    def test_raw_material_inputs_are_not_part_of_events(self):
        proposal = CallProposal(
            session_id="session-1",
            objective="Inspect authentication failure",
            route="specialist-agent",
            capability_gap="Repository evidence is missing",
            expected_new_information="A source-backed root cause",
            stop_condition="The failing path is identified",
            material_inputs={"prompt": "secret prompt"},
        )
        self.ledger.append(self.event("started", fingerprint=proposal.fingerprint))

        stored = self.jsonl_path.read_text(encoding="utf-8")

        self.assertNotIn("secret prompt", stored)
        self.assertNotIn("material_inputs", stored)

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
                        fingerprint=f"fp-{index}",
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
