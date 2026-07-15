from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_call_governor_runtime import CallLedger, GovernedRuntime
from agent_call_governor_runtime.codex_hook import handle_codex_hook


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "codex-hooks"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def scope_id(turn_id: str) -> str:
    session_ref = hashlib.sha256(b"session-1").hexdigest()
    turn_ref = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
    return f"codex:session:{session_ref}:turn:{turn_ref}"


class CodexHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.ledger = CallLedger(root / "events.sqlite3", root / "events.jsonl")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def runtime(self, *, mode: str = "observe") -> GovernedRuntime:
        return GovernedRuntime(
            self.ledger,
            mode=mode,
            source="codex-hook",
            warning_handler=lambda _message: None,
        )

    def test_pre_and_post_tool_use_record_lifecycle_without_raw_payloads(self) -> None:
        runtime = self.runtime()

        self.assertIsNone(handle_codex_hook(load_fixture("pre_tool_use.json"), runtime))
        self.assertIsNone(handle_codex_hook(load_fixture("post_tool_use.json"), runtime))

        events = self.ledger.events(scope_id("turn-1"))
        self.assertEqual(
            [event.event_type for event in events],
            ["call.proposed", "policy.decided", "call.started", "call.completed"],
        )
        self.assertEqual(events[-1].progress, "material_progress")
        self.assertEqual(events[-1].budget_kind, "direct-tool")
        persisted = (Path(self.tempdir.name) / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("secret customer question", persisted)
        self.assertNotIn("secret customer answer", persisted)
        self.assertNotIn("session-1", persisted)
        self.assertNotIn("turn-1", persisted)

    def test_warn_returns_only_supported_system_message(self) -> None:
        runtime = self.runtime(mode="warn")
        handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)
        duplicate = load_fixture("pre_tool_use.json")
        duplicate["tool_use_id"] = "tool-call-2"

        output = handle_codex_hook(duplicate, runtime)

        self.assertIsInstance(output, dict)
        self.assertIn("systemMessage", output)
        self.assertNotIn("continue", output)
        self.assertNotIn("stopReason", output)
        self.assertNotIn("suppressOutput", output)

    def test_replayed_start_delivery_is_idempotent(self) -> None:
        runtime = self.runtime(mode="warn")
        payload = load_fixture("pre_tool_use.json")

        self.assertIsNone(handle_codex_hook(payload, runtime))
        self.assertIsNone(handle_codex_hook(payload, runtime))

        self.assertEqual(
            [event.event_type for event in self.ledger.events(scope_id("turn-1"))],
            ["call.proposed", "policy.decided", "call.started"],
        )

    def test_concurrent_replayed_start_delivery_is_idempotent(self) -> None:
        barrier = threading.Barrier(2)

        class CoordinatedLedger(CallLedger):
            def __init__(inner_self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                inner_self.prechecks = 0
                inner_self.lock = threading.Lock()

            def latest_event(inner_self, session_id, call_id):
                event = super().latest_event(session_id, call_id)
                with inner_self.lock:
                    should_wait = event is None and inner_self.prechecks < 2
                    if should_wait:
                        inner_self.prechecks += 1
                if should_wait:
                    barrier.wait(timeout=5)
                return event

        ledger = CoordinatedLedger(Path(self.tempdir.name) / "concurrent-start.sqlite3")
        runtime = GovernedRuntime(ledger, mode="warn", warning_handler=lambda _message: None)
        payload = load_fixture("pre_tool_use.json")

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [
                future.result(timeout=10)
                for future in [
                    executor.submit(handle_codex_hook, payload, runtime),
                    executor.submit(handle_codex_hook, payload, runtime),
                ]
            ]

        self.assertEqual(outcomes, [None, None])
        self.assertEqual(
            [event.event_type for event in ledger.events(scope_id("turn-1"))],
            ["call.proposed", "policy.decided", "call.started"],
        )

    def test_concurrent_replayed_terminal_delivery_is_idempotent(self) -> None:
        db_path = Path(self.tempdir.name) / "concurrent-terminal.sqlite3"
        initial = CallLedger(db_path)
        handle_codex_hook(load_fixture("pre_tool_use.json"), GovernedRuntime(initial))
        barrier = threading.Barrier(2)

        class CoordinatedLedger(CallLedger):
            def __init__(inner_self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                inner_self.prechecks = 0
                inner_self.lock = threading.Lock()

            def latest_event(inner_self, session_id, call_id):
                event = super().latest_event(session_id, call_id)
                with inner_self.lock:
                    should_wait = event is not None and event.phase == "started" and inner_self.prechecks < 2
                    if should_wait:
                        inner_self.prechecks += 1
                if should_wait:
                    barrier.wait(timeout=5)
                return event

        ledger = CoordinatedLedger(db_path)
        runtime = GovernedRuntime(ledger)
        payload = load_fixture("post_tool_use.json")

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [
                future.result(timeout=10)
                for future in [
                    executor.submit(handle_codex_hook, payload, runtime),
                    executor.submit(handle_codex_hook, payload, runtime),
                ]
            ]

        self.assertEqual(outcomes, [None, None])
        self.assertEqual(
            [event.event_type for event in ledger.events(scope_id("turn-1"))],
            ["call.proposed", "policy.decided", "call.started", "call.completed"],
        )

    def test_policy_scope_resets_for_a_new_codex_turn(self) -> None:
        runtime = self.runtime(mode="warn")
        handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)
        handle_codex_hook(load_fixture("post_tool_use.json"), runtime)
        next_turn = load_fixture("pre_tool_use.json")
        next_turn["turn_id"] = "turn-99"
        next_turn["tool_use_id"] = "tool-call-99"

        self.assertIsNone(handle_codex_hook(next_turn, runtime))
        self.assertEqual(len(self.ledger.history(scope_id("turn-1"))), 1)
        self.assertEqual(len(self.ledger.history(scope_id("turn-99"))), 1)

    def test_subagent_stop_closes_started_call(self) -> None:
        runtime = self.runtime()

        handle_codex_hook(load_fixture("subagent_start.json"), runtime)
        handle_codex_hook(load_fixture("subagent_stop.json"), runtime)

        history = self.ledger.history(scope_id("turn-2"))
        self.assertEqual(history, [
            {
                "fingerprint": self.ledger.events(scope_id("turn-2"))[0].fingerprint,
                "fingerprint_version": 2,
                "budget_kind": "agent",
                "progress": "material_progress",
            }
        ])
        persisted = (Path(self.tempdir.name) / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("secret review output", persisted)
        self.assertNotIn("agent-transcript", persisted)

    def test_same_type_subagents_with_distinct_host_ids_are_not_false_duplicates(self) -> None:
        runtime = self.runtime(mode="warn")
        first = load_fixture("subagent_start.json")
        second = load_fixture("subagent_start.json")
        second["agent_id"] = "agent-call-2"

        self.assertIsNone(handle_codex_hook(first, runtime))
        self.assertIsNone(handle_codex_hook(second, runtime))

        started = [
            event
            for event in self.ledger.events(scope_id("turn-2"))
            if event.phase == "started"
        ]
        self.assertEqual(len(started), 2)
        self.assertNotEqual(started[0].fingerprint, started[1].fingerprint)

    def test_failed_tool_records_type_without_error_message(self) -> None:
        runtime = self.runtime()
        payload = load_fixture("post_tool_use.json")
        payload["is_error"] = True
        payload["error"] = "secret service failure"

        handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)
        handle_codex_hook(payload, runtime)

        event = self.ledger.events(scope_id("turn-1"))[-1]
        self.assertEqual(event.phase, "failed")
        self.assertEqual(event.error_type, "CodexToolError")
        self.assertEqual(event.progress, "low_progress")
        self.assertNotIn("secret service failure", json.dumps(event.to_dict()))

    def test_unknown_event_is_ignored(self) -> None:
        self.assertIsNone(handle_codex_hook({
            "hook_event_name": "SessionStart",
            "session_id": "session-1",
        }, self.runtime()))
        self.assertEqual(self.ledger.events(), [])

    def test_enforce_mode_is_rejected_because_codex_hooks_cannot_veto(self) -> None:
        with self.assertRaisesRegex(ValueError, "observe or warn"):
            handle_codex_hook(load_fixture("pre_tool_use.json"), self.runtime(mode="enforce"))

    def test_malformed_supported_event_fails_without_recording(self) -> None:
        with self.assertRaisesRegex(ValueError, "tool_name"):
            handle_codex_hook({
                "hook_event_name": "PreToolUse",
                "session_id": "session-1",
            }, self.runtime())
        self.assertEqual(self.ledger.events(), [])

    def test_cli_reads_stdin_and_emits_valid_warning_json(self) -> None:
        db_path = Path(self.tempdir.name) / "cli.sqlite3"
        script = ROOT / "skills" / "agent-call-governor" / "scripts" / "codex_hook.py"
        first_payload = load_fixture("pre_tool_use.json")
        second_payload = load_fixture("pre_tool_use.json")
        second_payload["tool_use_id"] = "tool-call-2"

        first = subprocess.run(
            [sys.executable, str(script), "--mode", "warn", "--db", str(db_path)],
            input=json.dumps(first_payload),
            text=True,
            capture_output=True,
            check=False,
        )
        second = subprocess.run(
            [sys.executable, str(script), "--mode", "warn", "--db", str(db_path)],
            input=json.dumps(second_payload),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "")
        self.assertEqual(second.returncode, 0, second.stderr)
        output = json.loads(second.stdout)
        self.assertIn("systemMessage", output)
        self.assertNotIn("continue", output)

    def test_cli_accepts_utf8_bom_from_windows_powershell_pipeline(self) -> None:
        db_path = Path(self.tempdir.name) / "bom-cli.sqlite3"
        script = ROOT / "skills" / "agent-call-governor" / "scripts" / "codex_hook.py"
        payload = json.dumps(load_fixture("pre_tool_use.json")).encode("utf-8")

        result = subprocess.run(
            [sys.executable, str(script), "--db", str(db_path)],
            input=b"\xef\xbb\xbf" + payload,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout, b"")
        self.assertEqual(len(CallLedger(db_path).events(scope_id("turn-1"))), 3)


if __name__ == "__main__":
    unittest.main()
