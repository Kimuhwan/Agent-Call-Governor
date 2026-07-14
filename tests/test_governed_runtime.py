import asyncio
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "agent-call-governor" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_call_governor_runtime import CallLedger, CallProposal, evaluate
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

    def test_concurrent_enforce_reserves_duplicate_atomically(self):
        first_evaluating = threading.Event()
        release_first = threading.Event()
        second_invoking = threading.Event()
        evaluation_count = 0
        evaluation_lock = threading.Lock()

        def coordinated_policy(document):
            nonlocal evaluation_count
            with evaluation_lock:
                evaluation_count += 1
                is_first = evaluation_count == 1
            if is_first:
                first_evaluating.set()
                self.assertTrue(release_first.wait(timeout=5))
            return evaluate(document)

        ledger = CallLedger(Path(self.tempdir.name) / "concurrent-duplicate.sqlite3")
        runtime = GovernedRuntime(
            ledger,
            mode="enforce",
            policy_evaluator=coordinated_policy,
        )
        executed = []
        lock = threading.Lock()

        def invoke():
            try:
                return runtime.run(
                    self.proposal(),
                    lambda: self._record_execution(executed, lock),
                )
            except GovernanceBlocked:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(invoke)
            self.assertTrue(first_evaluating.wait(timeout=5))

            def invoke_second():
                second_invoking.set()
                return invoke()

            second = executor.submit(invoke_second)
            self.assertTrue(second_invoking.wait(timeout=5))
            release_first.set()
            results = [first.result(timeout=10), second.result(timeout=10)]

        self.assertEqual(sorted(results), ["blocked", "executed"])
        self.assertEqual(executed, ["executed"])

    def test_concurrent_enforce_reserves_last_budget_slot_atomically(self):
        first_evaluating = threading.Event()
        release_first = threading.Event()
        second_invoking = threading.Event()
        evaluation_count = 0
        evaluation_lock = threading.Lock()

        def coordinated_policy(document):
            nonlocal evaluation_count
            with evaluation_lock:
                evaluation_count += 1
                is_first = evaluation_count == 1
            if is_first:
                first_evaluating.set()
                self.assertTrue(release_first.wait(timeout=5))
            return evaluate(document)

        ledger = CallLedger(Path(self.tempdir.name) / "concurrent-budget.sqlite3")
        runtime = GovernedRuntime(
            ledger,
            mode="enforce",
            policy_evaluator=coordinated_policy,
        )
        proposals = [
            self.proposal(
                objective=f"Inspect independent failure {index}",
                route=f"specialist-agent-{index}",
                material_inputs={"scope": f"server/auth/{index}"},
                profile="strict",
                budget_limit=1,
            )
            for index in (1, 2)
        ]

        def invoke(candidate):
            try:
                return runtime.run(candidate, lambda: "executed")
            except GovernanceBlocked as exc:
                return exc.decision.reason

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(invoke, proposals[0])
            self.assertTrue(first_evaluating.wait(timeout=5))

            def invoke_second():
                second_invoking.set()
                return invoke(proposals[1])

            second = executor.submit(invoke_second)
            self.assertTrue(second_invoking.wait(timeout=5))
            release_first.set()
            results = [first.result(timeout=10), second.result(timeout=10)]

        self.assertEqual(sorted(results), ["budget_exhausted", "executed"])

    def test_explicit_call_id_cannot_be_reused_to_bypass_accounting(self):
        runtime = GovernedRuntime(self.ledger, mode="enforce", failure_policy="fail-open")
        first = runtime.begin(self.proposal(), call_id="stable-call-id")
        runtime.complete(first)
        changed = self.proposal(
            objective="Inspect a separate authorization failure",
            route="second-specialist-agent",
            material_inputs={"scope": "server/authorization"},
        )

        with self.assertRaisesRegex(ValueError, "call_id already exists"):
            runtime.begin(changed, call_id="stable-call-id")

        self.assertEqual(len(self.ledger.history("session-1")), 1)

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

    def test_warn_handler_failure_never_blocks_the_call(self):
        def broken_warning_handler(_message):
            raise RuntimeError("warning sink unavailable")

        runtime = GovernedRuntime(
            self.ledger,
            mode="warn",
            warning_handler=broken_warning_handler,
        )
        runtime.run(self.proposal(), lambda: "first")

        self.assertEqual(runtime.run(self.proposal(), lambda: "second"), "second")

    def test_optional_jsonl_mirror_failure_never_blocks_authoritative_ledger(self):
        class BrokenMirrorLedger(CallLedger):
            def _append_jsonl(self, _events):
                raise OSError("optional mirror unavailable")

        ledger = BrokenMirrorLedger(Path(self.tempdir.name) / "mirror-failure.sqlite3")
        runtime = GovernedRuntime(
            ledger,
            mode="enforce",
            failure_policy="fail-closed",
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = runtime.run(self.proposal(), lambda: "executed")

        self.assertEqual(result, "executed")
        self.assertEqual(
            [event.phase for event in ledger.events("session-1")],
            ["proposed", "started", "completed"],
        )

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

    def test_async_runtime_does_not_block_event_loop_during_ledger_contention(self):
        ready = threading.Event()
        worker_errors = []
        db_path = self.ledger.sqlite_path

        def hold_write_lock():
            try:
                connection = sqlite3.connect(str(db_path), timeout=5)
                connection.execute("BEGIN IMMEDIATE")
                ready.set()
                time.sleep(0.35)
                connection.rollback()
                connection.close()
            except Exception as exc:  # pragma: no cover - asserted below
                worker_errors.append(exc)
                ready.set()

        worker = threading.Thread(target=hold_write_lock)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.assertTrue(ready.wait(timeout=5))

        runtime = GovernedRuntime(self.ledger)

        async def ticker():
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 0.2
            ticks = 0
            while loop.time() < deadline:
                await asyncio.sleep(0.01)
                ticks += 1
            return ticks

        async def work():
            await asyncio.sleep(0)
            return "executed"

        async def scenario():
            return await asyncio.gather(ticker(), runtime.run_async(self.proposal(), work))

        ticks, result = asyncio.run(scenario())
        worker.join(timeout=5)

        self.assertEqual(worker_errors, [])
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, "executed")
        self.assertGreaterEqual(ticks, 8)

    def test_cancelled_async_begin_releases_reservation_before_callable(self):
        ready = threading.Event()
        db_path = self.ledger.sqlite_path

        def hold_write_lock():
            connection = sqlite3.connect(str(db_path), timeout=5)
            connection.execute("BEGIN IMMEDIATE")
            ready.set()
            time.sleep(0.35)
            connection.rollback()
            connection.close()

        worker = threading.Thread(target=hold_write_lock)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.assertTrue(ready.wait(timeout=5))

        runtime = GovernedRuntime(self.ledger, mode="enforce")
        executed = []

        async def work():
            executed.append(True)
            return "must-not-run"

        async def scenario():
            task = asyncio.create_task(runtime.run_async(self.proposal(), work))
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(scenario())
        worker.join(timeout=5)

        self.assertEqual(executed, [])
        self.assertEqual(
            [event.phase for event in self.ledger.events("session-1")],
            ["proposed", "started", "cancelled"],
        )
        self.assertEqual(self.ledger.history("session-1"), [])

    def test_repeated_cancellation_cannot_interrupt_terminal_recording(self):
        completion_started = threading.Event()
        release_completion = threading.Event()

        class DelayedCompletionLedger(CallLedger):
            def append(inner_self, event):
                if event.phase == "completed":
                    completion_started.set()
                    if not release_completion.wait(timeout=5):
                        raise TimeoutError("test did not release completion")
                return super().append(event)

        ledger = DelayedCompletionLedger(Path(self.tempdir.name) / "delayed-completion.sqlite3")
        runtime = GovernedRuntime(ledger, mode="enforce")

        async def work():
            return "executed"

        async def scenario():
            task = asyncio.create_task(runtime.run_async(self.proposal(), work))
            self.assertTrue(await asyncio.to_thread(completion_started.wait, 5))
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
            release_completion.set()
            await task

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(scenario())

        self.assertEqual(
            [event.phase for event in ledger.events("session-1")],
            ["proposed", "started", "completed"],
        )

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

    def test_malformed_policy_boolean_cannot_be_coerced_to_allow(self):
        def malformed_policy(document):
            return {
                "allowed": "false",
                "reason": "allowed",
                "fingerprint": self.proposal().fingerprint,
            }

        runtime = GovernedRuntime(
            self.ledger,
            mode="enforce",
            failure_policy="fail-closed",
            policy_evaluator=malformed_policy,
        )
        calls = []

        with self.assertRaisesRegex(GovernanceBlocked, "internal_error_fail_closed"):
            runtime.run(self.proposal(), lambda: calls.append("executed"))

        self.assertEqual(calls, [])
        self.assertEqual(self.ledger.events("session-1")[-1].phase, "blocked")

    def test_policy_fingerprint_must_match_canonical_proposal(self):
        def mismatched_policy(_document):
            return {
                "allowed": True,
                "reason": "allowed",
                "fingerprint": "different-fingerprint",
            }

        runtime = GovernedRuntime(
            self.ledger,
            mode="enforce",
            failure_policy="fail-closed",
            policy_evaluator=mismatched_policy,
        )

        with self.assertRaisesRegex(GovernanceBlocked, "internal_error_fail_closed"):
            runtime.run(self.proposal(), lambda: "must-not-run")

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

    @staticmethod
    def _record_execution(executed, lock):
        with lock:
            executed.append("executed")
        return "executed"


if __name__ == "__main__":
    unittest.main()
