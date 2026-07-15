from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from agent_call_governor_runtime import CallLedger, GovernedRuntime, evaluate
from agent_call_governor_runtime.codex_hook import (
    deterministic_progress,
    handle_codex_hook,
    host_reference,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "codex-hooks"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def scope_id(turn_id: str) -> str:
    session_ref = hashlib.sha256(b"session-1").hexdigest()
    turn_ref = hashlib.sha256(turn_id.encode("utf-8")).hexdigest()
    return f"codex:trace:{session_ref}:turn:{turn_ref}"


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
        self.assertEqual(events[-1].progress, "unknown")
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
                "progress": "unknown",
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
        self.assertEqual(event.progress, "unknown")
        self.assertNotIn("secret service failure", json.dumps(event.to_dict()))

    def test_unknown_event_is_ignored(self) -> None:
        self.assertIsNone(handle_codex_hook({
            "hook_event_name": "UnrelatedHook",
            "session_id": "session-1",
        }, self.runtime()))
        self.assertEqual(self.ledger.events(), [])

    def test_enforce_mode_is_rejected_by_observe_warn_adapter_contract(self) -> None:
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

    def test_all_six_events_record_canonical_lifecycle(self) -> None:
        for fixture in (
            "session_start.json",
            "pre_tool_use.json",
            "post_tool_use.json",
            "subagent_start.json",
            "subagent_stop.json",
            "stop.json",
        ):
            self.assertIsNone(handle_codex_hook(load_fixture(fixture), self.runtime()))

        events = self.ledger.events()
        self.assertEqual(
            [event.event_type for event in events],
            [
                "session.started",
                "call.proposed",
                "policy.decided",
                "call.started",
                "call.completed",
                "call.proposed",
                "policy.decided",
                "call.started",
                "call.completed",
                "session.stopped",
            ],
        )
        self.assertEqual(
            [event.progress for event in events if event.event_type == "call.completed"],
            ["unknown", "unknown"],
        )
        trace_ids = {event.trace_id for event in events}
        self.assertEqual(
            trace_ids,
            {host_reference("private-session-id"), host_reference("session-1")},
        )
        self.assertTrue(all(event.source == "codex-hook" for event in events))
        self.assertEqual(
            [event.source_event for event in events],
            [
                "SessionStart",
                "PreToolUse",
                "PreToolUse",
                "PreToolUse",
                "PostToolUse",
                "SubagentStart",
                "SubagentStart",
                "SubagentStart",
                "SubagentStop",
                "Stop",
            ],
        )
        for event in events:
            self.assertEqual(event.status, {
                "session.started": "started",
                "call.proposed": "proposed",
                "policy.decided": "decided",
                "call.started": "running",
                "call.completed": "completed",
                "session.stopped": "stopped",
            }[event.event_type])
        terminal = [event for event in events if event.event_type == "call.completed"]
        self.assertEqual(
            [event.turn_id for event in terminal],
            [host_reference("turn-1"), host_reference("turn-2")],
        )
        self.assertEqual(
            [event.span_id for event in terminal],
            [
                host_reference("tool-use:tool-call-1"),
                host_reference("agent:agent-call-1"),
            ],
        )
        self.assertTrue(all(event.policy_allowed is None for event in terminal))
        self.assertTrue(all(event.execution_allowed is None for event in terminal))
        self.assertTrue(all(event.decision_reason is None for event in terminal))

    def test_session_start_deduplicates_only_inside_five_second_window(self) -> None:
        payload = load_fixture("session_start.json")
        handle_codex_hook(payload, self.runtime(), observed_at="2026-07-14T00:00:00Z")
        handle_codex_hook(payload, self.runtime(), observed_at="2026-07-14T00:00:04Z")
        handle_codex_hook(payload, self.runtime(), observed_at="2026-07-14T00:00:06Z")

        starts = [event for event in self.ledger.events() if event.event_type == "session.started"]
        self.assertEqual(len(starts), 2)
        self.assertNotEqual(starts[0].call_id, starts[1].call_id)
        self.assertNotEqual(starts[0].span_id, starts[1].span_id)
        self.assertEqual(
            [event.observed_at for event in starts],
            ["2026-07-14T00:00:00Z", "2026-07-14T00:00:06Z"],
        )

    def test_session_start_only_collapses_identical_delivery_digest(self) -> None:
        first = load_fixture("session_start.json")
        changed = load_fixture("session_start.json")
        changed["source"] = "resume"
        handle_codex_hook(first, self.runtime(), observed_at="2026-07-14T00:00:00Z")
        handle_codex_hook(changed, self.runtime(), observed_at="2026-07-14T00:00:01Z")

        self.assertEqual(
            len([event for event in self.ledger.events() if event.event_type == "session.started"]),
            2,
        )

    def test_stop_with_turn_is_permanent_and_without_turn_is_bounded(self) -> None:
        with_turn = load_fixture("stop.json")
        handle_codex_hook(with_turn, self.runtime(), observed_at="2026-07-14T00:00:00Z")
        handle_codex_hook(with_turn, self.runtime(), observed_at="2027-07-14T00:00:00Z")

        without_turn = load_fixture("stop.json")
        without_turn.pop("turn_id")
        for observed_at in (
            "2026-07-14T00:00:00Z",
            "2026-07-14T00:00:04Z",
            "2026-07-14T00:00:06Z",
        ):
            handle_codex_hook(without_turn, self.runtime(), observed_at=observed_at)

        stops = [event for event in self.ledger.events() if event.event_type == "session.stopped"]
        self.assertEqual(len(stops), 3)
        self.assertEqual(stops[0].turn_id, host_reference("private-turn-id"))
        self.assertEqual(stops[1].turn_id, None)
        self.assertEqual(stops[2].turn_id, None)
        self.assertNotEqual(stops[1].call_id, stops[2].call_id)

    def test_missing_turn_preserves_call_local_scope_and_null_turn(self) -> None:
        payload = load_fixture("pre_tool_use.json")
        payload.pop("turn_id")
        handle_codex_hook(payload, self.runtime())

        events = self.ledger.events()
        self.assertEqual(len(events), 3)
        self.assertTrue(all(event.turn_id is None for event in events))
        self.assertTrue(all(event.session_id.startswith("codex:trace:") for event in events))
        self.assertTrue(all(":call:" in event.session_id for event in events))

    def test_same_host_id_with_different_fingerprint_is_rejected(self) -> None:
        payload = load_fixture("pre_tool_use.json")
        handle_codex_hook(payload, self.runtime())
        changed = load_fixture("pre_tool_use.json")
        changed["tool_input"] = {"query": "different secret"}

        with self.assertRaisesRegex(ValueError, "different material inputs"):
            handle_codex_hook(changed, self.runtime())

    def test_subagent_task_digest_and_parent_are_private(self) -> None:
        payload = load_fixture("subagent_start.json")
        payload["task"] = "PRIVATE_DELEGATED_TASK"
        payload["parent_agent_id"] = "PRIVATE_PARENT_AGENT"
        handle_codex_hook(payload, self.runtime())

        events = self.ledger.events(scope_id("turn-2"))
        self.assertEqual(
            events[0].parent_span_id,
            host_reference("agent:PRIVATE_PARENT_AGENT"),
        )
        serialized = json.dumps([event.to_dict() for event in events], sort_keys=True)
        self.assertNotIn("PRIVATE_DELEGATED_TASK", serialized)
        self.assertNotIn("PRIVATE_PARENT_AGENT", serialized)
        self.assertNotIn("agent-call-1", serialized)

    def test_subagent_without_task_uses_opaque_invocation_digest(self) -> None:
        first = load_fixture("subagent_start.json")
        second = load_fixture("subagent_start.json")
        second["agent_id"] = "agent-call-2"
        handle_codex_hook(first, self.runtime())
        handle_codex_hook(second, self.runtime())

        proposed = [event for event in self.ledger.events() if event.event_type == "call.proposed"]
        self.assertEqual(len(proposed), 2)
        self.assertNotEqual(proposed[0].fingerprint, proposed[1].fingerprint)

    def test_repeated_subagent_host_id_replays_or_rejects_changed_task(self) -> None:
        payload = load_fixture("subagent_start.json")
        payload["task"] = "first private task"
        handle_codex_hook(payload, self.runtime())
        handle_codex_hook(payload, self.runtime())
        self.assertEqual(len(self.ledger.events(scope_id("turn-2"))), 3)

        changed = load_fixture("subagent_start.json")
        changed["task"] = "second private task"
        with self.assertRaisesRegex(ValueError, "different material inputs"):
            handle_codex_hook(changed, self.runtime())

    def test_progress_signals_and_precedence(self) -> None:
        cases = [
            ({"acceptance_criterion_status": "satisfied"}, "sufficient", "acceptance_criterion_status"),
            ({"file_changed": True}, "material_progress", "file_changed"),
            ({"result_digest_changed": True}, "material_progress", "result_digest_changed"),
            ({"new_unique_source_count": 1}, "material_progress", "new_unique_source_count"),
            ({"test_status": "passed"}, "material_progress", "test_status"),
            ({"exit_code": 0}, "material_progress", "exit_code"),
            ({"exit_code": 9}, "low_progress", "exit_code"),
        ]
        for signal, expected_progress, expected_key in cases:
            with self.subTest(signal=signal):
                top_level = deterministic_progress(signal)
                nested = deterministic_progress({"tool_response": signal})
                self.assertEqual(top_level, nested)
                self.assertIsNotNone(top_level)
                progress, metadata = top_level
                self.assertEqual(progress, expected_progress)
                self.assertEqual(set(metadata), {expected_key})

        conflict = deterministic_progress({
            "exit_code": 8,
            "tool_response": {
                "file_changed": True,
                "acceptance_criterion_status": "satisfied",
            },
        })
        self.assertEqual(conflict, ("sufficient", {"acceptance_criterion_status": "satisfied"}))

    def test_malformed_progress_signal_types_are_ignored(self) -> None:
        malformed = {
            "acceptance_criterion_status": True,
            "file_changed": 1,
            "result_digest_changed": "true",
            "new_unique_source_count": True,
            "test_status": ["passed"],
            "exit_code": False,
            "tool_response": "raw output",
        }
        self.assertIsNone(deterministic_progress(malformed))

    def test_deterministic_progress_is_appended_once_with_terminal(self) -> None:
        handle_codex_hook(load_fixture("pre_tool_use.json"), self.runtime())
        payload = load_fixture("post_tool_use.json")
        payload["tool_response"]["file_changed"] = True
        handle_codex_hook(payload, self.runtime())
        handle_codex_hook(payload, self.runtime())

        events = self.ledger.events()
        self.assertEqual(
            [event.event_type for event in events[-2:]],
            ["call.completed", "progress.observed"],
        )
        self.assertEqual(events[-2].progress, "material_progress")
        self.assertEqual(events[-1].progress, "material_progress")
        self.assertEqual(dict(events[-1].metadata), {"file_changed": True})

    def test_start_replay_after_terminal_and_progress_observation_is_stable(self) -> None:
        start = load_fixture("pre_tool_use.json")
        terminal = load_fixture("post_tool_use.json")
        terminal["tool_response"]["file_changed"] = True
        evaluations: list[dict[str, object]] = []

        def evaluator(document):
            evaluations.append(document)
            return evaluate(document)

        runtime = GovernedRuntime(
            self.ledger,
            source="codex-hook",
            policy_evaluator=evaluator,
            warning_handler=lambda _message: None,
        )
        handle_codex_hook(start, runtime)
        handle_codex_hook(terminal, runtime)

        self.assertIsNone(handle_codex_hook(start, runtime))
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(
            [event.event_type for event in self.ledger.events(scope_id("turn-1"))],
            [
                "call.proposed",
                "policy.decided",
                "call.started",
                "call.completed",
                "progress.observed",
            ],
        )

    def canonical_terminal_pair(self):
        ledger = CallLedger(Path(self.tempdir.name) / "canonical-pair.sqlite3")
        runtime = GovernedRuntime(
            ledger,
            source="codex-hook",
            warning_handler=lambda _message: None,
        )
        handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)
        payload = load_fixture("post_tool_use.json")
        payload["tool_response"]["file_changed"] = True
        handle_codex_hook(payload, runtime)
        events = ledger.events(scope_id("turn-1"))
        return events[-2], events[-1], events[0].policy_facts

    def started_ledger(self, name: str):
        ledger = CallLedger(Path(self.tempdir.name) / f"{name}.sqlite3")
        runtime = GovernedRuntime(
            ledger,
            source="codex-hook",
            warning_handler=lambda _message: None,
        )
        handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)
        return ledger, runtime

    def test_host_reference_hashes_exact_utf8_bytes(self) -> None:
        self.assertNotEqual(host_reference("id"), host_reference(" id "))
        self.assertEqual(
            host_reference(" id "),
            "sha256:" + hashlib.sha256(" id ".encode("utf-8")).hexdigest(),
        )
        with self.assertRaises(ValueError):
            host_reference(" \t\r\n ")

    def test_session_and_turn_identifiers_preserve_surrounding_whitespace(self) -> None:
        normal = load_fixture("pre_tool_use.json")
        spaced_session = load_fixture("pre_tool_use.json")
        spaced_session["session_id"] = " session-1 "
        spaced_session["tool_use_id"] = "tool-call-session-space"
        spaced_turn = load_fixture("pre_tool_use.json")
        spaced_turn["turn_id"] = " turn-1 "
        spaced_turn["tool_use_id"] = "tool-call-turn-space"

        for payload in (normal, spaced_session, spaced_turn):
            handle_codex_hook(payload, self.runtime())

        proposed = [
            event for event in self.ledger.events() if event.event_type == "call.proposed"
        ]
        self.assertEqual(len(proposed), 3)
        self.assertEqual(
            {event.trace_id for event in proposed},
            {host_reference("session-1"), host_reference(" session-1 ")},
        )
        self.assertEqual(
            {event.turn_id for event in proposed},
            {host_reference("turn-1"), host_reference(" turn-1 ")},
        )

    def test_tool_agent_parent_and_task_identity_preserve_whitespace(self) -> None:
        first_tool = load_fixture("pre_tool_use.json")
        first_tool["tool_use_id"] = "opaque-id"
        first_tool["parent_tool_use_id"] = "parent-id"
        second_tool = load_fixture("pre_tool_use.json")
        second_tool["tool_use_id"] = " opaque-id "
        second_tool["parent_tool_use_id"] = " parent-id "
        handle_codex_hook(first_tool, self.runtime())
        handle_codex_hook(second_tool, self.runtime())

        tools = [
            event for event in self.ledger.events(scope_id("turn-1"))
            if event.event_type == "call.proposed"
        ]
        self.assertEqual(
            {event.call_id for event in tools},
            {
                host_reference("tool-use:opaque-id"),
                host_reference("tool-use: opaque-id "),
            },
        )
        self.assertEqual(
            {event.parent_span_id for event in tools},
            {
                host_reference("tool-use:parent-id"),
                host_reference("tool-use: parent-id "),
            },
        )

        first_agent = load_fixture("subagent_start.json")
        first_agent["agent_id"] = "opaque-id"
        first_agent["task"] = "delegated task"
        second_agent = load_fixture("subagent_start.json")
        second_agent["agent_id"] = " opaque-id "
        second_agent["task"] = "delegated task"
        handle_codex_hook(first_agent, self.runtime())
        handle_codex_hook(second_agent, self.runtime())
        agents = [
            event for event in self.ledger.events(scope_id("turn-2"))
            if event.event_type == "call.proposed"
        ]
        self.assertEqual(
            {event.call_id for event in agents},
            {
                host_reference("agent:opaque-id"),
                host_reference("agent: opaque-id "),
            },
        )

        changed_task = load_fixture("subagent_start.json")
        changed_task["agent_id"] = "opaque-id"
        changed_task["task"] = " delegated task "
        with self.assertRaisesRegex(ValueError, "different material inputs"):
            handle_codex_hook(changed_task, self.runtime())

    def test_terminal_must_match_the_stored_open_lifecycle(self) -> None:
        terminal, _, _ = self.canonical_terminal_pair()
        corruptions = {
            "trace_id": {"trace_id": host_reference("different-trace")},
            "turn_id": {"turn_id": host_reference("different-turn")},
            "span_id": {"span_id": host_reference("different-span")},
            "parent_identity": {
                "parent_call_id": host_reference("different-parent"),
                "parent_span_id": host_reference("different-parent"),
            },
            "fingerprint": {"fingerprint": host_reference("different-fingerprint")},
            "objective": {"objective": host_reference("different-objective")},
            "route": {"route": "different-route"},
            "budget_kind": {"budget_kind": "agent"},
            "profile": {"profile": "strict"},
            "quality_risk": {"quality_risk": "high"},
            "mode": {"mode": "warn"},
            "failure_policy": {"failure_policy": "fail-closed"},
            "input_digest": {"input_digest": host_reference("different-input")},
            "agent_id": {"agent_id": "different-agent"},
            "tool_name": {"tool_name": "different-tool"},
        }
        for index, (label, values) in enumerate(corruptions.items()):
            with self.subTest(label=label):
                ledger, runtime = self.started_ledger(f"terminal-{index}")
                with self.assertRaises(ValueError):
                    ledger.append_terminal_with_progress_if_open(
                        replace(terminal, **values),
                        None,
                    )
                self.assertEqual(len(ledger.events(scope_id("turn-1"))), 3)
                self.assertIsNone(
                    handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)
                )

    def test_progress_companion_requires_exact_safe_terminal_relation(self) -> None:
        terminal, progress, policy_facts = self.canonical_terminal_pair()
        corruptions = {
            "session_id": {"session_id": "different-session"},
            "trace_id": {"trace_id": host_reference("different-trace")},
            "turn_id": {"turn_id": host_reference("different-turn")},
            "fingerprint": {"fingerprint": host_reference("different-fingerprint")},
            "objective": {"objective": host_reference("different-objective")},
            "route": {"route": "different-route"},
            "budget_kind": {"budget_kind": "agent"},
            "profile": {"profile": "strict"},
            "quality_risk": {"quality_risk": "high"},
            "mode": {"mode": "warn"},
            "failure_policy": {"failure_policy": "fail-closed"},
            "input_digest": {"input_digest": host_reference("different-input")},
            "source": {"source": "different-source"},
            "source_event": {"source_event": "different-event"},
            "occurred_at": {"occurred_at": "2026-07-14T00:00:01Z"},
            "observed_at": {"observed_at": "2026-07-14T00:00:01Z"},
            "progress": {"progress": "low_progress"},
            "metadata": {"metadata": {"exit_code": 0}},
            "agent_id": {"agent_id": "different-agent"},
            "tool_name": {"tool_name": "different-tool"},
            "terminal_identity": {
                "call_id": terminal.call_id,
                "span_id": terminal.span_id,
            },
            "split_identity": {
                "call_id": host_reference("progress-call"),
                "span_id": host_reference("progress-span"),
            },
            "parent_call_id": {"parent_call_id": host_reference("different-parent")},
            "parent_span_id": {"parent_span_id": host_reference("different-parent")},
            "phase": {"phase": "started"},
            "status": {"status": "running"},
            "decision": {"decision": "allow"},
            "error_type": {"error_type": "UnexpectedError"},
            "duration_ms": {"duration_ms": 1.0},
            "execution_latency_ms": {"execution_latency_ms": 1.0},
            "token_metrics": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
            "cost_metrics": {
                "estimated_cost_usd": 0.01,
                "pricing_version": "test-v1",
            },
            "policy_facts": {"policy_facts": policy_facts},
        }
        for index, (label, values) in enumerate(corruptions.items()):
            with self.subTest(label=label):
                ledger, runtime = self.started_ledger(f"progress-{index}")
                with self.assertRaises(ValueError):
                    ledger.append_terminal_with_progress_if_open(
                        terminal,
                        replace(progress, **values),
                    )
                self.assertEqual(len(ledger.events(scope_id("turn-1"))), 3)
                self.assertIsNone(
                    handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)
                )

    def test_progress_companion_cannot_reuse_another_call_identity(self) -> None:
        terminal, progress, _ = self.canonical_terminal_pair()
        ledger, runtime = self.started_ledger("progress-existing-call")
        other = load_fixture("pre_tool_use.json")
        other["tool_use_id"] = "other-call"
        handle_codex_hook(other, runtime)
        other_call_id = host_reference("tool-use:other-call")

        with self.assertRaises(ValueError):
            ledger.append_terminal_with_progress_if_open(
                terminal,
                replace(progress, call_id=other_call_id, span_id=other_call_id),
            )

        self.assertEqual(len(ledger.events(scope_id("turn-1"))), 6)
        self.assertIsNone(handle_codex_hook(load_fixture("pre_tool_use.json"), runtime))

    def test_tool_and_agent_host_ids_are_namespaced_within_one_turn(self) -> None:
        tool = load_fixture("pre_tool_use.json")
        agent = load_fixture("subagent_start.json")
        agent["session_id"] = tool["session_id"]
        agent["turn_id"] = tool["turn_id"]
        agent["agent_id"] = tool["tool_use_id"]
        handle_codex_hook(tool, self.runtime())
        handle_codex_hook(agent, self.runtime())

        tool_terminal = load_fixture("post_tool_use.json")
        agent_terminal = load_fixture("subagent_stop.json")
        agent_terminal["session_id"] = tool["session_id"]
        agent_terminal["turn_id"] = tool["turn_id"]
        agent_terminal["agent_id"] = tool["tool_use_id"]
        handle_codex_hook(tool_terminal, self.runtime())
        handle_codex_hook(agent_terminal, self.runtime())

        proposed = [
            event
            for event in self.ledger.events(scope_id("turn-1"))
            if event.event_type == "call.proposed"
        ]
        self.assertEqual(len(proposed), 2)
        self.assertNotEqual(proposed[0].call_id, proposed[1].call_id)
        self.assertEqual(
            {event.call_id for event in proposed},
            {
                host_reference("tool-use:tool-call-1"),
                host_reference("agent:tool-call-1"),
            },
        )
        completed = [
            event
            for event in self.ledger.events(scope_id("turn-1"))
            if event.event_type == "call.completed"
        ]
        self.assertEqual(len(completed), 2)
        self.assertEqual(
            {event.call_id for event in completed},
            {event.call_id for event in proposed},
        )
        self.assertEqual({event.budget_kind for event in completed}, {"direct-tool", "agent"})

    def test_maintenance_failures_are_independent_and_do_not_suppress_events(self) -> None:
        payload = load_fixture("session_start.json")
        with mock.patch.object(
            self.ledger, "prune_expired_sessions", side_effect=RuntimeError("PRIVATE_RETENTION")
        ) as prune, mock.patch.object(
            self.ledger, "recover_stale_reservations", side_effect=RuntimeError("PRIVATE_RECOVERY")
        ) as recover:
            self.assertIsNone(handle_codex_hook(payload, self.runtime()))
        prune.assert_called_once()
        recover.assert_called_once()
        self.assertEqual([event.event_type for event in self.ledger.events()], ["session.started"])

        with mock.patch.object(
            self.ledger, "prune_expired_sessions", side_effect=RuntimeError("PRIVATE_STOP")
        ):
            self.assertIsNone(handle_codex_hook(load_fixture("stop.json"), self.runtime()))
        self.assertEqual(
            [event.event_type for event in self.ledger.events()],
            ["session.started", "session.stopped"],
        )

    def test_raw_host_values_never_persist_in_sqlite_wal_or_event_json(self) -> None:
        canaries = {
            "session": "RAW_SESSION_CANARY",
            "turn": "RAW_TURN_CANARY",
            "tool": "RAW_TOOL_ID_CANARY",
            "agent": "RAW_AGENT_ID_CANARY",
            "parent": "RAW_PARENT_CANARY",
            "task": "RAW_TASK_CANARY",
            "prompt": "RAW_PROMPT_CANARY",
            "input": "RAW_INPUT_CANARY",
            "output": "RAW_OUTPUT_CANARY",
            "error": "RAW_ERROR_CANARY",
        }
        start = load_fixture("pre_tool_use.json")
        start.update({
            "session_id": canaries["session"],
            "turn_id": canaries["turn"],
            "tool_use_id": canaries["tool"],
            "parent_tool_use_id": canaries["parent"],
            "tool_input": {"input": canaries["input"]},
        })
        stop = load_fixture("post_tool_use.json")
        stop.update({
            "session_id": canaries["session"],
            "turn_id": canaries["turn"],
            "tool_use_id": canaries["tool"],
            "tool_input": {"input": canaries["input"]},
            "tool_response": {"output": canaries["output"]},
            "error": canaries["error"],
            "is_error": True,
        })
        handle_codex_hook(start, self.runtime())
        hold = sqlite3.connect(self.ledger.sqlite_path)
        try:
            hold.execute("BEGIN")
            hold.execute("SELECT COUNT(*) FROM call_events").fetchone()
            handle_codex_hook(stop, self.runtime())
            agent_start = load_fixture("subagent_start.json")
            agent_start.update({
                "session_id": canaries["session"],
                "turn_id": canaries["turn"],
                "agent_id": canaries["agent"],
                "parent_agent_id": canaries["parent"],
                "task": canaries["task"],
                "prompt": canaries["prompt"],
            })
            agent_stop = load_fixture("subagent_stop.json")
            agent_stop.update({
                "session_id": canaries["session"],
                "turn_id": canaries["turn"],
                "agent_id": canaries["agent"],
                "last_assistant_message": canaries["output"],
                "error": canaries["error"],
            })
            handle_codex_hook(agent_start, self.runtime())
            handle_codex_hook(agent_stop, self.runtime())
            event_json = json.dumps([event.to_dict() for event in self.ledger.events()])
            wal = Path(str(self.ledger.sqlite_path) + "-wal")
            shm = Path(str(self.ledger.sqlite_path) + "-shm")
            self.assertTrue(wal.is_file())
            self.assertTrue(shm.is_file())
            storage = b"".join(
                path.read_bytes()
                for path in (
                    self.ledger.sqlite_path,
                    wal,
                    shm,
                )
                if path.exists()
            ).decode("utf-8", errors="ignore")
            storage += (Path(self.tempdir.name) / "events.jsonl").read_text(
                encoding="utf-8"
            )
        finally:
            hold.rollback()
            hold.close()
        for canary in canaries.values():
            self.assertNotIn(canary, event_json)
            self.assertNotIn(canary, storage)


if __name__ == "__main__":
    unittest.main()
