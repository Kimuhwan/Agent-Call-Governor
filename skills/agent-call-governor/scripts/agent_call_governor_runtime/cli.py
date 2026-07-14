"""Command-line reporting, export, and Codex-hook entrypoints."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .codex_hook import main as codex_hook_main
from .ledger import CallLedger
from .models import CallEvent


def build_report(events: list[CallEvent]) -> dict[str, Any]:
    """Summarize policy decisions and actual execution by logical call."""
    grouped: dict[tuple[str, str], list[CallEvent]] = {}
    for event in events:
        grouped.setdefault((event.session_id, event.call_id), []).append(event)

    policy_decisions: dict[tuple[str, str], CallEvent] = {}
    executed: set[tuple[str, str]] = set()
    final_states: Counter[str] = Counter()
    budget_kinds: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    for key, call_events in grouped.items():
        for event in call_events:
            if event.phase == "proposed":
                policy_decisions[key] = event
        final = call_events[-1]
        if final.phase in {"started", "completed", "failed"}:
            executed.add(key)
        final_states[final.phase] += 1
        budget_kinds[final.budget_kind] += 1
        modes[final.mode] += 1

    policy_allowed = 0
    policy_blocked = 0
    would_block_but_executed = 0
    for key, decision in policy_decisions.items():
        if decision.policy_allowed is True:
            policy_allowed += 1
        elif decision.policy_allowed is False:
            policy_blocked += 1
            if key in executed:
                would_block_but_executed += 1
        if decision.decision_reason:
            reasons[decision.decision_reason] += 1

    return {
        "schema_version": 1,
        "events": len(events),
        "sessions": len({event.session_id for event in events}),
        "calls": len(grouped),
        "policy_allowed": policy_allowed,
        "policy_blocked": policy_blocked,
        "executed": len(executed),
        "execution_blocked": final_states["blocked"],
        "would_block_but_executed": would_block_but_executed,
        "final_states": dict(sorted(final_states.items())),
        "by_budget_kind": dict(sorted(budget_kinds.items())),
        "by_mode": dict(sorted(modes.items())),
        "decision_reasons": dict(sorted(reasons.items())),
    }


def export_jsonl(events: list[CallEvent], output_path: Path, database_path: Path) -> int:
    """Atomically export validated events without exposing raw call payloads."""
    output = output_path.expanduser().resolve()
    database = database_path.expanduser().resolve()
    if output == database:
        raise ValueError("--output and --db must differ")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for event in events:
                stream.write(
                    json.dumps(
                        event.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        os.replace(temporary, output)
    finally:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-call-governor-runtime",
        description="Inspect and operate the Agent Call Governor runtime ledger",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    report = subparsers.add_parser("report", help="Summarize a runtime ledger")
    report.add_argument("--db", required=True, type=Path)
    report.add_argument("--session")
    report.add_argument("--json", action="store_true")

    export = subparsers.add_parser("export-jsonl", help="Export sanitized lifecycle events")
    export.add_argument("--db", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--session")

    hook = subparsers.add_parser("codex-hook", help="Handle one Codex hook event from stdin")
    hook.add_argument("--mode", choices=("observe", "warn"), default="observe")
    hook.add_argument("--failure-policy", choices=("fail-open", "fail-closed"), default="fail-open")
    hook.add_argument(
        "--db",
        type=Path,
        default=Path.home() / ".codex" / "agent-call-governor" / "events.sqlite3",
    )
    hook.add_argument("--jsonl", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "codex-hook":
            hook_arguments = [
                "--mode",
                args.mode,
                "--failure-policy",
                args.failure_policy,
                "--db",
                str(args.db),
            ]
            if args.jsonl is not None:
                hook_arguments.extend(("--jsonl", str(args.jsonl)))
            return codex_hook_main(hook_arguments)

        if args.command == "export-jsonl" and args.db.expanduser().resolve() == args.output.expanduser().resolve():
            raise ValueError("--output and --db must differ")
        ledger = CallLedger(args.db)
        events = ledger.events(args.session)
        if args.command == "report":
            report = build_report(events)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                print(_format_report(report))
            return 0
        count = export_jsonl(events, args.output, args.db)
        print(f"Exported {count} events to {args.output}")
        return 0
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"agent-call-governor-runtime: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
