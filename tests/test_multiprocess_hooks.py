from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_call_governor_runtime import CallLedger


ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = ROOT / "hooks" / "dispatch.py"
FIXTURES = ROOT / "tests" / "fixtures" / "codex-hooks"
GOVERNOR_KEYS = frozenset({
    "AGENT_CALL_GOVERNOR_MODE",
    "AGENT_CALL_GOVERNOR_PROFILE",
    "AGENT_CALL_GOVERNOR_RISK",
    "AGENT_CALL_GOVERNOR_RETENTION_DAYS",
    "AGENT_CALL_GOVERNOR_STALE_SECONDS",
    "AGENT_CALL_GOVERNOR_DB",
    "PLUGIN_ROOT",
    "PLUGIN_DATA",
    "PYTHONPATH",
})


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class MultiprocessHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.plugin_data = self.root / "plugin-data"
        self.db = self.plugin_data / "events.sqlite3"
        CallLedger(self.db, busy_timeout_ms=1000)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def environment(self, *, mode: str = "observe") -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in GOVERNOR_KEYS
            and not key.startswith("AGENT_CALL_GOVERNOR_")
        }
        env.update({
            "PLUGIN_ROOT": str(ROOT),
            "PLUGIN_DATA": str(self.plugin_data),
            "AGENT_CALL_GOVERNOR_MODE": mode,
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        return env

    def dispatch(
        self,
        payload: object,
        *,
        mode: str = "observe",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DISPATCHER)],
            cwd=ROOT,
            env=self.environment(mode=mode),
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            timeout=6,
        )

    def dispatch_many(
        self,
        payloads: list[object],
        *,
        mode: str = "observe",
    ) -> list[subprocess.CompletedProcess[str]]:
        barrier = threading.Barrier(len(payloads))

        def launch(payload: object) -> subprocess.CompletedProcess[str]:
            barrier.wait(timeout=5)
            return self.dispatch(payload, mode=mode)

        with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
            futures = [executor.submit(launch, payload) for payload in payloads]
            return [future.result(timeout=10) for future in futures]

    def assert_quiet_success(
        self,
        results: list[subprocess.CompletedProcess[str]],
    ) -> None:
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def storage_text(self) -> str:
        entries = sorted(self.plugin_data.rglob("*"))
        paths = "".join(str(path.relative_to(self.plugin_data)) for path in entries)
        contents = "".join(
            path.read_bytes().decode("utf-8", errors="ignore")
            for path in entries
            if path.is_file()
        )
        return paths + contents

    def test_eight_processes_collapse_same_delivery_without_raw_state(self) -> None:
        payload = fixture("pre_tool_use.json")
        canaries = {
            "session_id": "RAW_RACE_SESSION_CANARY",
            "turn_id": "RAW_RACE_TURN_CANARY",
            "tool_use_id": "RAW_RACE_TOOL_CANARY",
        }
        payload.update(canaries)
        payload["tool_input"] = {"query": "RAW_RACE_INPUT_CANARY"}

        results = self.dispatch_many([payload] * 8)

        self.assert_quiet_success(results)
        events = CallLedger(self.db).events()
        self.assertEqual(
            [event.event_type for event in events],
            ["call.proposed", "policy.decided", "call.started"],
        )
        self.assertEqual(len({event.call_id for event in events}), 1)
        self.assertEqual(len([event for event in events if event.event_type == "policy.decided"]), 1)
        combined = "".join(result.stdout + result.stderr for result in results) + self.storage_text()
        for canary in (*canaries.values(), "RAW_RACE_INPUT_CANARY"):
            self.assertNotIn(canary, combined)
        self.assertEqual(list(self.plugin_data.rglob("*.jsonl")), [])

    def test_same_call_id_with_conflicting_inputs_fails_open_without_poisoning(self) -> None:
        first = fixture("pre_tool_use.json")
        second = fixture("pre_tool_use.json")
        first["tool_input"] = {"query": "first strategy"}
        second["tool_input"] = {"query": "second strategy"}

        results = self.dispatch_many([first, second])

        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertEqual(sorted(result.stdout for result in results), ["", ""])
        self.assertEqual(
            sorted(result.stderr for result in results),
            ["", "agent-call-governor hook unavailable: ValueError\n"],
        )
        self.assertEqual(
            [event.event_type for event in CallLedger(self.db).events()],
            ["call.proposed", "policy.decided", "call.started"],
        )

    def test_two_processes_compete_for_one_policy_budget_slot(self) -> None:
        for index in range(5):
            started = fixture("pre_tool_use.json")
            completed = fixture("post_tool_use.json")
            call_id = f"seed-{index}"
            inputs = {"command": f"echo seed-{index}"}
            started["tool_use_id"] = completed["tool_use_id"] = call_id
            started["tool_input"] = completed["tool_input"] = inputs
            self.assertEqual(self.dispatch(started).stderr, "")
            self.assertEqual(self.dispatch(completed).stderr, "")
        self.assertEqual(
            len(
                [
                    event
                    for event in CallLedger(self.db).events()
                    if event.event_type == "call.completed"
                ]
            ),
            5,
        )

        first = fixture("pre_tool_use.json")
        second = fixture("pre_tool_use.json")
        first["tool_use_id"], first["tool_input"] = "call-a", {"command": "git status"}
        second["tool_use_id"], second["tool_input"] = "call-b", {"command": "git diff"}

        results = self.dispatch_many([first, second], mode="warn")

        self.assertTrue(all(result.returncode == 0 for result in results))
        self.assertEqual([result.stderr for result in results], ["", ""])
        raw_outputs = [result.stdout for result in results]
        self.assertEqual(raw_outputs.count(""), 1)
        warning_outputs = [output for output in raw_outputs if output]
        self.assertEqual(len(warning_outputs), 1)
        warning = json.loads(warning_outputs[0])
        self.assertIsInstance(warning, dict)
        self.assertEqual(set(warning), {"systemMessage"})
        self.assertIsInstance(warning["systemMessage"], str)
        decisions = [
            event
            for event in CallLedger(self.db).events()
            if event.event_type == "policy.decided"
        ][-2:]
        self.assertEqual(
            sorted((event.decision, event.reason_code, event.budget_before) for event in decisions),
            [
                ("allow", "allowed", 1),
                ("would_block", "budget_exhausted", 0),
            ],
        )
        self.assertEqual(
            len([event for event in CallLedger(self.db).events() if event.event_type == "call.started"]),
            7,
            "warn mode starts both raced calls; this proves policy-slot atomicity only",
        )

    def test_exclusive_database_lock_returns_zero_within_three_seconds(self) -> None:
        locker = sqlite3.connect(self.db)
        try:
            locker.execute("BEGIN EXCLUSIVE")
            started = time.monotonic()
            result = self.dispatch(fixture("pre_tool_use.json"))
            elapsed = time.monotonic() - started
        finally:
            locker.rollback()
            locker.close()

        self.assertEqual(result.returncode, 0)
        self.assertLess(elapsed, 3.0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "agent-call-governor hook unavailable: OperationalError\n",
        )

    def test_eight_terminal_deliveries_append_one_terminal_and_progress(self) -> None:
        self.assertEqual(self.dispatch(fixture("pre_tool_use.json")).stderr, "")
        completed = fixture("post_tool_use.json")
        completed["tool_response"] = {"test_status": "passed"}

        results = self.dispatch_many([completed] * 8)

        self.assert_quiet_success(results)
        self.assertEqual(
            [event.event_type for event in CallLedger(self.db).events()],
            [
                "call.proposed",
                "policy.decided",
                "call.started",
                "call.completed",
                "progress.observed",
            ],
        )

    def test_eight_stop_with_turn_deliveries_append_once(self) -> None:
        results = self.dispatch_many([fixture("stop.json")] * 8)

        self.assert_quiet_success(results)
        events = CallLedger(self.db).events()
        self.assertEqual([event.event_type for event in events], ["session.stopped"])
        self.assertEqual(list(self.plugin_data.rglob("*.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
