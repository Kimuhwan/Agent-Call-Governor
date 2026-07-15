from __future__ import annotations

import json
import io
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from agent_call_governor_runtime import CallLedger, SecureCleanupIncompleteError
from agent_call_governor_runtime.cli import (
    _sqlite_contract,
    build_parser,
    build_report,
    export_jsonl,
    main,
)
from tests.helpers import make_event
from tests.test_schema_migration import create_v02_database


ROOT = Path(__file__).resolve().parents[1]
TRACE_A = "sha256:" + "a" * 64
TRACE_B = "sha256:" + "b" * 64


class OperationalCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db = self.root / "plugin-data" / "events.sqlite3"
        self.ledger = CallLedger(self.db, busy_timeout_ms=1000)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_cli(
        self,
        *arguments: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        return subprocess.run(
            [sys.executable, "-m", "agent_call_governor_runtime", *arguments],
            cwd=ROOT,
            env=process_env,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )

    def append(
        self,
        *,
        trace: str,
        session: str,
        call: str,
        event_type: str = "call.completed",
        observed_at: str = "2026-07-15T00:00:00Z",
    ) -> None:
        event = make_event(
            session_id=session,
            call_id=call,
            event_type=event_type,
            observed_at=observed_at,
        )
        values = event.to_dict()
        values["trace_id"] = trace
        self.ledger.append(type(event).from_dict(values))

    def test_default_database_prefers_plugin_data(self) -> None:
        with patch.dict(os.environ, {"PLUGIN_DATA": str(self.root / "isolated")}, clear=False):
            args = build_parser().parse_args(["sessions", "--json"])
        self.assertEqual(args.db, self.root / "isolated" / "events.sqlite3")

    def test_doctor_check_rejects_unknown_status(self) -> None:
        from agent_call_governor_runtime import DoctorCheck

        with self.assertRaisesRegex(ValueError, "pass, warn, or fail"):
            DoctorCheck("sqlite", "maybe", "invalid")

    def test_doctor_plugin_root_defaults_to_current_directory(self) -> None:
        args = build_parser().parse_args(["doctor", "--json"])
        self.assertEqual(args.plugin_root, Path.cwd())

    def test_sessions_json_emits_only_summary_fields_and_trace_ids(self) -> None:
        self.append(trace=TRACE_A, session="policy-turn-a", call="call-a")
        result = self.run_cli("sessions", "--db", str(self.db), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["session_id"], TRACE_A)
        self.assertEqual(
            set(payload[0]),
            {"session_id", "first_observed_at", "last_observed_at", "call_count", "event_count", "final_status"},
        )

    def test_inspect_uses_exact_trace_and_has_no_policy_session_fallback(self) -> None:
        self.append(trace=TRACE_A, session="policy-turn-a", call="call-a")
        exact = self.run_cli("inspect", TRACE_A, "--db", str(self.db), "--json")
        fallback = self.run_cli("inspect", "policy-turn-a", "--db", str(self.db), "--json")
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(fallback.returncode, 0, fallback.stderr)
        self.assertEqual(len(json.loads(exact.stdout)), 1)
        self.assertEqual(json.loads(fallback.stdout), [])
        self.assertFalse(json.loads(exact.stdout)[0]["raw_input_stored"])

    def test_doctor_migrates_released_v02_and_inspect_round_trips_trace(self) -> None:
        legacy = self.root / "legacy.sqlite3"
        create_v02_database(legacy)
        doctor = self.run_cli(
            "doctor", "--plugin-root", str(ROOT), "--db", str(legacy), "--json"
        )
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        inspect = self.run_cli("inspect", "session-ref", "--db", str(legacy), "--json")
        self.assertEqual(inspect.returncode, 0, inspect.stderr)
        events = json.loads(inspect.stdout)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["trace_id"], "session-ref")
        self.assertEqual(events[0]["fingerprint_version"], 1)

    def test_delete_requires_yes_and_deletes_exact_trace(self) -> None:
        self.append(trace=TRACE_A, session="policy-turn-a", call="call-a")
        self.append(trace=TRACE_B, session="policy-turn-b", call="call-b")
        refused = self.run_cli("delete-session", TRACE_A, "--db", str(self.db))
        self.assertEqual(refused.returncode, 2)
        deleted = self.run_cli("delete-session", TRACE_A, "--yes", "--db", str(self.db))
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertIn("Deleted 1 events", deleted.stdout)
        self.assertIn(TRACE_A, deleted.stdout)
        self.assertEqual(self.ledger.inspect_session(TRACE_A), [])
        self.assertEqual(len(self.ledger.inspect_session(TRACE_B)), 1)

    def test_export_and_legacy_alias_are_ordered_sanitized_and_equivalent(self) -> None:
        self.append(
            trace=TRACE_A,
            session="policy-turn-a",
            call="call-a",
            observed_at="2026-07-15T00:00:01Z",
        )
        output = self.root / "exports" / "events.jsonl"
        alias_output = self.root / "exports" / "alias.jsonl"
        result = self.run_cli(
            "export", "--format", "jsonl", "--db", str(self.db),
            "--session", TRACE_A, "--output", str(output),
        )
        alias = self.run_cli(
            "export-jsonl", "--db", str(self.db), "--session", TRACE_A,
            "--output", str(alias_output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(alias.returncode, 0, alias.stderr)
        self.assertIn("deprecated", alias.stderr.lower())
        self.assertEqual(output.read_bytes(), alias_output.read_bytes())
        exported = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["event_type"] for item in exported], ["call.completed"])
        self.assertTrue(all(item["raw_input_stored"] is False for item in exported))

    def test_export_rejects_database_sidecars_and_existing_symlink(self) -> None:
        self.append(trace=TRACE_A, session="policy-turn-a", call="call-a")
        for target in (self.db, Path(f"{self.db}-wal"), Path(f"{self.db}-shm")):
            with self.subTest(target=target.name):
                result = self.run_cli(
                    "export", "--format", "jsonl", "--db", str(self.db),
                    "--output", str(target),
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("unsafe export target", result.stderr.lower())
        symlink = self.root / "export-link.jsonl"
        try:
            symlink.symlink_to(self.root / "elsewhere.jsonl")
        except OSError:
            self.skipTest("symlink creation is unavailable")
        result = self.run_cli(
            "export", "--format", "jsonl", "--db", str(self.db),
            "--output", str(symlink),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe export target", result.stderr.lower())

    def test_export_failure_removes_same_directory_temporary_file(self) -> None:
        event = make_event(session_id="turn", call_id="call")
        output = self.root / "exports" / "events.jsonl"
        with patch(
            "agent_call_governor_runtime.cli.os.replace",
            side_effect=OSError("private replacement failure"),
        ):
            with self.assertRaises(OSError):
                export_jsonl([event], output, self.db)
        self.assertFalse(output.exists())
        self.assertEqual(list(output.parent.glob(".*.tmp")), [])

    def test_export_re_sanitizes_metadata_canary(self) -> None:
        canary = "PRIVATE_EXPORT_CANARY"
        event = make_event(session_id="turn", call_id="call", event_type="policy.decided")
        values = event.to_dict()
        values["metadata"] = {"input": canary, "unknown": canary}
        values["decision_reason"] = canary
        values["reason_code"] = canary
        values["error_type"] = canary
        values["policy_version"] = canary
        output = self.root / "events.jsonl"
        unsafe = type(event).from_dict(values)
        export_jsonl([unsafe], output, self.db)
        text = output.read_text(encoding="utf-8")
        self.assertNotIn(canary, text)
        self.assertIn("[REDACTED]", text)
        self.assertNotIn(canary, json.dumps(build_report([unsafe]), sort_keys=True))
        self.ledger.append(unsafe)
        inspected = self.run_cli("inspect", "turn", "--db", str(self.db), "--json")
        reported = self.run_cli("report", "--db", str(self.db), "--json")
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(reported.returncode, 0, reported.stderr)
        self.assertNotIn(canary, inspected.stdout)
        self.assertNotIn(canary, reported.stdout)

    def test_export_rejects_existing_hardlink_to_database(self) -> None:
        alias = self.root / "alias.sqlite3"
        try:
            os.link(self.db, alias)
        except OSError:
            self.skipTest("hardlink creation is unavailable")
        result = self.run_cli(
            "export", "--format", "jsonl", "--db", str(alias),
            "--output", str(self.db),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsafe export target", result.stderr.lower())
        self.assertEqual(CallLedger(alias).events(), [])
        self.assertEqual(CallLedger(self.db).events(), [])

    @unittest.skipIf(os.name == "nt", "POSIX mode contract")
    def test_export_creates_owner_only_parent_and_file(self) -> None:
        self.append(trace=TRACE_A, session="policy-turn-a", call="call-a")
        output = self.root / "new-parent" / "events.jsonl"
        result = self.run_cli(
            "export", "--format", "jsonl", "--db", str(self.db),
            "--output", str(output),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_report_counts_schema_v2_calls_by_trace_and_span_only(self) -> None:
        lifecycle = [
            make_event(session_id="turn-a", call_id="call-a", event_type="call.proposed"),
            make_event(session_id="turn-a", call_id="call-a", event_type="policy.decided"),
            make_event(session_id="turn-a", call_id="call-a", event_type="call.started"),
            make_event(session_id="turn-a", call_id="call-a", event_type="call.completed"),
            make_event(session_id="turn-b", call_id="session", event_type="session.started"),
            make_event(session_id="turn-b", call_id="call-b", event_type="call.completed"),
            make_event(session_id="turn-b", call_id="progress", event_type="progress.observed"),
            make_event(session_id="turn-b", call_id="session-stop", event_type="session.stopped"),
        ]
        normalized = []
        for event in lifecycle:
            values = event.to_dict()
            values["trace_id"] = TRACE_A
            normalized.append(type(event).from_dict(values))
        report = build_report(normalized)
        self.assertEqual(report["sessions"], 1)
        self.assertEqual(report["calls"], 2)
        self.assertEqual(report["events"], 8)

    def test_report_session_filter_uses_trace_not_policy_session(self) -> None:
        self.append(trace=TRACE_A, session="policy-turn-a", call="call-a")
        self.append(trace=TRACE_B, session="policy-turn-b", call="call-b")
        exact = self.run_cli(
            "report", "--session", TRACE_A, "--db", str(self.db), "--json"
        )
        fallback = self.run_cli(
            "report", "--session", "policy-turn-a", "--db", str(self.db), "--json"
        )
        self.assertEqual(exact.returncode, 0, exact.stderr)
        self.assertEqual(fallback.returncode, 0, fallback.stderr)
        self.assertEqual(json.loads(exact.stdout)["calls"], 1)
        self.assertEqual(json.loads(fallback.stdout)["calls"], 0)

    def test_doctor_returns_exact_seven_checks_for_new_database(self) -> None:
        db = self.root / "doctor" / "events.sqlite3"
        result = self.run_cli(
            "doctor", "--plugin-root", str(ROOT), "--db", str(db), "--json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        checks = json.loads(result.stdout)
        self.assertEqual(
            [item["name"] for item in checks],
            ["python", "plugin", "hooks", "permissions", "sqlite", "privacy", "telemetry"],
        )
        self.assertFalse(any(item["status"] == "fail" for item in checks))
        if os.name == "nt":
            permissions = next(item for item in checks if item["name"] == "permissions")
            self.assertEqual(permissions["status"], "warn")
            self.assertIn("Windows ACL inspection is best-effort", permissions["detail"])

    def test_doctor_emits_all_checks_when_hooks_are_invalid(self) -> None:
        plugin = self.root / "broken-plugin"
        (plugin / ".codex-plugin").mkdir(parents=True)
        (plugin / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
        result = self.run_cli(
            "doctor", "--plugin-root", str(plugin), "--db", str(self.root / "doctor.db"), "--json"
        )
        self.assertEqual(result.returncode, 1)
        checks = json.loads(result.stdout)
        self.assertEqual(len(checks), 7)
        self.assertTrue(any(item["status"] == "fail" for item in checks))
        self.assertNotIn(str(plugin), result.stderr)

    def test_doctor_rejects_missing_command_windows(self) -> None:
        plugin = self.root / "plugin"
        for relative in (
            ".codex-plugin/plugin.json",
            "skills/agent-call-governor/SKILL.md",
            "hooks/dispatch.py",
        ):
            source = ROOT / relative
            destination = plugin / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        del hooks["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"]
        (plugin / "hooks" / "hooks.json").write_text(
            json.dumps(hooks), encoding="utf-8"
        )
        result = self.run_cli(
            "doctor", "--plugin-root", str(plugin),
            "--db", str(self.root / "missing-windows.db"), "--json",
        )
        self.assertEqual(result.returncode, 1)
        checks = {item["name"]: item for item in json.loads(result.stdout)}
        self.assertEqual(checks["hooks"]["status"], "fail")

    def test_doctor_handles_non_mapping_hook_without_traceback(self) -> None:
        plugin = self.root / "null-hook-plugin"
        for relative in (
            ".codex-plugin/plugin.json",
            "skills/agent-call-governor/SKILL.md",
            "hooks/dispatch.py",
        ):
            source = ROOT / relative
            destination = plugin / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        hooks["hooks"]["SessionStart"] = [None]
        (plugin / "hooks" / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")
        result = self.run_cli(
            "doctor", "--plugin-root", str(plugin),
            "--db", str(self.root / "null-hook.db"), "--json",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(json.loads(result.stdout)), 7)
        self.assertNotIn("Traceback", result.stderr)

    def test_doctor_rejects_future_database_without_mutating_it(self) -> None:
        db = self.root / "future.sqlite3"
        connection = sqlite3.connect(db)
        connection.execute("PRAGMA user_version = 99")
        connection.commit()
        connection.close()
        before = db.read_bytes()
        result = self.run_cli(
            "doctor", "--plugin-root", str(ROOT), "--db", str(db), "--json"
        )
        self.assertEqual(result.returncode, 1)
        checks = {item["name"]: item for item in json.loads(result.stdout)}
        self.assertEqual(checks["sqlite"]["status"], "fail")
        self.assertEqual(db.read_bytes(), before)
        self.assertNotIn("Traceback", result.stderr)

    def test_operational_schema_error_is_static_and_has_no_traceback(self) -> None:
        db = self.root / "foreign.sqlite3"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE unrelated(secret TEXT)")
        connection.commit()
        connection.close()
        result = self.run_cli("sessions", "--db", str(db), "--json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "agent-call-governor-runtime: operational command failed\n",
        )

    def test_doctor_rejects_foreign_v0_before_ledger_construction(self) -> None:
        db = self.root / "foreign-preflight.sqlite3"
        connection = sqlite3.connect(db)
        connection.execute("CREATE TABLE unrelated(secret TEXT)")
        connection.commit()
        connection.close()
        with patch.object(CallLedger, "__init__", side_effect=AssertionError) as initialize:
            status, detail = _sqlite_contract(db)
        self.assertEqual(status, "fail")
        self.assertIn("foreign", detail)
        initialize.assert_not_called()

    def test_secure_cleanup_post_commit_failure_is_controlled(self) -> None:
        error = SecureCleanupIncompleteError("delete_session", 3, None)
        stderr = io.StringIO()
        with patch("agent_call_governor_runtime.cli.CallLedger") as ledger_type:
            ledger_type.return_value.delete_session.side_effect = error
            with redirect_stderr(stderr):
                result = main([
                    "delete-session", TRACE_A, "--yes", "--db", str(self.db)
                ])
        self.assertEqual(result, 2)
        self.assertEqual(
            stderr.getvalue(),
            "agent-call-governor-runtime: secure cleanup incomplete; retry required\n",
        )


if __name__ == "__main__":
    unittest.main()
