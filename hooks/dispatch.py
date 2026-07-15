from __future__ import annotations

import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any


def _integer(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    value = default if raw is None else int(raw)
    if value < minimum or value > maximum:
        raise ValueError
    return value


def _dispatch() -> dict[str, Any] | None:
    plugin_root = Path(
        os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1])
    ).resolve()
    plugin_data = Path(
        os.environ.get(
            "PLUGIN_DATA",
            Path.home() / ".codex" / "agent-call-governor",
        )
    ).expanduser()
    package_root = plugin_root / "skills" / "agent-call-governor" / "scripts"
    sys.path.insert(0, str(package_root))

    from agent_call_governor_runtime import CallLedger, GovernedRuntime
    from agent_call_governor_runtime.codex_hook import handle_codex_hook

    mode = os.environ.get("AGENT_CALL_GOVERNOR_MODE", "observe")
    profile = os.environ.get("AGENT_CALL_GOVERNOR_PROFILE", "balanced")
    risk = os.environ.get("AGENT_CALL_GOVERNOR_RISK", "medium")
    if mode not in {"observe", "warn"}:
        raise ValueError
    if profile not in {"strict", "balanced", "quality-first"}:
        raise ValueError
    if risk not in {"low", "medium", "high"}:
        raise ValueError
    retention = _integer(
        "AGENT_CALL_GOVERNOR_RETENTION_DAYS",
        7,
        minimum=1,
        maximum=3650,
    )
    stale = _integer(
        "AGENT_CALL_GOVERNOR_STALE_SECONDS",
        86400,
        minimum=0,
        maximum=31536000,
    )
    db_path = Path(
        os.environ.get(
            "AGENT_CALL_GOVERNOR_DB",
            plugin_data / "events.sqlite3",
        )
    ).expanduser()
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError

    ledger = CallLedger(db_path, busy_timeout_ms=1000)
    runtime = GovernedRuntime(
        ledger,
        mode=mode,
        failure_policy="fail-open",
        source="codex-hook",
        warning_handler=lambda _message: None,
        default_profile=profile,
        default_risk=risk,
    )
    return handle_codex_hook(
        payload,
        runtime,
        retention_days=retention,
        stale_reservation_seconds=stale,
    )


def main() -> int:
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            output = _dispatch()
        if output is not None:
            sys.stdout.write(json.dumps(output, ensure_ascii=False))
        return 0
    except BaseException as exc:
        print(
            f"agent-call-governor hook unavailable: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
