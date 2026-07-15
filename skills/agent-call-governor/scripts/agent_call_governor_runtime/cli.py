"""Local operational CLI and Codex-hook compatibility entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .codex_hook import SUPPORTED_EVENTS, main as codex_hook_main
from .ledger import CallLedger, SCHEMA_VERSION, SecureCleanupIncompleteError
from .models import CallEvent, DoctorCheck
from .redaction import sanitize_event_dict


_CALL_EVENT_TYPES = frozenset({
    "call.proposed",
    "policy.decided",
    "call.started",
    "call.blocked",
    "call.completed",
    "call.failed",
    "call.cancelled",
})
_FINAL_EVENT_TYPES = frozenset({
    "call.started",
    "call.blocked",
    "call.completed",
    "call.failed",
    "call.cancelled",
})


class UnsafeExportTargetError(ValueError):
    """The requested export path aliases protected plugin state."""


def default_database_path() -> Path:
    """Return the documented plugin-aware default database path."""
    plugin_data = os.environ.get("PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data) / "events.sqlite3"
    return Path.home() / ".codex" / "agent-call-governor" / "events.sqlite3"


def build_report(events: list[CallEvent]) -> dict[str, Any]:
    """Summarize schema-v2 policy and execution once per logical call."""
    call_events = [event for event in events if event.event_type in _CALL_EVENT_TYPES]
    grouped: dict[tuple[str, str], list[CallEvent]] = {}
    for event in call_events:
        grouped.setdefault((event.trace_id, event.span_id), []).append(event)

    policy_allowed = 0
    policy_blocked = 0
    would_block_but_executed = 0
    executed = 0
    final_states: Counter[str] = Counter()
    budget_kinds: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    for key_events in grouped.values():
        decisions = [event for event in key_events if event.event_type == "policy.decided"]
        if not decisions:
            decisions = [
                event
                for event in key_events
                if event.policy_allowed is not None and event.phase == "proposed"
            ]
        decision = decisions[-1] if decisions else None
        lifecycle = [event for event in key_events if event.event_type in _FINAL_EVENT_TYPES]
        final = lifecycle[-1] if lifecycle else key_events[-1]
        did_execute = final.event_type in {"call.started", "call.completed", "call.failed"}
        if did_execute:
            executed += 1
        final_states[final.phase] += 1
        budget_kinds[final.budget_kind] += 1
        modes[final.mode] += 1
        if decision is not None:
            if decision.policy_allowed is True:
                policy_allowed += 1
            elif decision.policy_allowed is False:
                policy_blocked += 1
                if did_execute:
                    would_block_but_executed += 1
            if decision.decision_reason:
                safe_decision = sanitize_event_dict(decision.to_dict())
                reasons[str(safe_decision["decision_reason"])] += 1

    return {
        "schema_version": 2,
        "events": len(events),
        "sessions": len({event.trace_id for event in events}),
        "calls": len(grouped),
        "policy_allowed": policy_allowed,
        "policy_blocked": policy_blocked,
        "executed": executed,
        "execution_blocked": final_states["blocked"],
        "would_block_but_executed": would_block_but_executed,
        "final_states": dict(sorted(final_states.items())),
        "by_budget_kind": dict(sorted(budget_kinds.items())),
        "by_mode": dict(sorted(modes.items())),
        "decision_reasons": dict(sorted(reasons.items())),
    }


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _aliases_protected_file(output: Path, protected: Path) -> bool:
    if _same_path(output, protected):
        return True
    if not output.exists() or not protected.exists():
        return False
    try:
        return os.path.samefile(output, protected)
    except OSError:
        return False


def _validate_export_target(output_path: Path, database_path: Path) -> tuple[Path, Path]:
    requested_output = output_path.expanduser()
    database = database_path.expanduser()
    if requested_output.is_symlink():
        raise UnsafeExportTargetError(
            "unsafe export target; --output and --db must differ"
        )
    output = requested_output.parent.resolve(strict=False) / requested_output.name
    database_targets = (
        database,
        Path(f"{database}-wal"),
        Path(f"{database}-shm"),
    )
    if any(_aliases_protected_file(output, target) for target in database_targets):
        raise UnsafeExportTargetError(
            "unsafe export target; --output and --db must differ"
        )
    return output, database.resolve(strict=False)


def export_jsonl(events: list[CallEvent], output_path: Path, database_path: Path) -> int:
    """Atomically export validated events without exposing raw call payloads."""
    output, _ = _validate_export_target(output_path, database_path)
    parent_existed = output.parent.exists()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    parent_identity = os.stat(output.parent, follow_symlinks=False)
    if (
        output.parent.is_symlink()
        or not stat.S_ISDIR(parent_identity.st_mode)
        or (
            os.name != "nt"
            and not parent_existed
            and stat.S_IMODE(parent_identity.st_mode) != 0o700
        )
    ):
        raise UnsafeExportTargetError(
            "unsafe export target; --output and --db must differ"
        )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            for event in events:
                stream.write(
                    json.dumps(
                        sanitize_event_dict(event.to_dict()),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        current_parent = os.stat(output.parent, follow_symlinks=False)
        if (
            current_parent.st_dev != parent_identity.st_dev
            or current_parent.st_ino != parent_identity.st_ino
            or output.parent.is_symlink()
        ):
            raise UnsafeExportTargetError(
                "unsafe export target; --output and --db must differ"
            )
        _validate_export_target(output, database_path)
        os.replace(temporary, output)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()
    return len(events)


def _format_report(report: dict[str, Any]) -> str:
    lines = [
        "Agent Call Governor runtime report",
        f"Sessions: {report['sessions']}",
        f"Calls: {report['calls']}",
        f"Events: {report['events']}",
        f"Policy allowed: {report['policy_allowed']}",
        f"Policy blocked: {report['policy_blocked']}",
        f"Executed: {report['executed']}",
        f"Execution blocked: {report['execution_blocked']}",
        f"Would block but executed: {report['would_block_but_executed']}",
    ]
    if report["decision_reasons"]:
        reasons = ", ".join(
            f"{name}={count}" for name, count in report["decision_reasons"].items()
        )
        lines.append(f"Decision reasons: {reasons}")
    return "\n".join(lines)


def _plugin_contract(plugin_root: Path) -> tuple[str, str]:
    required = (
        plugin_root / ".codex-plugin" / "plugin.json",
        plugin_root / "skills" / "agent-call-governor" / "SKILL.md",
        plugin_root / "hooks" / "hooks.json",
        plugin_root / "hooks" / "dispatch.py",
    )
    if not all(path.is_file() for path in required):
        return "fail", "required plugin files are missing"
    try:
        manifest = json.loads(required[0].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "fail", "plugin manifest is invalid"
    if not isinstance(manifest, dict) or not manifest.get("name"):
        return "fail", "plugin manifest is invalid"
    return "pass", "plugin manifest, skill, hook configuration, and dispatcher are present"


def _hooks_contract(plugin_root: Path) -> tuple[str, str]:
    try:
        document = json.loads(
            (plugin_root / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        hooks = document["hooks"]
        if not isinstance(hooks, dict) or set(hooks) != set(SUPPORTED_EVENTS):
            raise ValueError
        for configurations in hooks.values():
            if not isinstance(configurations, list) or not configurations:
                raise ValueError
            for configuration in configurations:
                if not isinstance(configuration, dict):
                    raise ValueError
                commands = configuration.get("hooks")
                if not isinstance(commands, list) or not commands:
                    raise ValueError
                for command in commands:
                    if (
                        not isinstance(command, dict)
                        or command.get("type") != "command"
                        or not isinstance(command.get("command"), str)
                        or not command["command"].strip()
                        or not isinstance(command.get("commandWindows"), str)
                        or not command["commandWindows"].strip()
                    ):
                        raise ValueError
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return "fail", "hook configuration must define exactly six portable command events"
    return "pass", "exactly six portable Codex hook events are configured"


def _permissions_contract(database_path: Path) -> tuple[str, str]:
    if os.name == "nt":
        return "warn", "Windows ACL inspection is best-effort"
    paths = [database_path.parent]
    if database_path.exists():
        paths.append(database_path)
    for path in paths:
        if not path.exists():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        expected = 0o700 if path.is_dir() else 0o600
        if mode != expected:
            return "fail", "database directory and file must be owner-only"
    return "pass", "database directory and file are owner-only"


def _sqlite_contract(database_path: Path) -> tuple[str, str]:
    try:
        initial_state = CallLedger.inspect_schema(database_path)
    except Exception:
        return "fail", "database is unreadable, foreign, corrupt, or unsupported"
    try:
        ledger = CallLedger(database_path, busy_timeout_ms=1000)
        connection = ledger._connect()
        try:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            timeout = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TEMP TABLE doctor_write_probe(value INTEGER)")
            connection.execute("INSERT INTO doctor_write_probe VALUES (1)")
            connection.rollback()
        finally:
            connection.close()
    except Exception:
        return "fail", "database schema, WAL, or transaction probe failed"
    if version != SCHEMA_VERSION or journal != "wal" or timeout != 1000:
        return "fail", "database schema, WAL, or timeout is inconsistent"
    migration = "released v0.2 migration completed; " if initial_state == "legacy-v0.2" else ""
    return "pass", migration + "schema 2, WAL, 1000 ms timeout, and rollback probe are healthy"


def run_doctor(database_path: Path, plugin_root: Path) -> list[DoctorCheck]:
    """Return all seven stable checks without leaking operational exceptions."""
    python_status = "pass" if sys.version_info >= (3, 10) else "fail"
    plugin_status, plugin_detail = _plugin_contract(plugin_root)
    hooks_status, hooks_detail = _hooks_contract(plugin_root)
    sqlite_status, sqlite_detail = _sqlite_contract(database_path)
    permissions_status, permissions_detail = _permissions_contract(database_path)
    return [
        DoctorCheck("python", python_status, "Python 3.10 or newer is required"),
        DoctorCheck("plugin", plugin_status, plugin_detail),
        DoctorCheck("hooks", hooks_status, hooks_detail),
        DoctorCheck("permissions", permissions_status, permissions_detail),
        DoctorCheck("sqlite", sqlite_status, sqlite_detail),
        DoctorCheck(
            "privacy",
            "pass",
            "bundled CLI stores no raw input, retains seven days by default, and configures no live JSONL mirror",
        ),
        DoctorCheck("telemetry", "pass", "no remote telemetry is configured"),
    ]


def _add_database_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=default_database_path())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-call-governor-runtime",
        description="Inspect and operate the Agent Call Governor runtime ledger",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Validate plugin and local storage")
    _add_database_argument(doctor)
    doctor.add_argument("--plugin-root", type=Path, default=Path.cwd())
    doctor.add_argument("--json", action="store_true")

    sessions = subparsers.add_parser("sessions", help="List local operational traces")
    _add_database_argument(sessions)
    sessions.add_argument("--json", action="store_true")

    inspect = subparsers.add_parser("inspect", help="Inspect one operational trace")
    inspect.add_argument("session_id")
    _add_database_argument(inspect)
    inspect.add_argument("--json", action="store_true")

    delete = subparsers.add_parser("delete-session", help="Securely delete one trace")
    delete.add_argument("session_id")
    delete.add_argument("--yes", required=True, action="store_true")
    _add_database_argument(delete)

    report = subparsers.add_parser("report", help="Summarize a runtime ledger")
    _add_database_argument(report)
    report.add_argument("--session")
    report.add_argument("--json", action="store_true")

    export = subparsers.add_parser("export", help="Export sanitized lifecycle events")
    export.add_argument("--format", choices=("jsonl",), required=True)
    export.add_argument("--output", required=True, type=Path)
    _add_database_argument(export)
    export.add_argument("--session")

    legacy_export = subparsers.add_parser(
        "export-jsonl", help="Deprecated alias for export --format jsonl"
    )
    legacy_export.add_argument("--output", required=True, type=Path)
    _add_database_argument(legacy_export)
    legacy_export.add_argument("--session")

    hook = subparsers.add_parser("codex-hook", help="Handle one Codex hook event from stdin")
    hook.add_argument("--mode", choices=("observe", "warn"), default="observe")
    hook.add_argument(
        "--failure-policy", choices=("fail-open", "fail-closed"), default="fail-open"
    )
    hook.add_argument(
        "--profile", choices=("strict", "balanced", "quality-first"), default="balanced"
    )
    hook.add_argument("--risk", choices=("low", "medium", "high"), default="medium")
    _add_database_argument(hook)
    hook.add_argument("--jsonl", type=Path)
    return parser


def _events_for_trace(ledger: CallLedger, trace_id: str | None) -> list[CallEvent]:
    return ledger.inspect_session(trace_id) if trace_id is not None else ledger.events()


def _json_events(events: list[CallEvent]) -> list[dict[str, Any]]:
    return [sanitize_event_dict(event.to_dict()) for event in events]


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "codex-hook":
        hook_arguments = [
            "--mode", args.mode,
            "--failure-policy", args.failure_policy,
            "--profile", args.profile,
            "--risk", args.risk,
            "--db", str(args.db),
        ]
        if args.jsonl is not None:
            hook_arguments.extend(("--jsonl", str(args.jsonl)))
        return codex_hook_main(hook_arguments)

    if args.command == "doctor":
        checks = run_doctor(args.db, args.plugin_root)
        if args.json:
            print(json.dumps([asdict(check) for check in checks], ensure_ascii=False, indent=2))
        else:
            for check in checks:
                print(f"{check.name}: {check.status} - {check.detail}")
        return 1 if any(check.status == "fail" for check in checks) else 0

    try:
        ledger = CallLedger(args.db)
        if args.command == "sessions":
            summaries = [asdict(summary) for summary in ledger.sessions()]
            if args.json:
                print(json.dumps(summaries, ensure_ascii=False, indent=2))
            else:
                for summary in summaries:
                    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "inspect":
            events = _json_events(ledger.inspect_session(args.session_id))
            if args.json:
                print(json.dumps(events, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                for event in events:
                    print(json.dumps(event, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "delete-session":
            try:
                count = ledger.delete_session(args.session_id)
            except SecureCleanupIncompleteError:
                print(
                    "agent-call-governor-runtime: secure cleanup incomplete; retry required",
                    file=sys.stderr,
                )
                return 2
            safe_reference = json.dumps(args.session_id, ensure_ascii=False)
            print(f"Deleted {count} events from {safe_reference}")
            return 0
        if args.command == "report":
            report = build_report(_events_for_trace(ledger, args.session))
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(_format_report(report))
            return 0
        if args.command == "export-jsonl":
            print(
                "agent-call-governor-runtime: export-jsonl is deprecated; use export --format jsonl",
                file=sys.stderr,
            )
        events = _events_for_trace(ledger, args.session)
        count = export_jsonl(events, args.output, args.db)
        print(f"Exported {count} events")
        return 0
    except UnsafeExportTargetError:
        print(
            "agent-call-governor-runtime: unsafe export target; --output and --db must differ",
            file=sys.stderr,
        )
        return 2
    except (OSError, sqlite3.Error, ValueError, RuntimeError):
        print("agent-call-governor-runtime: operational command failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
