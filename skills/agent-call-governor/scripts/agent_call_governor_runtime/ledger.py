"""SQLite-first lifecycle ledger with an optional JSONL audit mirror."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import warnings
from collections.abc import Callable, Sequence
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from .models import (
    CallEvent,
    SessionSummary,
    _sanitized_event_metadata,
    _validate_event_boundary,
)
from .policy import PROGRESS_VALUES


SCHEMA_VERSION = 2
_MIGRATION_CLEANUP_PENDING = 0x41434732  # ASCII "ACG2"
_COUNTED_EVENT_TYPES = frozenset({
    "call.started",
    "call.completed",
    "call.failed",
})
_CALL_LIFECYCLE_EVENT_TYPES = _COUNTED_EVENT_TYPES | {
    "call.proposed",
    "call.blocked",
    "call.cancelled",
}
_LEGACY_EVENT_TYPES = {
    "proposed": "call.proposed",
    "blocked": "call.blocked",
    "started": "call.started",
    "completed": "call.completed",
    "failed": "call.failed",
    "cancelled": "call.cancelled",
}
_LEGACY_V02_COLUMNS = (
    ("seq", "INTEGER", 0, None, 1),
    ("event_id", "TEXT", 1, None, 0),
    ("schema_version", "INTEGER", 1, None, 0),
    ("session_id", "TEXT", 1, None, 0),
    ("call_id", "TEXT", 1, None, 0),
    ("parent_call_id", "TEXT", 0, None, 0),
    ("phase", "TEXT", 1, None, 0),
    ("occurred_at", "TEXT", 1, None, 0),
    ("objective", "TEXT", 1, None, 0),
    ("route", "TEXT", 1, None, 0),
    ("fingerprint", "TEXT", 1, None, 0),
    ("budget_kind", "TEXT", 1, None, 0),
    ("profile", "TEXT", 1, None, 0),
    ("quality_risk", "TEXT", 1, None, 0),
    ("mode", "TEXT", 1, None, 0),
    ("policy_allowed", "INTEGER", 0, None, 0),
    ("execution_allowed", "INTEGER", 0, None, 0),
    ("decision_reason", "TEXT", 0, None, 0),
    ("progress", "TEXT", 0, None, 0),
    ("duration_ms", "REAL", 0, None, 0),
    ("source", "TEXT", 1, None, 0),
    ("metadata_json", "TEXT", 1, None, 0),
    ("error_type", "TEXT", 0, None, 0),
)
_V2_ADDED_COLUMNS = (
    ("event_type", "TEXT"),
    ("observed_at", "TEXT"),
    ("trace_id", "TEXT"),
    ("turn_id", "TEXT"),
    ("span_id", "TEXT"),
    ("parent_span_id", "TEXT"),
    ("source_event", "TEXT"),
    ("agent_id", "TEXT"),
    ("tool_name", "TEXT"),
    ("input_digest", "TEXT"),
    ("fingerprint_version", "INTEGER"),
    ("failure_policy", "TEXT"),
    ("decision", "TEXT"),
    ("reason_code", "TEXT"),
    ("policy_version", "TEXT"),
    ("budget_before", "INTEGER"),
    ("budget_after", "INTEGER"),
    ("decision_latency_ms", "REAL"),
    ("execution_latency_ms", "REAL"),
    ("status", "TEXT"),
    ("prompt_tokens", "INTEGER"),
    ("completion_tokens", "INTEGER"),
    ("total_tokens", "INTEGER"),
    ("estimated_cost_usd", "REAL"),
    ("pricing_version", "TEXT"),
    ("raw_input_stored", "INTEGER"),
    ("safe_metadata_json", "TEXT"),
    ("policy_facts_json", "TEXT"),
)
_SCHEMA_V2_COLUMNS = _LEGACY_V02_COLUMNS + tuple(
    (name, column_type, 0, None, 0) for name, column_type in _V2_ADDED_COLUMNS
)
_EXPECTED_SCHEMA_OBJECTS = frozenset({
    ("table", "call_events", "call_events"),
    ("table", "sqlite_sequence", "sqlite_sequence"),
    ("index", "idx_call_events_session_call", "call_events"),
    ("index", "idx_call_events_session_fingerprint", "call_events"),
    ("index", "sqlite_autoindex_call_events_1", "call_events"),
})
_EXPECTED_INDEX_XINFO = {
    "idx_call_events_session_call": (
        (0, 3, "session_id", 0, "BINARY", 1),
        (1, 4, "call_id", 0, "BINARY", 1),
        (2, 0, "seq", 0, "BINARY", 1),
        (3, -1, None, 0, "BINARY", 0),
    ),
    "idx_call_events_session_fingerprint": (
        (0, 3, "session_id", 0, "BINARY", 1),
        (1, 10, "fingerprint", 0, "BINARY", 1),
        (2, 0, "seq", 0, "BINARY", 1),
        (3, -1, None, 0, "BINARY", 0),
    ),
}
_EXPECTED_SQLITE_SEQUENCE_XINFO = (
    (0, "name", "", 0, None, 0, 0),
    (1, "seq", "", 0, None, 0, 0),
)
_SQL_TOKEN_PATTERN = re.compile(
    r"""
    (?P<whitespace>\s+)
    |(?P<line_comment>--[^\r\n]*(?:\r?\n|$))
    |(?P<block_comment>/\*.*?\*/)
    |(?P<single_quote>'(?:''|[^'])*')
    |(?P<double_quote>"(?:""|[^"])*")
    |(?P<backtick>`(?:``|[^`])*`)
    |(?P<bracket>\[(?:\]\]|[^\]])*\])
    |(?P<word>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<number>[0-9]+(?:\.[0-9]+)?)
    |(?P<punctuation>[(),])
    |(?P<other>.)
    """,
    re.DOTALL | re.VERBOSE,
)
T = TypeVar("T")
_TERMINAL_EVENT_TYPES = frozenset({
    "call.completed",
    "call.failed",
    "call.cancelled",
})


def _bounded_integer(
    value: Any,
    field_name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if type(value) is not int or value < minimum or (
        maximum is not None and value > maximum
    ):
        bounds = (
            f"{minimum} through {maximum}"
            if maximum is not None
            else f"at least {minimum}"
        )
        raise ValueError(f"{field_name} must be an integer from {bounds}")
    return value


def _normalized_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise ValueError("now must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _observed_datetime(value: str) -> datetime:
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        return observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc)


def _has_later_terminal(
    events: Sequence[CallEvent],
    index: int,
    start: CallEvent,
) -> bool:
    return any(
        later.span_id == start.span_id and later.event_type in _TERMINAL_EVENT_TYPES
        for later in events[index + 1:]
    )


def _trace_is_active(events: Sequence[CallEvent]) -> bool:
    for index, event in enumerate(events):
        if event.event_type == "call.started" and not _has_later_terminal(
            events,
            index,
            event,
        ):
            return True
        if event.event_type == "session.started" and not any(
            later.event_type == "session.stopped" for later in events[index + 1:]
        ):
            return True
    return False


def _stale_cancellation(start: CallEvent, observed_now: datetime) -> CallEvent:
    timestamp = observed_now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return CallEvent.create(
        session_id=start.session_id,
        call_id=start.call_id,
        parent_call_id=start.parent_call_id,
        phase="cancelled",
        occurred_at=timestamp,
        objective=start.objective,
        route=start.route,
        fingerprint=start.fingerprint,
        budget_kind=start.budget_kind,
        profile=start.profile,
        quality_risk=start.quality_risk,
        mode=start.mode,
        policy_allowed=start.policy_allowed,
        execution_allowed=start.execution_allowed,
        decision_reason=start.decision_reason,
        progress="unknown",
        source=start.source,
        metadata=start.metadata,
        event_type="call.cancelled",
        observed_at=timestamp,
        trace_id=start.trace_id,
        turn_id=start.turn_id,
        span_id=start.span_id,
        parent_span_id=start.parent_span_id,
        source_event="stale_reservation_recovered",
        agent_id=start.agent_id,
        tool_name=start.tool_name,
        input_digest=start.input_digest,
        fingerprint_version=start.fingerprint_version,
        failure_policy=start.failure_policy,
        decision=start.decision,
        reason_code="stale_reservation_recovered",
        policy_version=start.policy_version,
        budget_before=start.budget_before,
        budget_after=start.budget_after,
        status="cancelled",
        raw_input_stored=False,
        policy_facts=start.policy_facts,
    )


def _sql_tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _SQL_TOKEN_PATTERN.finditer(value):
        if match.lastgroup in {"whitespace", "line_comment", "block_comment"}:
            continue
        token = match.group(0)
        tokens.append(token.upper() if match.lastgroup == "word" else token)
    if tokens[:5] == ["CREATE", "TABLE", "IF", "NOT", "EXISTS"]:
        tokens = tokens[:2] + tokens[5:]
    return tuple(tokens)


def _expected_create_table_tokens(
    columns: tuple[tuple[Any, ...], ...],
) -> tuple[str, ...]:
    tokens = ["CREATE", "TABLE", "CALL_EVENTS", "("]
    for index, (name, column_type, notnull, default, primary_key) in enumerate(columns):
        if index:
            tokens.append(",")
        tokens.extend((str(name).upper(), str(column_type).upper()))
        if primary_key:
            tokens.extend(("PRIMARY", "KEY"))
            if name == "seq":
                tokens.append("AUTOINCREMENT")
        elif notnull:
            tokens.extend(("NOT", "NULL"))
        if name == "event_id":
            tokens.append("UNIQUE")
        if default is not None:
            tokens.extend(("DEFAULT", str(default).upper()))
    tokens.append(")")
    return tuple(tokens)


class FutureSchemaError(RuntimeError):
    """Raised when a database was created by a newer governor."""


class UnsupportedLegacySchemaError(RuntimeError):
    """Raised when user-version zero does not match released v0.2."""


class UnsupportedSchemaVersionError(RuntimeError):
    """Raised when no ordered migration exists for a schema version."""


class DuplicateCallIdError(ValueError):
    """Raised when a session attempts to reuse a lifecycle call ID."""

    def __init__(self, session_id: str, call_id: str) -> None:
        self.session_id = session_id
        self.call_id = call_id
        super().__init__(f"call_id already exists in session: {call_id}")


class SecureCleanupIncompleteError(RuntimeError):
    """Logical deletion committed, but physical cleanup must be retried."""

    def __init__(
        self,
        operation: str,
        committed_result: int | tuple[str, ...],
        checkpoint_result: tuple[int, int, int] | None,
    ) -> None:
        self.operation = operation
        self.committed_result = committed_result
        self.checkpoint_result = checkpoint_result
        super().__init__(
            f"{operation}: logical deletion committed but secure cleanup is incomplete; "
            "retry required"
        )


def _secure_cleanup_after_commit(
    connection: sqlite3.Connection,
    *,
    operation: str,
    committed_result: int | tuple[str, ...],
) -> None:
    checkpoint_result: tuple[int, int, int] | None = None
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None:
            raise RuntimeError("WAL checkpoint returned no status")
        values = tuple(checkpoint)
        if len(values) != 3 or any(type(value) is not int for value in values):
            raise RuntimeError("WAL checkpoint returned an invalid status")
        checkpoint_result = (values[0], values[1], values[2])
        busy, log_frames, checkpointed_frames = checkpoint_result
        if (
            busy != 0
            or log_frames < 0
            or checkpointed_frames < 0
            or log_frames != checkpointed_frames
        ):
            raise RuntimeError("WAL checkpoint did not complete")
        connection.execute("VACUUM")
    except Exception as exc:
        raise SecureCleanupIncompleteError(
            operation,
            committed_result,
            checkpoint_result,
        ) from exc


class CallLedger:
    """Persist call lifecycle events and derive policy history per session."""

    def __init__(
        self,
        sqlite_path: str | Path,
        jsonl_path: str | Path | None = None,
        *,
        busy_timeout_ms: int = 30000,
    ) -> None:
        self.busy_timeout_ms = _bounded_integer(
            busy_timeout_ms,
            "busy_timeout_ms",
            minimum=0,
            maximum=60000,
        )
        self.sqlite_path = Path(sqlite_path).expanduser()
        self.jsonl_path = Path(jsonl_path).expanduser() if jsonl_path is not None else None
        self._jsonl_lock = threading.Lock()
        parent_existed = self.sqlite_path.parent.exists()
        self.sqlite_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt" and not parent_existed:
            self.sqlite_path.parent.chmod(0o700)
        if self.jsonl_path is not None:
            warnings.warn(
                "jsonl_path is deprecated; SQLite is the authoritative ledger",
                DeprecationWarning,
                stacklevel=2,
            )
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if os.name != "nt":
            self.sqlite_path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.sqlite_path),
            timeout=self.busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            # Admission is read-only so rejected databases retain their original
            # journal mode and catalog. Recheck under the exclusive transaction
            # after WAL is enabled to guard the mutation boundary.
            self._classify_schema(connection)
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            cleanup_pending = False
            try:
                connection.execute("BEGIN EXCLUSIVE")
                schema_state = self._classify_schema(connection)
                if schema_state == "fresh":
                    self._create_schema_v2(connection)
                    self._verify_schema_v2(connection)
                elif schema_state == "legacy-v0.2":
                    connection.execute(
                        f"PRAGMA application_id = {_MIGRATION_CLEANUP_PENDING}"
                    )
                    self._migrate_v02_to_v2(connection)
                    self._verify_schema_v2(connection)
                    cleanup_pending = True
                elif schema_state == "schema-v2-cleanup-pending":
                    cleanup_pending = True
                else:
                    cleanup_pending = False
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

            if cleanup_pending:
                self._cleanup_migrated_storage(connection)
                try:
                    connection.execute("BEGIN EXCLUSIVE")
                    if (
                        int(connection.execute("PRAGMA application_id").fetchone()[0])
                        == _MIGRATION_CLEANUP_PENDING
                    ):
                        connection.execute("PRAGMA application_id = 0")
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise

    def _classify_schema(self, connection: sqlite3.Connection) -> str:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        if user_version > SCHEMA_VERSION:
            raise FutureSchemaError(
                f"database schema {user_version} requires a newer Agent Call Governor; "
                f"supported={SCHEMA_VERSION}"
            )
        if user_version not in {0, SCHEMA_VERSION}:
            raise UnsupportedSchemaVersionError(
                f"unsupported schema version {user_version}; supported migration is "
                "released v0.2 user_version 0 to schema 2"
            )
        if user_version == SCHEMA_VERSION:
            if application_id not in {0, _MIGRATION_CLEANUP_PENDING}:
                raise UnsupportedSchemaVersionError(
                    "schema version 2 has unsupported application_id "
                    f"{application_id}; expected 0 or the migration cleanup marker"
                )
            self._verify_schema_v2(connection)
            if application_id == _MIGRATION_CLEANUP_PENDING:
                return "schema-v2-cleanup-pending"
            return "schema-v2"

        if application_id != 0:
            raise UnsupportedLegacySchemaError(
                "user-version 0 database has unsupported application_id "
                f"{application_id}; expected 0"
            )

        user_objects = self._user_object_signature(connection)
        if not user_objects:
            return "fresh"
        if not self._table_exists(connection, "call_events"):
            raise UnsupportedLegacySchemaError(
                "nonempty user-version 0 database is not a fresh Agent Call Governor ledger"
            )
        self._verify_legacy_v02_columns(connection)
        self._prepare_legacy_rows(connection)
        return "legacy-v0.2"

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone() is not None

    @staticmethod
    def _user_object_signature(
        connection: sqlite3.Connection,
    ) -> frozenset[tuple[str, str, str]]:
        return frozenset(
            (str(row["type"]), str(row["name"]), str(row["tbl_name"]))
            for row in connection.execute(
                "SELECT type, name, tbl_name FROM sqlite_schema"
            )
        )

    @staticmethod
    def _table_xinfo_signature(
        connection: sqlite3.Connection,
        table_name: str = "call_events",
    ) -> tuple[tuple[Any, ...], ...]:
        escaped_name = table_name.replace('"', '""')
        return tuple(
            (
                int(row["cid"]),
                str(row["name"]),
                str(row["type"]).upper(),
                int(row["notnull"]),
                row["dflt_value"],
                int(row["pk"]),
                int(row["hidden"]),
            )
            for row in connection.execute(f'PRAGMA table_xinfo("{escaped_name}")')
        )

    @staticmethod
    def _expected_table_xinfo(
        columns: tuple[tuple[Any, ...], ...],
    ) -> tuple[tuple[Any, ...], ...]:
        return tuple(
            (cid, name, column_type, notnull, default, primary_key, 0)
            for cid, (name, column_type, notnull, default, primary_key) in enumerate(columns)
        )

    @staticmethod
    def _index_xinfo_signature(
        connection: sqlite3.Connection,
        index_name: str,
    ) -> tuple[tuple[Any, ...], ...]:
        escaped_name = index_name.replace('"', '""')
        return tuple(
            (
                int(row["seqno"]),
                int(row["cid"]),
                row["name"],
                int(row["desc"]),
                str(row["coll"]),
                int(row["key"]),
            )
            for row in connection.execute(f'PRAGMA index_xinfo("{escaped_name}")')
        )

    def _schema_mismatch(
        self,
        connection: sqlite3.Connection,
        expected_columns: tuple[tuple[Any, ...], ...],
    ) -> str | None:
        if self._user_object_signature(connection) != _EXPECTED_SCHEMA_OBJECTS:
            return "unexpected or missing schema objects"
        sequence_row = connection.execute(
            "SELECT rootpage, sql FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'sqlite_sequence'"
        ).fetchone()
        if (
            sequence_row is None
            or int(sequence_row["rootpage"]) <= 0
            or sequence_row["sql"] is None
            or _sql_tokens(str(sequence_row["sql"]))
            != _sql_tokens("CREATE TABLE sqlite_sequence(name,seq)")
            or self._table_xinfo_signature(connection, "sqlite_sequence")
            != _EXPECTED_SQLITE_SEQUENCE_XINFO
        ):
            return "sqlite_sequence definition is incompatible"
        autoindex_row = connection.execute(
            "SELECT rootpage, sql FROM sqlite_schema "
            "WHERE type = 'index' AND name = 'sqlite_autoindex_call_events_1'"
        ).fetchone()
        if (
            autoindex_row is None
            or int(autoindex_row["rootpage"]) <= 0
            or autoindex_row["sql"] is not None
        ):
            return "event_id SQLite autoindex definition is incompatible"
        if self._table_xinfo_signature(connection) != self._expected_table_xinfo(expected_columns):
            return "incompatible call_events columns or hidden definitions"

        table_row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'call_events'"
        ).fetchone()
        table_sql = "" if table_row is None or table_row["sql"] is None else str(table_row["sql"])
        if _sql_tokens(table_sql) != _expected_create_table_tokens(expected_columns):
            return "call_events CREATE TABLE semantics are incompatible"
        if tuple(connection.execute("PRAGMA foreign_key_list(call_events)")):
            return "call_events has unexpected foreign keys"

        indexes = {
            str(row["name"]): (
                int(row["unique"]),
                str(row["origin"]),
                int(row["partial"]),
            )
            for row in connection.execute("PRAGMA index_list(call_events)")
        }
        named_index_names = set(_EXPECTED_INDEX_XINFO)
        auto_indexes = {
            name for name, signature in indexes.items() if signature == (1, "u", 0)
        }
        if set(indexes) != named_index_names | auto_indexes or len(auto_indexes) != 1:
            return "event_id UNIQUE or required index set is incompatible"
        for name in named_index_names:
            if indexes.get(name) != (0, "c", 0):
                return f"required index {name} has incompatible flags"
            if self._index_xinfo_signature(connection, name) != _EXPECTED_INDEX_XINFO[name]:
                return f"required index {name} has incompatible columns"
        auto_index_name = next(iter(auto_indexes))
        expected_auto_xinfo = (
            (0, 1, "event_id", 0, "BINARY", 1),
            (1, -1, None, 0, "BINARY", 0),
        )
        if self._index_xinfo_signature(connection, auto_index_name) != expected_auto_xinfo:
            return "event_id UNIQUE index is incompatible"
        return None

    def _verify_legacy_v02_columns(self, connection: sqlite3.Connection) -> None:
        mismatch = self._schema_mismatch(connection, _LEGACY_V02_COLUMNS)
        if mismatch is not None:
            raise UnsupportedLegacySchemaError(
                "unrecognized legacy schema; expected the released v0.2 schema: "
                f"{mismatch}"
            )

    def _verify_schema_v2(self, connection: sqlite3.Connection) -> None:
        if not self._table_exists(connection, "call_events"):
            raise UnsupportedSchemaVersionError(
                "schema version 2 is missing the call_events table"
            )
        mismatch = self._schema_mismatch(connection, _SCHEMA_V2_COLUMNS)
        if mismatch is not None:
            raise UnsupportedSchemaVersionError(
                f"schema version 2 has incompatible call_events schema: {mismatch}"
            )
        invalid_versions = int(
            connection.execute(
                "SELECT COUNT(*) FROM call_events WHERE schema_version IS NULL OR schema_version <> ?",
                (SCHEMA_VERSION,),
            ).fetchone()[0]
        )
        if invalid_versions:
            raise UnsupportedSchemaVersionError(
                "schema version 2 contains noncanonical event schema_version rows"
            )

    @staticmethod
    def _create_schema_v2(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE call_events (
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
                error_type TEXT,
                event_type TEXT,
                observed_at TEXT,
                trace_id TEXT,
                turn_id TEXT,
                span_id TEXT,
                parent_span_id TEXT,
                source_event TEXT,
                agent_id TEXT,
                tool_name TEXT,
                input_digest TEXT,
                fingerprint_version INTEGER,
                failure_policy TEXT,
                decision TEXT,
                reason_code TEXT,
                policy_version TEXT,
                budget_before INTEGER,
                budget_after INTEGER,
                decision_latency_ms REAL,
                execution_latency_ms REAL,
                status TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                estimated_cost_usd REAL,
                pricing_version TEXT,
                raw_input_stored INTEGER,
                safe_metadata_json TEXT,
                policy_facts_json TEXT
            )
            """
        )
        CallLedger._create_indexes(connection)

    @staticmethod
    def _create_indexes(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_call_events_session_call "
            "ON call_events(session_id, call_id, seq)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_call_events_session_fingerprint "
            "ON call_events(session_id, fingerprint, seq)"
        )

    @staticmethod
    def _prepare_legacy_rows(connection: sqlite3.Connection) -> list[tuple[int, str, str]]:
        prepared_rows: list[tuple[int, str, str]] = []
        for row in connection.execute("SELECT * FROM call_events ORDER BY seq"):
            seq = row["seq"]
            if type(seq) is not int or seq <= 0:
                raise ValueError("legacy row seq must be a positive integer")
            legacy_schema_version = row["schema_version"]
            if type(legacy_schema_version) is not int or legacy_schema_version < 1:
                raise ValueError(
                    f"legacy row {seq} schema_version must be a positive integer"
                )
            phase = row["phase"]
            if phase not in _LEGACY_EVENT_TYPES:
                raise UnsupportedLegacySchemaError(
                    f"unrecognized legacy phase {phase!r}; migration was rolled back"
                )
            metadata_json = row["metadata_json"]
            if not isinstance(metadata_json, str):
                raise ValueError(
                    f"legacy metadata_json for row {seq} must contain a JSON object"
                )
            try:
                decoded = json.loads(metadata_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"legacy metadata_json for row {seq} must contain a JSON object"
                ) from exc
            if not isinstance(decoded, dict):
                raise ValueError(
                    f"legacy metadata_json for row {seq} must contain a JSON object"
                )
            try:
                canonical = CallEvent(
                    event_id=row["event_id"],
                    session_id=row["session_id"],
                    call_id=row["call_id"],
                    parent_call_id=row["parent_call_id"],
                    phase=phase,
                    occurred_at=row["occurred_at"],
                    objective=row["objective"],
                    route=row["route"],
                    fingerprint=row["fingerprint"],
                    budget_kind=row["budget_kind"],
                    profile=row["profile"],
                    quality_risk=row["quality_risk"],
                    mode=row["mode"],
                    policy_allowed=_optional_bool_from_storage(
                        row["policy_allowed"],
                        "legacy policy_allowed",
                    ),
                    execution_allowed=_optional_bool_from_storage(
                        row["execution_allowed"],
                        "legacy execution_allowed",
                    ),
                    decision_reason=row["decision_reason"],
                    progress=row["progress"],
                    duration_ms=row["duration_ms"],
                    source=row["source"],
                    metadata=decoded,
                    error_type=row["error_type"],
                    schema_version=1,
                    event_type=_LEGACY_EVENT_TYPES[phase],
                    observed_at=row["occurred_at"],
                    trace_id=row["session_id"],
                    span_id=row["call_id"],
                    parent_span_id=row["parent_call_id"],
                    source_event="legacy",
                    fingerprint_version=1,
                    failure_policy="legacy-unknown",
                    policy_version="legacy-v0.2",
                    raw_input_stored=False,
                )
                canonical.to_dict()
            except ValueError as exc:
                raise ValueError(f"legacy row {seq} is invalid: {exc}") from exc
            for field_name in (
                "event_id",
                "session_id",
                "call_id",
                "parent_call_id",
                "phase",
                "occurred_at",
                "objective",
                "route",
                "fingerprint",
                "budget_kind",
                "profile",
                "quality_risk",
                "mode",
                "decision_reason",
                "progress",
                "duration_ms",
                "source",
                "error_type",
            ):
                if getattr(canonical, field_name) != row[field_name]:
                    raise ValueError(
                        f"legacy row {seq} {field_name} is not in canonical stored form"
                    )
            safe = dict(canonical.metadata)
            prepared_rows.append(
                (
                    seq,
                    _LEGACY_EVENT_TYPES[phase],
                    json.dumps(safe, ensure_ascii=False, sort_keys=True),
                )
            )
        return prepared_rows

    def _migrate_v02_to_v2(self, connection: sqlite3.Connection) -> None:
        prepared_rows = self._prepare_legacy_rows(connection)
        connection.execute("PRAGMA secure_delete = ON")
        for name, column_type in _V2_ADDED_COLUMNS:
            connection.execute(f"ALTER TABLE call_events ADD COLUMN {name} {column_type}")
        for seq, event_type, safe_json in prepared_rows:
            connection.execute(
                """
                UPDATE call_events
                SET schema_version = ?, event_type = ?, observed_at = occurred_at,
                    trace_id = session_id, span_id = call_id, source_event = 'legacy',
                    fingerprint_version = 1, failure_policy = 'legacy-unknown',
                    policy_version = 'legacy-v0.2', raw_input_stored = 0,
                    metadata_json = ?, safe_metadata_json = ?
                WHERE seq = ?
                """,
                (SCHEMA_VERSION, event_type, safe_json, safe_json, seq),
            )
        self._create_indexes(connection)

    @staticmethod
    def _cleanup_migrated_storage(connection: sqlite3.Connection) -> None:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or int(checkpoint[0]) != 0:
            raise RuntimeError("could not checkpoint legacy WAL for secure migration cleanup")
        connection.execute("VACUUM")

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
        operation: Callable[[list[dict[str, Any]]], tuple[T, Sequence[CallEvent]]],
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
        policy_facts = _validate_event_boundary(event)
        safe_metadata = _sanitized_event_metadata(event)
        safe_metadata_json = json.dumps(
            safe_metadata,
            ensure_ascii=False,
            sort_keys=True,
        )
        policy_facts_json = (
            None
            if policy_facts is None
            else json.dumps(
                policy_facts,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        values = {
            "event_id": event.event_id,
            "schema_version": event.schema_version,
            "session_id": event.session_id,
            "call_id": event.call_id,
            "parent_call_id": event.parent_call_id,
            "phase": event.phase,
            "occurred_at": event.occurred_at,
            "objective": event.objective,
            "route": event.route,
            "fingerprint": event.fingerprint,
            "budget_kind": event.budget_kind,
            "profile": event.profile,
            "quality_risk": event.quality_risk,
            "mode": event.mode,
            "policy_allowed": _optional_bool(event.policy_allowed),
            "execution_allowed": _optional_bool(event.execution_allowed),
            "decision_reason": event.decision_reason,
            "progress": event.progress,
            "duration_ms": event.duration_ms,
            "source": event.source,
            "metadata_json": safe_metadata_json,
            "error_type": event.error_type,
            "event_type": event.event_type,
            "observed_at": event.observed_at,
            "trace_id": event.trace_id,
            "turn_id": event.turn_id,
            "span_id": event.span_id,
            "parent_span_id": event.parent_span_id,
            "source_event": event.source_event,
            "agent_id": event.agent_id,
            "tool_name": event.tool_name,
            "input_digest": event.input_digest,
            "fingerprint_version": event.fingerprint_version,
            "failure_policy": event.failure_policy,
            "decision": event.decision,
            "reason_code": event.reason_code,
            "policy_version": event.policy_version,
            "budget_before": event.budget_before,
            "budget_after": event.budget_after,
            "decision_latency_ms": event.decision_latency_ms,
            "execution_latency_ms": event.execution_latency_ms,
            "status": event.status,
            "prompt_tokens": event.prompt_tokens,
            "completion_tokens": event.completion_tokens,
            "total_tokens": event.total_tokens,
            "estimated_cost_usd": event.estimated_cost_usd,
            "pricing_version": event.pricing_version,
            "raw_input_stored": int(event.raw_input_stored),
            "safe_metadata_json": safe_metadata_json,
            "policy_facts_json": policy_facts_json,
        }
        columns = tuple(values)
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO call_events ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values[column] for column in columns),
        )

    def _append_jsonl(self, events: Sequence[CallEvent]) -> None:
        if self.jsonl_path is None:
            return
        encoded = []
        for event in events:
            value = event.to_dict()
            value["safe_metadata_json"] = _sanitized_event_metadata(event)
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

    def sessions(self) -> list[SessionSummary]:
        """Summarize operational traces."""
        grouped: dict[str, list[CallEvent]] = {}
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM call_events ORDER BY seq").fetchall()
        for row in rows:
            event = _event_from_row(row)
            grouped.setdefault(event.trace_id, []).append(event)

        summaries: list[SessionSummary] = []
        for trace_id, events in grouped.items():
            indexed_events = tuple(enumerate(events))
            _, first_event = min(
                indexed_events,
                key=lambda item: (_observed_datetime(item[1].observed_at), item[0]),
            )
            _, last_event = max(
                indexed_events,
                key=lambda item: (_observed_datetime(item[1].observed_at), item[0]),
            )
            proposed_calls = {
                event.call_id for event in events if event.event_type == "call.proposed"
            }
            summaries.append(
                SessionSummary(
                    session_id=trace_id,
                    first_observed_at=first_event.observed_at,
                    last_observed_at=last_event.observed_at,
                    call_count=len(proposed_calls),
                    event_count=len(events),
                    final_status=(
                        "active" if _trace_is_active(events) else last_event.event_type
                    ),
                )
            )
        return summaries

    def inspect_session(self, session_id: str) -> list[CallEvent]:
        """Return canonical events for one operational trace in sequence order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM call_events WHERE trace_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        return [_event_from_row(row) for row in rows]

    def delete_session(self, session_id: str) -> int:
        """Securely delete one operational trace and reclaim SQLite storage."""
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA secure_delete = ON")
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    "DELETE FROM call_events WHERE trace_id = ?",
                    (session_id,),
                )
                deleted = cursor.rowcount
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            _secure_cleanup_after_commit(
                connection,
                operation="delete_session",
                committed_result=deleted,
            )
        return deleted

    def prune_expired_sessions(
        self,
        retention_days: int,
        *,
        now: datetime | None = None,
    ) -> list[str]:
        """Securely remove closed traces older than the retention cutoff."""
        days = _bounded_integer(retention_days, "retention_days", minimum=1)
        cutoff = _normalized_datetime(now) - timedelta(days=days)
        removed: list[str] = []
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA secure_delete = ON")
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute("SELECT * FROM call_events ORDER BY seq").fetchall()
                grouped: dict[str, list[CallEvent]] = {}
                for row in rows:
                    event = _event_from_row(row)
                    grouped.setdefault(event.trace_id, []).append(event)
                for trace_id, events in grouped.items():
                    last_observed = max(
                        _observed_datetime(event.observed_at) for event in events
                    )
                    if last_observed < cutoff and not _trace_is_active(events):
                        connection.execute(
                            "DELETE FROM call_events WHERE trace_id = ?",
                            (trace_id,),
                        )
                        removed.append(trace_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            _secure_cleanup_after_commit(
                connection,
                operation="prune_expired_sessions",
                committed_result=tuple(removed),
            )
        return removed

    def recover_stale_reservations(
        self,
        trace_id: str,
        stale_after_seconds: int,
        *,
        now: datetime | None = None,
    ) -> int:
        """Append canonical cancellations for stale open calls in one trace."""
        threshold = _bounded_integer(
            stale_after_seconds,
            "stale_after_seconds",
            minimum=0,
        )
        if threshold == 0:
            return 0
        observed_now = _normalized_datetime(now)
        cutoff = observed_now - timedelta(seconds=threshold)
        recovery_events: list[CallEvent] = []
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    "SELECT * FROM call_events WHERE trace_id = ? ORDER BY seq",
                    (trace_id,),
                ).fetchall()
                events = [_event_from_row(row) for row in rows]
                latest_lifecycle: dict[str | None, tuple[int, CallEvent]] = {}
                for index, event in enumerate(events):
                    if event.event_type in _CALL_LIFECYCLE_EVENT_TYPES:
                        latest_lifecycle[event.span_id] = (index, event)
                for _, event in sorted(latest_lifecycle.values()):
                    if (
                        event.event_type == "call.started"
                        and _observed_datetime(event.observed_at) < cutoff
                    ):
                        cancellation = _stale_cancellation(event, observed_now)
                        self._insert(connection, cancellation)
                        recovery_events.append(cancellation)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        self._mirror_best_effort(recovery_events)
        return len(recovery_events)

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

    def history(self, session_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            return self._history(connection, session_id)

    @staticmethod
    def _history(connection: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM call_events WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        latest_lifecycle: dict[str, tuple[int, CallEvent]] = {}
        for index, row in enumerate(rows):
            event = _event_from_row(row)
            if event.event_type in _CALL_LIFECYCLE_EVENT_TYPES:
                latest_lifecycle[event.call_id] = (index, event)

        history: list[dict[str, Any]] = []
        for _, event in sorted(latest_lifecycle.values()):
            if event.event_type not in _COUNTED_EVENT_TYPES:
                continue
            item: dict[str, Any] = {
                "fingerprint": event.fingerprint,
                "budget_kind": event.budget_kind,
            }
            if event.fingerprint_version is not None:
                item["fingerprint_version"] = event.fingerprint_version
            if event.progress in PROGRESS_VALUES:
                item["progress"] = event.progress
            history.append(item)
        return history


def _optional_bool(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _optional_bool_from_storage(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if type(value) is not int or value not in {0, 1}:
        raise ValueError(f"{field_name} must be stored as null, 0, or 1")
    return bool(value)


def _false_from_storage(value: Any, field_name: str) -> bool:
    if type(value) is not int or value != 0:
        raise ValueError(f"{field_name} must be stored as 0")
    return False


def _event_from_row(row: sqlite3.Row) -> CallEvent:
    seq = row["seq"]
    if type(seq) is not int or seq <= 0:
        raise ValueError("stored event seq must be a positive integer")
    try:
        metadata = _json_object_from_storage(
            row["safe_metadata_json"],
            "safe_metadata_json",
        )
        policy_facts = (
            None
            if row["policy_facts_json"] is None
            else _json_object_from_storage(
                row["policy_facts_json"],
                "policy_facts_json",
            )
        )
        event = CallEvent(
            schema_version=row["schema_version"],
            event_id=row["event_id"],
            session_id=row["session_id"],
            call_id=row["call_id"],
            parent_call_id=row["parent_call_id"],
            phase=row["phase"],
            occurred_at=row["occurred_at"],
            objective=row["objective"],
            route=row["route"],
            fingerprint=row["fingerprint"],
            budget_kind=row["budget_kind"],
            profile=row["profile"],
            quality_risk=row["quality_risk"],
            mode=row["mode"],
            event_type=row["event_type"],
            observed_at=row["observed_at"],
            trace_id=row["trace_id"],
            turn_id=row["turn_id"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
            source_event=row["source_event"],
            policy_allowed=_optional_bool_from_storage(
                row["policy_allowed"],
                "policy_allowed",
            ),
            execution_allowed=_optional_bool_from_storage(
                row["execution_allowed"],
                "execution_allowed",
            ),
            decision_reason=row["decision_reason"],
            progress=row["progress"],
            duration_ms=row["duration_ms"],
            source=row["source"],
            metadata=metadata,
            error_type=row["error_type"],
            agent_id=row["agent_id"],
            tool_name=row["tool_name"],
            input_digest=row["input_digest"],
            fingerprint_version=row["fingerprint_version"],
            failure_policy=row["failure_policy"],
            decision=row["decision"],
            reason_code=row["reason_code"],
            policy_version=row["policy_version"],
            budget_before=row["budget_before"],
            budget_after=row["budget_after"],
            decision_latency_ms=row["decision_latency_ms"],
            execution_latency_ms=row["execution_latency_ms"],
            status=row["status"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            total_tokens=row["total_tokens"],
            estimated_cost_usd=row["estimated_cost_usd"],
            pricing_version=row["pricing_version"],
            raw_input_stored=_false_from_storage(
                row["raw_input_stored"],
                "raw_input_stored",
            ),
            policy_facts=policy_facts,
        )
        if row["parent_span_id"] is None:
            object.__setattr__(event, "parent_span_id", None)
        validated_policy_facts = _validate_event_boundary(event)
        _validate_canonical_storage_row(
            row,
            event,
            validated_policy_facts,
        )
    except ValueError as exc:
        raise ValueError(f"stored event row {seq} is invalid: {exc}") from exc
    return event


def _validate_canonical_storage_row(
    row: sqlite3.Row,
    event: CallEvent,
    policy_facts: dict[str, Any] | None,
) -> None:
    scalar_values = {
        "event_id": event.event_id,
        "schema_version": event.schema_version,
        "session_id": event.session_id,
        "call_id": event.call_id,
        "parent_call_id": event.parent_call_id,
        "phase": event.phase,
        "occurred_at": event.occurred_at,
        "objective": event.objective,
        "route": event.route,
        "fingerprint": event.fingerprint,
        "budget_kind": event.budget_kind,
        "profile": event.profile,
        "quality_risk": event.quality_risk,
        "mode": event.mode,
        "policy_allowed": _optional_bool(event.policy_allowed),
        "execution_allowed": _optional_bool(event.execution_allowed),
        "decision_reason": event.decision_reason,
        "progress": event.progress,
        "duration_ms": event.duration_ms,
        "source": event.source,
        "error_type": event.error_type,
        "event_type": event.event_type,
        "observed_at": event.observed_at,
        "trace_id": event.trace_id,
        "turn_id": event.turn_id,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "source_event": event.source_event,
        "agent_id": event.agent_id,
        "tool_name": event.tool_name,
        "input_digest": event.input_digest,
        "fingerprint_version": event.fingerprint_version,
        "failure_policy": event.failure_policy,
        "decision": event.decision,
        "reason_code": event.reason_code,
        "policy_version": event.policy_version,
        "budget_before": event.budget_before,
        "budget_after": event.budget_after,
        "decision_latency_ms": event.decision_latency_ms,
        "execution_latency_ms": event.execution_latency_ms,
        "status": event.status,
        "prompt_tokens": event.prompt_tokens,
        "completion_tokens": event.completion_tokens,
        "total_tokens": event.total_tokens,
        "estimated_cost_usd": event.estimated_cost_usd,
        "pricing_version": event.pricing_version,
        "raw_input_stored": int(event.raw_input_stored),
    }
    for field_name, canonical_value in scalar_values.items():
        stored_value = row[field_name]
        if (
            type(stored_value) is not type(canonical_value)
            or stored_value != canonical_value
        ):
            raise ValueError(
                f"{field_name} is not in canonical schema-v2 stored form"
            )

    safe_metadata_json = json.dumps(
        _sanitized_event_metadata(event),
        ensure_ascii=False,
        sort_keys=True,
    )
    json_values = {
        "safe_metadata_json": safe_metadata_json,
        "metadata_json": safe_metadata_json,
        "policy_facts_json": (
            None
            if policy_facts is None
            else json.dumps(
                policy_facts,
                ensure_ascii=False,
                sort_keys=True,
            )
        ),
    }
    for field_name, canonical_value in json_values.items():
        stored_value = row[field_name]
        if (
            type(stored_value) is not type(canonical_value)
            or stored_value != canonical_value
        ):
            raise ValueError(
                f"{field_name} is not in canonical schema-v2 stored form"
            )


def _json_object_from_storage(value: Any, field_name: str) -> dict[str, Any]:
    if type(value) is not str:
        raise ValueError(f"{field_name} must contain a canonical JSON object string")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must contain a JSON object") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must contain a JSON object")
    return decoded
