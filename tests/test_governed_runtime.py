import asyncio
import sys
import tempfile
import unittest
import warnings
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "agent-call-governor" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_call_governor_runtime import CallLedger, CallProposal
from agent_call_governor_runtime.runtime import (
    GovernedRuntime,
    GovernanceBlocked,
    GovernanceInternalError,
)


class GovernedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.ledger = CallLedger(Path(self.tempdir.name) / "events.sqlite3")

    def proposal(self, **overrides):
        values = {
            "session_id": "session-1",
            "objective": "Inspect authentication failure",
            "route": "specialist-agent",
            "capability_gap": "Repository evidence is missing",
            "expected_new_information": "A source-backed root cause",
            "stop_condition": "The failing path is identified",
            "material_inputs": {"scope": "server/auth"},
        }
        values.update(overrides)
        return CallProposal(**values)

    def test_defaults_to_observe_and_records_full_success_lifecycle(self):
        runtime = GovernedRuntime(self.ledger)

        result = runtime.run(self.proposal(), lambda: "ok", progress="sufficient")

        self.assertEqual(result, "ok")
        events = self.ledger.events("session-1")
        self.assertEqual([event.phase for event in events], ["proposed", "started", "completed"])
        self.assertEqual(events[-1].progress, "sufficient")
        self.assertIsNotNone(events[-1].duration_ms)
        self.assertEqual(events[-1].mode, "observe")

    def test_enforce_blocks_duplicate_before_callable_runs(self):
        runtime = GovernedRuntime(self.ledger, mode="enforce")
        runtime.run(self.proposal(), lambda: "first")
        calls = []

        with self.assertRaisesRegex(GovernanceBlocked, "duplicate_fingerprint"):
            runtime.run(self.proposal(), lambda: calls.append("executed"))

        self.assertEqual(calls, [])
        phases = [event.phase for event in self.ledger.events("session-1")]
        self.assertEqual(phases[-2:], ["proposed", "blocked"])

    def test_observe_executes_would_block_call_and_records_policy_decision(self):
        runtime = GovernedRuntime(self.ledger, mode="observe")
        runtime.run(self.proposal(), lambda: "first")

        result = runtime.run(self.proposal(), lambda: "second")

        self.assertEqual(result, "second")
        second_proposal = self.ledger.events("session-1")[-3]
        self.assertEqual(second_proposal.phase, "proposed")
        self.assertFalse(second_proposal.policy_allowed)
        self.assertTrue(second_proposal.execution_allowed)
        self.assertEqual(second_proposal.decision_reason, "duplicate_fingerprint")

    def test_warn_executes_and_emits_runtime_warning(self):
        runtime = GovernedRuntime(self.ledger, mode="warn")
        runtime.run(self.proposal(), lambda: "first")

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            result = runtime.run(self.proposal(), lambda: "second")

        self.assertEqual(result, "second")
        self.assertEqual(len(captured), 1)
        self.assertIn("duplicate_fingerprint", str(captured[0].message))

    def test_application_exception_is_recorded_and_reraised(self):
        runtime = GovernedRuntime(self.ledger)

        def explode():
            raise ValueError("raw secret must not be stored")

        with self.assertRaisesRegex(ValueError, "raw secret"):
            runtime.run(self.proposal(), explode)

        failed = self.ledger.events("session-1")[-1]
        self.assertEqual(failed.phase, "failed")
        self.assertEqual(failed.progress, "low_progress")
        self.assertEqual(failed.error_type, "ValueError")
        self.assertNotIn("raw secret", str(failed.to_dict()))

    def test_async_callable_uses_same_lifecycle(self):
        runtime = GovernedRuntime(self.ledger)

        async def work(value):
            await asyncio.sleep(0)
            return value * 2

        result = asyncio.run(runtime.run_async(self.proposal(), work, 4))

        self.assertEqual(result, 8)
        self.assertEqual(self.ledger.events("session-1")[-1].phase, "completed")

    def test_fail_open_executes_when_policy_evaluation_crashes(self):
        def broken_policy(_document):
            raise RuntimeError("policy unavailable")

        runtime = GovernedRuntime(
            self.ledger,
            mode="enforce",
            failure_policy="fail-open",
            policy_evaluator=broken_policy,
        )

        result = runtime.run(self.proposal(), lambda: "fallback")

        self.assertEqual(result, "fallback")
        proposed = self.ledger.events("session-1")[0]
        self.assertEqual(proposed.decision_reason, "internal_error_fail_open")
        self.assertEqual(proposed.metadata["internal_error_type"], "RuntimeError")

    def test_fail_closed_blocks_when_policy_evaluation_crashes(self):
        def broken_policy(_document):
            raise RuntimeError("policy unavailable")

        runtime = GovernedRuntime(
            self.ledger,
            failure_policy="fail-closed",
            policy_evaluator=broken_policy,
        )
        calls = []

        with self.assertRaisesRegex(GovernanceBlocked, "internal_error_fail_closed"):
            runtime.run(self.proposal(), lambda: calls.append("executed"))

        self.assertEqual(calls, [])
        self.assertEqual(self.ledger.events("session-1")[-1].phase, "blocked")

    def test_fail_closed_blocks_when_ledger_cannot_record_start(self):
        class FailingLedger:
            def history(self, _session_id):
                return []

            def append(self, _event):
                raise OSError("disk unavailable")

        runtime = GovernedRuntime(FailingLedger(), failure_policy="fail-closed")
        calls = []

        with self.assertRaisesRegex(GovernanceInternalError, "ledger"):
            runtime.run(self.proposal(), lambda: calls.append("executed"))

        self.assertEqual(calls, [])

    def test_fail_open_executes_when_ledger_cannot_record(self):
        class FailingLedger:
            def history(self, _session_id):
                return []

            def append(self, _event):
                raise OSError("disk unavailable")

        runtime = GovernedRuntime(FailingLedger(), failure_policy="fail-open")

        self.assertEqual(runtime.run(self.proposal(), lambda: "ok"), "ok")

    def test_package_exports_runtime_api(self):
        from agent_call_governor_runtime import GovernedRuntime as ExportedRuntime
        from agent_call_governor_runtime import GovernanceBlocked as ExportedBlocked

        self.assertIs(ExportedRuntime, GovernedRuntime)
        self.assertIs(ExportedBlocked, GovernanceBlocked)


if __name__ == "__main__":
    unittest.main()
