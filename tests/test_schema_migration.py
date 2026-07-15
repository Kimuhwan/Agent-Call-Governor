from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from agent_call_governor_runtime import CallEvent
from agent_call_governor_runtime.ledger import (
    CallLedger,
    FutureSchemaError,
    SCHEMA_VERSION,
    UnsupportedLegacySchemaError,
    UnsupportedSchemaVersionError,
)


ROOT = Path(__file__).resolve().parents[1]
LEGACY_COLUMNS = """
CREATE TABLE call_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  schema_version INTEGER NOT NULL, session_id TEXT NOT NULL, call_id TEXT NOT NULL,
  parent_call_id TEXT, phase TEXT NOT NULL, occurred_at TEXT NOT NULL,
  objective TEXT NOT NULL, route TEXT NOT NULL, fingerprint TEXT NOT NULL,
  budget_kind TEXT NOT NULL, profile TEXT NOT NULL, quality_risk TEXT NOT NULL,
  mode TEXT NOT NULL, policy_allowed INTEGER, execution_allowed INTEGER,
  decision_reason TEXT, progress TEXT, duration_ms REAL, source TEXT NOT NULL,
  metadata_json TEXT NOT NULL, error_type TEXT
)
"""
LEGACY_INDEXES = """
CREATE INDEX idx_call_events_session_call
  ON call_events(session_id, call_id, seq);
CREATE INDEX idx_call_events_session_fingerprint
  ON call_events(session_id, fingerprint, seq);
"""
LEGACY_CANARY = "private legacy value"
LEGACY_COLUMN_NAMES = {
    "seq", "event_id", "schema_version", "session_id", "call_id", "parent_call_id",
    "phase", "occurred_at", "objective", "route", "fingerprint", "budget_kind",
    "profile", "quality_risk", "mode", "policy_allowed", "execution_allowed",
    "decision_reason", "progress", "duration_ms", "source", "metadata_json", "error_type",
}
V2_ADDED_COLUMN_NAMES = {
    "event_type", "observed_at", "trace_id", "turn_id", "span_id", "parent_span_id",
    "source_event", "agent_id", "tool_name", "input_digest", "fingerprint_version",
    "failure_policy", "decision", "reason_code", "policy_version", "budget_before",
    "budget_after", "decision_latency_ms", "execution_latency_ms", "status",
    "prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost_usd",
    "pricing_version", "raw_input_stored", "safe_metadata_json", "policy_facts_json",
}


def create_v02_database(
    path: Path,
    *,
    metadata: dict[str, object] | None = None,
    wal: bool = False,
    keep_open: bool = False,
) -> sqlite3.Connection | None:
    """Create the exact released-v0.2 table shape and one lifecycle row."""
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute(LEGACY_COLUMNS)
    connection.executescript(LEGACY_INDEXES)
    connection.execute(
        "INSERT INTO call_events VALUES "
        "(1,'event-1',1,'session-ref','call-ref',NULL,'completed','2026-07-13T00:00:00Z',"
        "'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',"
        "'Bash','sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',"
        "'direct-tool','balanced','medium','observe',1,1,'allowed','material_progress',1.5,"
        "'runtime',?,NULL)",
        (json.dumps(metadata or {"custom_note": LEGACY_CANARY}),),
    )
    connection.commit()
    if keep_open:
        return connection
    connection.close()
    return None


