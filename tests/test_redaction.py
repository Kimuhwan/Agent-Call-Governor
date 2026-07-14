from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from agent_call_governor_runtime.cli import export_jsonl
from agent_call_governor_runtime.ledger import CallLedger
from agent_call_governor_runtime.models import CallEvent
from agent_call_governor_runtime.redaction import redact_text, sanitize_event_dict, sanitize_metadata


class RedactionTests(unittest.TestCase):
    def test_text_redacts_credentials_email_and_home_path(self) -> None:
        raw = "Bearer sk-secret-123 user@example.com C:/Users/Ada/private.txt"
        safe = redact_text(raw, home_directory="C:/Users/Ada")
        self.assertNotIn("sk-secret-123", safe)
        self.assertNotIn("user@example.com", safe)
        self.assertNotIn("C:/Users/Ada", safe)
        self.assertIn("[REDACTED", safe)

    def test_known_fields_keep_safe_scalars_and_unknown_strings_are_hashed(self) -> None:
        safe = sanitize_metadata(
            {"exit_code": 0, "file_changed": True, "custom_note": "private prompt"},
            source="codex-hook",
        )
        self.assertEqual(safe["exit_code"], 0)
        self.assertTrue(safe["file_changed"])
        encoded = json.dumps(safe, sort_keys=True)
        self.assertNotIn("private prompt", encoded)
        self.assertIn("sha256:", encoded)

    def test_allowlisted_string_field_rejects_non_enum_text(self) -> None:
        safe = sanitize_metadata({"test_status": "private test transcript"}, source="codex-hook")
        self.assertNotIn("private test transcript", json.dumps(safe, sort_keys=True))

    def test_allowlisted_enum_field_hashes_non_string_input(self) -> None:
        canary = "private nested test transcript"
        try:
            safe = sanitize_metadata(
                {"test_status": {"transcript": canary}},
                source="codex-hook",
            )
        except TypeError as exc:
            self.fail(f"non-string enum metadata must be sanitized: {exc}")
        self.assertTrue(str(safe["test_status"]).startswith("sha256:"))
        self.assertNotIn(canary, json.dumps(safe, sort_keys=True))

    def test_nested_input_output_and_exception_messages_do_not_survive(self) -> None:
        event = sanitize_event_dict({
            "source": "codex-hook",
            "metadata": {
                "tool_input": {"query": "private input"},
                "tool_output": "private output",
                "exception_message": "private exception",
            },
        })
        encoded = json.dumps(event, sort_keys=True)
        for canary in ("private input", "private output", "private exception"):
            self.assertNotIn(canary, encoded)

    def test_sanitization_is_idempotent(self) -> None:
        once = sanitize_metadata({"custom": "secret"}, source="runtime")
        twice = sanitize_metadata(once, source="runtime")
        self.assertEqual(once, twice)

    def test_persistence_and_export_resanitize_mutated_metadata(self) -> None:
        raw_key = "private_key_canary"
        canary = "PERSISTENCE-CANARY-must-not-survive"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "events.sqlite3"
            mirror = root / "events.jsonl"
            exported = root / "export.jsonl"
            ledger = CallLedger(database, mirror)
            event = CallEvent.create(
                session_id="session-1",
                call_id="call-1",
                phase="started",
                objective="Inspect one failure",
                route="agent:reviewer",
                fingerprint="fp-1",
                budget_kind="agent",
                profile="balanced",
                quality_risk="medium",
                mode="observe",
                source="runtime",
            )
            event.metadata[raw_key] = canary

            ledger.append(event)
            export_jsonl([event], exported, database)

            with closing(sqlite3.connect(database)) as connection:
                sqlite_metadata = str(connection.execute(
                    "SELECT metadata_json FROM call_events"
                ).fetchone()[0])
            representations = {
                "sqlite": sqlite_metadata,
                "jsonl": mirror.read_text(encoding="utf-8"),
                "export": exported.read_text(encoding="utf-8"),
            }
            for boundary, encoded in representations.items():
                with self.subTest(boundary=boundary):
                    self.assertNotIn(raw_key, encoded)
                    self.assertNotIn(canary, encoded)


if __name__ == "__main__":
    unittest.main()
