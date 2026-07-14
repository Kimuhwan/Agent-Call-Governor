"""SQLite-first lifecycle ledger with an optional JSONL audit mirror."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Any

from .models import CallEvent


_COUNTED_PHASES = ("started", "completed", "failed")


class CallLedger:
    """Persist call lifecycle events and derive policy history per session."""

    def __init__(self, sqlite_path: str | Path, jsonl_path: str | Path | None = None) -> None:
        self.sqlite_path = Path(sqlite_path).expanduser()
        self.jsonl_path = Path(jsonl_path).expanduser() if jsonl_path is not None else None
        self._jsonl_lock = threading.Lock()
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        if self.jsonl_path is not None:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.sqlite_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.executescript(
                    """
                CREATE TABLE IF NOT EXISTS call_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    parent_call_id TEXT,
                    phase TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    route TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    budget_kind TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    quality_risk TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    policy_allowed INTEGER,
                    execution_allowed INTEGER,
                    decision_reason TEXT,
                    progress TEXT,
                    duration_ms REAL,
                    source TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    error_type TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_call_events_session_call
                    ON call_events(session_id, call_id, seq);
                CREATE INDEX IF NOT EXISTS idx_call_events_session_fingerprint
                    ON call_events(session_id, fingerprint, seq);
                    """
                )

    def append(self, event: CallEvent) -> None:
        value = event.to_dict()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                INSERT INTO call_events (
                    event_id, schema_version, session_id, call_id, parent_call_id,
                    phase, occurred_at, objective, route, fingerprint, budget_kind,
                    profile, quality_risk, mode, policy_allowed, execution_allowed,
                    decision_reason, progress, duration_ms, source, metadata_json,
                    error_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.schema_version,
                        event.session_id,
                        event.call_id,
                        event.parent_call_id,
                        event.phase,
                        event.occurred_at,
                        event.objective,
                        event.route,
                        event.fingerprint,
                        event.budget_kind,
                        event.profile,
                        event.quality_risk,
                        event.mode,
                        _optional_bool(event.policy_allowed),
                        _optional_bool(event.execution_allowed),
                        event.decision_reason,
                        event.progress,
                        event.duration_ms,
                        event.source,
                        json.dumps(dict(event.metadata), ensure_ascii=False, sort_keys=True),
                        event.error_type,
                    ),
                )
        if self.jsonl_path is not None:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            with self._jsonl_lock:
                with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(encoded + "\n")

    def events(self, session_id: str | None = None) -> list[CallEvent]:
        query = "SELECT * FROM call_events"
        parameters: tuple[Any, ...] = ()
        if session_id is not None:
            query += " WHERE session_id = ?"
            parameters = (session_id,)
        query += " ORDER BY seq"
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_event_from_row(row) for row in rows]

    def history(self, session_id: str) -> list[dict[str, str]]:
        placeholders = ",".join("?" for _ in _COUNTED_PHASES)
        query = f"""
            SELECT event.*
            FROM call_events AS event
            JOIN (
                SELECT call_id, MAX(seq) AS latest_seq
                FROM call_events
                WHERE session_id = ? AND phase IN ({placeholders})
                GROUP BY call_id
            ) AS latest ON event.seq = latest.latest_seq
            ORDER BY event.seq
        """
        with closing(self._connect()) as connection:
            rows = connection.execute(query, (session_id, *_COUNTED_PHASES)).fetchall()
        history: list[dict[str, str]] = []
        for row in rows:
            item = {
                "fingerprint": str(row["fingerprint"]),
                "budget_kind": str(row["budget_kind"]),
            }
            if row["progress"] is not None:
                item["progress"] = str(row["progress"])
            history.append(item)
        return history


def _optional_bool(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _event_from_row(row: sqlite3.Row) -> CallEvent:
    return CallEvent(
        schema_version=int(row["schema_version"]),
        event_id=str(row["event_id"]),
        session_id=str(row["session_id"]),
        call_id=str(row["call_id"]),
        parent_call_id=row["parent_call_id"],
        phase=str(row["phase"]),
        occurred_at=str(row["occurred_at"]),
        objective=str(row["objective"]),
        route=str(row["route"]),
        fingerprint=str(row["fingerprint"]),
        budget_kind=str(row["budget_kind"]),
        profile=str(row["profile"]),
        quality_risk=str(row["quality_risk"]),
        mode=str(row["mode"]),
        policy_allowed=None if row["policy_allowed"] is None else bool(row["policy_allowed"]),
        execution_allowed=None
        if row["execution_allowed"] is None
        else bool(row["execution_allowed"]),
        decision_reason=row["decision_reason"],
        progress=row["progress"],
        duration_ms=row["duration_ms"],
        source=str(row["source"]),
        metadata=json.loads(str(row["metadata_json"])),
        error_type=row["error_type"],
    )
