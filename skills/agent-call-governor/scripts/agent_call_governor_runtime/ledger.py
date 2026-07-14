"""SQLite-first lifecycle ledger with an optional JSONL audit mirror."""

from __future__ import annotations

import json
import sqlite3
import threading
import warnings
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any, TypeVar

from .models import CallEvent
from .redaction import sanitize_metadata


_COUNTED_PHASES = ("started", "completed", "failed")
T = TypeVar("T")


class DuplicateCallIdError(ValueError):
    """Raised when a session attempts to reuse a lifecycle call ID."""

    def __init__(self, session_id: str, call_id: str) -> None:
        self.session_id = session_id
        self.call_id = call_id
        super().__init__(f"call_id already exists in session: {call_id}")


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
        with closing(self._connect()) as connection:
            with connection:
                self._insert(connection, event)
        self._mirror_best_effort((event,))

    def append_terminal_if_open(self, event: CallEvent) -> bool:
        """Atomically append one terminal phase for an open lifecycle call."""
        if event.phase not in {"completed", "failed", "cancelled"}:
            raise ValueError("terminal event phase must be completed, failed, or cancelled")
        inserted = False
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                latest = connection.execute(
                    """
                    SELECT phase FROM call_events
                    WHERE session_id = ? AND call_id = ?
                    ORDER BY seq DESC
                    LIMIT 1
                    """,
                    (event.session_id, event.call_id),
                ).fetchone()
                if latest is not None and str(latest["phase"]) in {"proposed", "started"}:
                    self._insert(connection, event)
                    inserted = True
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        if inserted:
            self._mirror_best_effort((event,))
        return inserted

    def atomic_transition(
        self,
        session_id: str,
        operation: Callable[[list[dict[str, str]]], tuple[T, Sequence[CallEvent]]],
    ) -> T:
        """Serialize history evaluation and lifecycle reservation in SQLite.

        ``BEGIN IMMEDIATE`` ensures concurrent processes cannot both decide from
        the same stale session history. The optional JSONL mirror is written only
        after the authoritative SQLite transaction commits.
        """
        events: tuple[CallEvent, ...] = ()
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                history = self._history(connection, session_id)
                result, built_events = operation(history)
                events = tuple(built_events)
                if not events:
                    raise ValueError("atomic transition must persist at least one event")
                call_ids = {event.call_id for event in events}
                if len(call_ids) != 1:
                    raise ValueError("atomic transition events must share one call_id")
                for event in events:
                    if event.session_id != session_id:
                        raise ValueError("atomic transition events must match session_id")
                call_id = next(iter(call_ids))
                existing = connection.execute(
                    "SELECT 1 FROM call_events WHERE session_id = ? AND call_id = ? LIMIT 1",
                    (session_id, call_id),
                ).fetchone()
                if existing is not None:
                    raise DuplicateCallIdError(session_id, call_id)
                for event in events:
                    self._insert(connection, event)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        self._mirror_best_effort(events)
        return result

    @staticmethod
    def _insert(connection: sqlite3.Connection, event: CallEvent) -> None:
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
                json.dumps(
                    sanitize_metadata(event.metadata, source=event.source),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                event.error_type,
            ),
        )

    def _append_jsonl(self, events: Sequence[CallEvent]) -> None:
        if self.jsonl_path is None:
            return
        encoded = []
        for event in events:
            value = event.to_dict()
            value["metadata"] = sanitize_metadata(event.metadata, source=event.source)
            encoded.append(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        with self._jsonl_lock:
            with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as stream:
                for line in encoded:
                    stream.write(line + "\n")

    def _mirror_best_effort(self, events: Sequence[CallEvent]) -> None:
        """Mirror authoritative events without changing execution semantics."""
        try:
            self._append_jsonl(events)
        except Exception as exc:
            try:
                warnings.warn(
                    f"Agent Call Governor JSONL mirror failed: {type(exc).__name__}",
                    RuntimeWarning,
                    stacklevel=3,
                )
            except Exception:
                # Warning filters and custom warning hooks may raise. An optional
                # mirror must never invalidate an authoritative SQLite commit.
                pass

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

    def latest_event(self, session_id: str, call_id: str) -> CallEvent | None:
        """Return the latest persisted phase for a call, if it exists."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM call_events
                WHERE session_id = ? AND call_id = ?
                ORDER BY seq DESC
                LIMIT 1
                """,
                (session_id, call_id),
            ).fetchone()
        return None if row is None else _event_from_row(row)

    def history(self, session_id: str) -> list[dict[str, str]]:
        with closing(self._connect()) as connection:
            return self._history(connection, session_id)

    @staticmethod
    def _history(connection: sqlite3.Connection, session_id: str) -> list[dict[str, str]]:
        placeholders = ",".join("?" for _ in _COUNTED_PHASES)
        query = f"""
            SELECT event.*
            FROM call_events AS event
            JOIN (
                SELECT call_id, MAX(seq) AS latest_seq
                FROM call_events
                WHERE session_id = ?
                GROUP BY call_id
            ) AS latest ON event.seq = latest.latest_seq
            WHERE event.phase IN ({placeholders})
            ORDER BY event.seq
        """
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
