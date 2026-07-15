from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from agent_call_governor_runtime.cli import export_jsonl
from agent_call_governor_runtime.ledger import CallLedger
from agent_call_governor_runtime.models import CallEvent
from agent_call_governor_runtime.redaction import redact_text, sanitize_event_dict, sanitize_metadata


def sha256_reference(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def custom_key(value: str) -> str:
    return "custom:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class ExpandablePurePosixPath(PurePosixPath):
    def expanduser(self) -> PurePosixPath:
        return self


class RedactionTests(unittest.TestCase):
    def test_text_redacts_credentials_email_and_home_path(self) -> None:
        raw = (
            "Bearer sk-secret-123 rk-independent-credential-456 user@example.com "
            r"C:/Users/Ada/private.txt C:\Users\Ada\other.txt"
        )
        safe = redact_text(raw, home_directory="C:/Users/Ada")
        self.assertNotIn("sk-secret-123", safe)
        self.assertNotIn("rk-independent-credential-456", safe)
        self.assertNotIn("user@example.com", safe)
        self.assertNotIn("C:/Users/Ada", safe)
        self.assertNotIn(r"C:\Users\Ada", safe)
        self.assertIn("[REDACTED", safe)

    def test_explicit_home_redacts_both_separators_under_posix_normalization(self) -> None:
        forward = "c:/users/ADA/forward.txt"
        backward = r"C:\USERS\ada\backward.txt"

        with patch(
            "agent_call_governor_runtime.redaction.Path",
            ExpandablePurePosixPath,
        ):
            safe = redact_text(
                f"{forward} {backward}",
                home_directory="C:/Users/Ada",
            )

        self.assertNotIn(forward, safe)
        self.assertNotIn(backward, safe)
        self.assertEqual(safe.count("[REDACTED_HOME]"), 2)

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

    def test_nested_unknown_keys_and_primitives_are_hashed(self) -> None:
        account_id = 3141592653589793
        retry_count = 2718281828459045
        email_key = "user@example.com"
        list_email_key = "list-user@example.com"
        safe = sanitize_metadata(
            {
                "debug": {
                    "tool_input": {"account_id": account_id},
                    email_key: True,
                    "retry_count": retry_count,
                    "items": [{list_email_key: False, "count": 42}],
                }
            },
            source="runtime",
        )

        debug = safe[custom_key("debug")]
        self.assertEqual(debug["tool_input"], "[REDACTED]")
        self.assertEqual(debug[custom_key(email_key)], sha256_reference("True"))
        self.assertEqual(debug[custom_key("retry_count")], sha256_reference(str(retry_count)))
        items = debug[custom_key("items")]
        self.assertEqual(items[0][custom_key(list_email_key)], sha256_reference("False"))
        self.assertEqual(items[0][custom_key("count")], sha256_reference("42"))
        encoded = json.dumps(safe, sort_keys=True)
        for canary in (
            "account_id",
            str(account_id),
            email_key,
            list_email_key,
            "retry_count",
            str(retry_count),
        ):
            self.assertNotIn(canary, encoded)

    def test_nested_canaries_do_not_survive_model_persistence_or_export(self) -> None:
        account_id = 3141592653589793
        retry_count = 2718281828459045
        email_key = "nested-canary@example.com"
        raw_metadata = {
            "debug": {
                "tool_input": {"account_id": account_id},
                email_key: True,
                "retry_count": retry_count,
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "events.sqlite3"
            mirror = root / "events.jsonl"
            exported = root / "export.jsonl"
            ledger = CallLedger(database, mirror)
            event = CallEvent.create(
                session_id="session-nested",
                call_id="call-nested",
                phase="started",
                objective="Inspect nested metadata",
                route="agent:reviewer",
                fingerprint=sha256_reference("fp-nested"),
                budget_kind="agent",
                profile="balanced",
                quality_risk="medium",
                mode="observe",
                source="runtime",
                metadata=raw_metadata,
            )
            model_metadata = json.dumps(event.metadata, sort_keys=True)
            event.metadata["debug"] = raw_metadata["debug"]

            ledger.append(event)
            export_jsonl([event], exported, database)

            with closing(sqlite3.connect(database)) as connection:
                sqlite_metadata = str(connection.execute(
                    "SELECT metadata_json FROM call_events"
                ).fetchone()[0])
            representations = {
                "model": model_metadata,
                "sqlite": sqlite_metadata,
                "jsonl": mirror.read_text(encoding="utf-8"),
                "export": exported.read_text(encoding="utf-8"),
            }
            for boundary, encoded in representations.items():
                with self.subTest(boundary=boundary):
                    self.assertIn("[REDACTED]", encoded)
                    self.assertNotIn('"debug"', encoded)
                    self.assertNotIn("account_id", encoded)
                    self.assertNotIn(str(account_id), encoded)
                    self.assertNotIn(email_key, encoded)
                    self.assertNotIn("retry_count", encoded)
                    self.assertNotIn(str(retry_count), encoded)

    def test_malformed_sha256_references_are_rehashed(self) -> None:
        key = custom_key("field")
        for forged in (
            "sha256:" + "g" * 64,
            "sha256:" + "A" * 64,
        ):
            with self.subTest(forged=forged):
                safe = sanitize_metadata({key: forged}, source="runtime")
                self.assertEqual(safe[key], sha256_reference(forged))
                self.assertNotIn(forged, json.dumps(safe, sort_keys=True))

    def test_malformed_custom_keys_are_hashed(self) -> None:
        for forged in (
            "custom:" + "g" * 64,
            "custom:" + "A" * 64,
        ):
            with self.subTest(forged=forged):
                expected = custom_key(forged)
                safe = sanitize_metadata({forged: "private"}, source="runtime")
                self.assertEqual(safe, {expected: sha256_reference("private")})
                self.assertNotIn(forged, safe)

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
                fingerprint=sha256_reference("fp-1"),
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
