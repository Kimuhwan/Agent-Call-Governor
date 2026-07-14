from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_call_governor_runtime import CallLedger, GovernedRuntime
from agent_call_governor_runtime.codex_hook import handle_codex_hook


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "codex-hooks"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


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

        events = self.ledger.events("session-1")
        self.assertEqual([event.phase for event in events], ["proposed", "started", "completed"])
        self.assertEqual(events[-1].progress, "material_progress")
        self.assertEqual(events[-1].budget_kind, "direct-tool")
        persisted = (Path(self.tempdir.name) / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("secret customer question", persisted)
        self.assertNotIn("secret customer answer", persisted)

    def test_warn_returns_only_supported_system_message(self) -> None:
        runtime = self.runtime(mode="warn")
        handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)

        output = handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)

        self.assertIsInstance(output, dict)
        self.assertIn("systemMessage", output)
        self.assertNotIn("continue", output)
        self.assertNotIn("stopReason", output)
        self.assertNotIn("suppressOutput", output)

    def test_subagent_stop_closes_started_call(self) -> None:
        runtime = self.runtime()

        handle_codex_hook(load_fixture("subagent_start.json"), runtime)
        handle_codex_hook(load_fixture("subagent_stop.json"), runtime)

        history = self.ledger.history("session-1")
        self.assertEqual(history, [
            {
                "fingerprint": self.ledger.events("session-1")[0].fingerprint,
                "budget_kind": "agent",
                "progress": "material_progress",
            }
        ])
        persisted = (Path(self.tempdir.name) / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("secret review output", persisted)
        self.assertNotIn("agent-transcript", persisted)

    def test_failed_tool_records_type_without_error_message(self) -> None:
        runtime = self.runtime()
        payload = load_fixture("post_tool_use.json")
        payload["is_error"] = True
        payload["error"] = "secret service failure"

        handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)
        handle_codex_hook(payload, runtime)

        event = self.ledger.events("session-1")[-1]
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
        script = ROOT / "agent-call-governor" / "scripts" / "codex_hook.py"
        payload = json.dumps(load_fixture("pre_tool_use.json"))

        first = subprocess.run(
            [sys.executable, str(script), "--mode", "warn", "--db", str(db_path)],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
        )
        second = subprocess.run(
            [sys.executable, str(script), "--mode", "warn", "--db", str(db_path)],
            input=payload,
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
        script = ROOT / "agent-call-governor" / "scripts" / "codex_hook.py"
        payload = json.dumps(load_fixture("pre_tool_use.json")).encode("utf-8")

        result = subprocess.run(
            [sys.executable, str(script), "--db", str(db_path)],
            input=b"\xef\xbb\xbf" + payload,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout, b"")
        self.assertEqual(len(CallLedger(db_path).events("session-1")), 2)


if __name__ == "__main__":
    unittest.main()