def create_legacy_schema(
    path: Path,
    *,
    table_sql: str = LEGACY_COLUMNS,
    indexes_sql: str = LEGACY_INDEXES,
) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(table_sql)
    if indexes_sql:
        connection.executescript(indexes_sql)
    connection.commit()
    return connection


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "events.sqlite3"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_fresh_database_is_schema_v2(self) -> None:
        CallLedger(self.path)
        connection = sqlite3.connect(self.path)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(call_events)")}
        self.assertEqual(columns, LEGACY_COLUMN_NAMES | V2_ADDED_COLUMN_NAMES)
        connection.close()

    def test_v02_migration_preserves_event_and_removes_plaintext_metadata(self) -> None:
        create_v02_database(self.path)
        self.assertIn(LEGACY_CANARY, self.path.read_bytes().decode("utf-8", errors="ignore"))

        ledger = CallLedger(self.path)

        events = ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].schema_version, SCHEMA_VERSION)
        self.assertEqual(events[0].event_type, "call.completed")
        self.assertEqual(events[0].fingerprint_version, 1)
        self.assertEqual(events[0].policy_version, "legacy-v0.2")
        event_schema = json.loads(
            (ROOT / "schemas" / "event-v2.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(event_schema).validate(events[0].to_dict())
        self.assertNotIn(LEGACY_CANARY, self.path.read_bytes().decode("utf-8", errors="ignore"))

    def test_migration_is_idempotent_on_reopen(self) -> None:
        create_v02_database(self.path)
        CallLedger(self.path)
        CallLedger(self.path)
        self.assertEqual(len(CallLedger(self.path).events()), 1)

    def test_migrated_fingerprint_version_is_exposed_in_history(self) -> None:
        create_v02_database(self.path)
        ledger = CallLedger(self.path)
        history = ledger.history("session-ref")
        self.assertEqual(history[0]["fingerprint_version"], 1)
        self.assertEqual(history[0]["progress"], "material_progress")

    def test_unknown_future_schema_is_rejected(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(FutureSchemaError, "newer Agent Call Governor"):
            CallLedger(self.path)

    def test_unreleased_intermediate_schema_version_is_rejected(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA user_version = 1")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(UnsupportedSchemaVersionError, "unsupported schema version 1"):
            CallLedger(self.path)

    def test_unknown_user_version_zero_shape_is_rejected_without_mutation(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE call_events (seq INTEGER PRIMARY KEY, unknown TEXT)")
        connection.commit()
        before = tuple(connection.execute("PRAGMA table_info(call_events)"))
        journal_mode_before = connection.execute("PRAGMA journal_mode").fetchone()[0]
        connection.close()

        with self.assertRaisesRegex(UnsupportedLegacySchemaError, "unrecognized legacy schema"):
            CallLedger(self.path)

        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(tuple(connection.execute("PRAGMA table_info(call_events)")), before)
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0],
                journal_mode_before,
            )

    def test_nonempty_user_version_zero_database_is_not_claimed_or_mutated(self) -> None:
        cases = {
            "table": "CREATE TABLE sentinel (value TEXT)",
            "view": "CREATE VIEW sentinel_view AS SELECT 1 AS value",
        }
        for name, statement in cases.items():
            with self.subTest(object_type=name):
                path = self.path.with_name(f"unrelated-{name}.sqlite3")
                connection = sqlite3.connect(path)
                connection.execute(statement)
                connection.commit()
                before = tuple(
                    connection.execute(
                        "SELECT type, name, sql FROM sqlite_schema "
                        "WHERE substr(lower(name), 1, 7) <> 'sqlite_' ORDER BY type, name"
                    )
                )
                journal_mode_before = connection.execute("PRAGMA journal_mode").fetchone()[0]
                connection.close()

                with self.assertRaisesRegex(
                    UnsupportedLegacySchemaError,
                    "nonempty user-version 0 database",
                ):
                    CallLedger(path)

                connection = sqlite3.connect(path)
                after = tuple(
                    connection.execute(
                        "SELECT type, name, sql FROM sqlite_schema "
                        "WHERE substr(lower(name), 1, 7) <> 'sqlite_' ORDER BY type, name"
                    )
                )
                self.assertEqual(after, before)
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0],
                    journal_mode_before,
                )
                connection.close()

    def test_legacy_lookalikes_without_autoincrement_or_unique_are_rejected(self) -> None:
        cases = {
            "autoincrement": LEGACY_COLUMNS.replace(" AUTOINCREMENT", ""),
            "unique": LEGACY_COLUMNS.replace(" UNIQUE", ""),
        }
        for name, table_sql in cases.items():
            with self.subTest(missing=name):
                path = self.path.with_name(f"legacy-no-{name}.sqlite3")
                create_legacy_schema(path, table_sql=table_sql).close()

                with self.assertRaisesRegex(
                    UnsupportedLegacySchemaError,
                    "released v0.2 schema",
                ):
                    CallLedger(path)

    def test_legacy_lookalikes_with_missing_or_wrong_indexes_are_rejected(self) -> None:
        cases = {
            "missing": "DROP INDEX idx_call_events_session_fingerprint",
            "wrong": (
                "DROP INDEX idx_call_events_session_call;"
                "CREATE INDEX idx_call_events_session_call "
                "ON call_events(call_id, session_id, seq);"
            ),
        }
        for name, mutation in cases.items():
            with self.subTest(index_case=name):
                path = self.path.with_name(f"legacy-index-{name}.sqlite3")
                connection = create_legacy_schema(path)
                connection.executescript(mutation)
                connection.commit()
                connection.close()

                with self.assertRaisesRegex(
                    UnsupportedLegacySchemaError,
                    "released v0.2 schema",
                ):
                    CallLedger(path)

    def test_legacy_generated_column_and_extra_trigger_are_rejected(self) -> None:
        generated_table = LEGACY_COLUMNS.replace(
            "  metadata_json TEXT NOT NULL, error_type TEXT\n)",
            "  metadata_json TEXT NOT NULL, error_type TEXT,\n"
            "  generated_probe INTEGER GENERATED ALWAYS AS (length(event_id)) VIRTUAL\n)",
        )
        cases = {
            "generated": (generated_table, None),
            "trigger": (
                LEGACY_COLUMNS,
                "CREATE TRIGGER unexpected_trigger AFTER INSERT ON call_events "
                "BEGIN SELECT 1; END",
            ),
        }
        for name, (table_sql, mutation) in cases.items():
            with self.subTest(schema_case=name):
                path = self.path.with_name(f"legacy-{name}.sqlite3")
                connection = create_legacy_schema(path, table_sql=table_sql)
                if mutation is not None:
                    connection.execute(mutation)
                    connection.commit()
                connection.close()

                with self.assertRaisesRegex(
                    UnsupportedLegacySchemaError,
                    "released v0.2 schema",
                ):
                    CallLedger(path)

    def test_legacy_extra_table_constraints_are_rejected(self) -> None:
        cases = {
            "check": LEGACY_COLUMNS.replace(
                "error_type TEXT",
                "error_type TEXT CHECK(error_type IS NULL)",
            ),
            "collate": LEGACY_COLUMNS.replace(
                "error_type TEXT",
                'error_type TEXT COLLATE"NOCASE"',
            ),
            "conflict-comment": LEGACY_COLUMNS.replace(
                "event_id TEXT NOT NULL UNIQUE",
                "event_id TEXT NOT NULL ON/**/CONFLICT FAIL UNIQUE",
            ),
            "strict": LEGACY_COLUMNS.rstrip() + "STRICT\n",
        }
        for name, table_sql in cases.items():
            with self.subTest(constraint=name):
                path = self.path.with_name(f"legacy-constraint-{name}.sqlite3")
                create_legacy_schema(path, table_sql=table_sql).close()

                with self.assertRaisesRegex(
                    UnsupportedLegacySchemaError,
                    "released v0.2 schema",
                ):
                    CallLedger(path)

    def test_declared_v2_with_incompatible_shape_is_rejected(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE call_events (seq INTEGER PRIMARY KEY)")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
        journal_mode_before = connection.execute("PRAGMA journal_mode").fetchone()[0]
        connection.close()

        with self.assertRaisesRegex(
            UnsupportedSchemaVersionError,
            "incompatible call_events",
        ):
            CallLedger(self.path)

        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0],
                journal_mode_before,
            )

    def test_declared_v2_semantic_lookalikes_are_rejected(self) -> None:
        cases = {
            "missing-index": "DROP INDEX idx_call_events_session_fingerprint",
            "wrong-index": (
                "DROP INDEX idx_call_events_session_call;"
                "CREATE INDEX idx_call_events_session_call "
                "ON call_events(call_id, session_id, seq);"
            ),
            "extra-trigger": (
                "CREATE TRIGGER unexpected_trigger AFTER INSERT ON call_events "
                "BEGIN SELECT 1; END"
            ),
            "extra-table": "CREATE TABLE unexpected_table (value TEXT)",
            "generated-column": (
                "ALTER TABLE call_events ADD COLUMN generated_probe INTEGER "
                "GENERATED ALWAYS AS (length(event_id)) VIRTUAL"
            ),
        }
        for name, mutation in cases.items():
            with self.subTest(schema_case=name):
                path = self.path.with_name(f"v2-{name}.sqlite3")
                CallLedger(path)
                connection = sqlite3.connect(path)
                connection.executescript(mutation)
                connection.commit()
                connection.close()

                with self.assertRaisesRegex(
                    UnsupportedSchemaVersionError,
                    "schema version 2 has incompatible",
                ):
                    CallLedger(path)

    def test_declared_v2_extra_table_constraint_is_rejected(self) -> None:
        CallLedger(self.path)
        with closing(sqlite3.connect(self.path)) as connection:
            table_sql = connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = 'call_events'"
            ).fetchone()[0]
            tampered_sql = table_sql.replace(
                "error_type TEXT,",
                "error_type TEXT CHECK(error_type IS NULL),",
            )
            self.assertNotEqual(tampered_sql, table_sql)
            connection.execute("PRAGMA writable_schema = ON")
            connection.execute(
                "UPDATE sqlite_schema SET sql = ? WHERE type = 'table' AND name = 'call_events'",
                (tampered_sql,),
            )
            schema_cookie = connection.execute("PRAGMA schema_version").fetchone()[0]
            connection.execute(f"PRAGMA schema_version = {schema_cookie + 1}")
            connection.execute("PRAGMA writable_schema = OFF")
            connection.commit()

        with self.assertRaisesRegex(
            UnsupportedSchemaVersionError,
            "schema version 2 has incompatible",
        ):
            CallLedger(self.path)

    def test_declared_v2_rejects_noncanonical_event_schema_versions(self) -> None:
        create_v02_database(self.path)
        CallLedger(self.path)
        connection = sqlite3.connect(self.path)
        connection.execute("UPDATE call_events SET schema_version = 1")
        connection.commit()
        connection.close()

        with self.assertRaisesRegex(
            UnsupportedSchemaVersionError,
            "noncanonical event schema_version",
        ):
            CallLedger(self.path)

    def test_failed_migration_rolls_back_and_can_succeed_on_reopen(self) -> None:
        create_v02_database(self.path)
        connection = sqlite3.connect(self.path)
        connection.execute("UPDATE call_events SET metadata_json = '{not-json'")
        connection.commit()
        journal_mode_before = connection.execute("PRAGMA journal_mode").fetchone()[0]
        connection.close()
        database_bytes_before = self.path.read_bytes()

        with self.assertRaisesRegex(ValueError, "legacy metadata_json"):
            CallLedger(self.path)

        self.assertEqual(self.path.read_bytes(), database_bytes_before)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0],
                journal_mode_before,
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(call_events)")}
            self.assertNotIn("event_type", columns)
            connection.execute(
                "UPDATE call_events SET metadata_json = ?",
                (json.dumps({"custom_note": LEGACY_CANARY}),),
            )
            connection.commit()

        reopened = CallLedger(self.path)
        self.assertEqual(reopened.events()[0].fingerprint_version, 1)

    def test_migration_truncates_legacy_wal_and_vacuums_free_pages(self) -> None:
        wal_canary = "WAL-PRIVACY-CANARY-must-not-survive"
        large_private_value = wal_canary + ("x" * 64_000)
        legacy_connection = create_v02_database(
            self.path,
            metadata={"custom_note": large_private_value},
            wal=True,
            keep_open=True,
        )
        assert legacy_connection is not None
        try:
            wal_path = Path(str(self.path) + "-wal")
            self.assertTrue(wal_path.exists())
            self.assertIn(wal_canary.encode(), wal_path.read_bytes())
            pages_before = legacy_connection.execute("PRAGMA page_count").fetchone()[0]

            CallLedger(self.path)

            with closing(sqlite3.connect(self.path)) as connection:
                pages_after = connection.execute("PRAGMA page_count").fetchone()[0]
                self.assertEqual(connection.execute("PRAGMA freelist_count").fetchone()[0], 0)
            self.assertLess(pages_after, pages_before)
            for candidate in (self.path, wal_path, Path(str(self.path) + "-shm")):
                if candidate.exists():
                    self.assertNotIn(wal_canary.encode(), candidate.read_bytes(), candidate.name)
        finally:
            legacy_connection.close()

    def test_cleanup_marker_resumes_privacy_scrub_after_post_commit_failure(self) -> None:
        create_v02_database(self.path)
        with patch.object(
            CallLedger,
            "_cleanup_migrated_storage",
            side_effect=RuntimeError("simulated cleanup interruption"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated cleanup interruption"):
                CallLedger(self.path)

        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertNotEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0)

        CallLedger(self.path)

        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA application_id").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA freelist_count").fetchone()[0], 0)
        for candidate in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            if candidate.exists():
                self.assertNotIn(LEGACY_CANARY.encode(), candidate.read_bytes(), candidate.name)

    def test_exported_event_and_policy_facts_match_checked_in_schemas(self) -> None:
        facts = {
            "fingerprint": "sha256:" + "b" * 64,
            "fingerprint_version": 2,
            "budget_kind": "agent",
            "requested_budget_limit": 2,
            "profile": "balanced",
            "risk": "medium",
            "mandatory_reason": None,
            "changed_strategy_present": False,
            "required_fields_present": True,
            "duplicate_scope": "turn",
            "state_token_digest": None,
            "policy_facts_version": 1,
        }
        event = CallEvent.create(
            session_id="sha256:" + "1" * 64,
            call_id="sha256:" + "2" * 64,
            phase="proposed",
            occurred_at="2026-07-14T00:00:00Z",
            objective="sha256:" + "a" * 64,
            route="Bash",
            fingerprint="sha256:" + "b" * 64,
            budget_kind="agent",
            profile="balanced",
            quality_risk="medium",
            mode="observe",
            event_type="call.proposed",
            observed_at="2026-07-14T00:00:00Z",
            trace_id="sha256:" + "1" * 64,
            turn_id="sha256:" + "3" * 64,
            span_id="sha256:" + "2" * 64,
            parent_span_id=None,
            source="runtime",
            source_event="test",
            input_digest="sha256:" + "c" * 64,
            fingerprint_version=2,
            failure_policy="fail-open",
            policy_facts=facts,
            raw_input_stored=False,
        )
        facts_schema = json.loads(
            (ROOT / "schemas" / "policy-facts-v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(facts_schema)
        Draft202012Validator(facts_schema).validate(event.to_dict()["policy_facts_json"])

        event_schema_paths = (ROOT / "schemas" / "event-v2.schema.json",)
        for path in event_schema_paths:
            self.assertTrue(path.is_file(), f"missing canonical event schema: {path.name}")

        facts_contract = {
            key: value
            for key, value in facts_schema.items()
            if key not in {"$schema", "$id", "title"}
        }
        for path in event_schema_paths:
            with self.subTest(event_schema=path.name):
                event_schema = json.loads(path.read_text(encoding="utf-8"))
                Draft202012Validator.check_schema(event_schema)
                self.assertEqual(
                    event_schema["$defs"]["policy_facts_v1"],
                    facts_contract,
                )
                self.assertEqual(
                    event_schema["properties"]["policy_facts_json"],
                    {
                        "oneOf": [
                            {"type": "null"},
                            {"$ref": "#/$defs/policy_facts_v1"},
                        ]
                    },
                )
                encoded_schema = json.dumps(event_schema, sort_keys=True)
                self.assertNotIn('"$ref": "http', encoded_schema)
                self.assertNotIn('"$ref": "policy-facts', encoded_schema)

                validator = Draft202012Validator(event_schema)
                validator.validate(event.to_dict())

                null_facts = event.to_dict()
                null_facts["policy_facts_json"] = None
                validator.validate(null_facts)

                raw_facts = event.to_dict()
                raw_facts["policy_facts_json"] = {"raw_prompt": "SECRET"}
                with self.assertRaises(ValidationError):
                    validator.validate(raw_facts)

                legacy_event_facts = event.to_dict()
                legacy_event_facts["policy_facts_json"] = dict(
                    facts,
                    fingerprint_version=1,
                )
                with self.assertRaises(ValidationError):
                    validator.validate(legacy_event_facts)

                unsafe = event.to_dict()
                unsafe["raw_input_stored"] = True
                with self.assertRaises(ValidationError):
                    validator.validate(unsafe)

        legacy_facts = dict(facts, fingerprint_version=1)
        with self.assertRaises(ValidationError):
            Draft202012Validator(facts_schema).validate(legacy_facts)


if __name__ == "__main__":
    unittest.main()
