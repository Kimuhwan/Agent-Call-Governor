from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_call_governor_runtime import CallEvent, CallLedger, SessionSummary
from tests.helpers import make_event


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


class LedgerOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "private" / "events.sqlite3"
        self.ledger = CallLedger(self.db, busy_timeout_ms=1000)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def event_for_trace(
        *,
        trace_id: str,
        session_id: str,
        call_id: str,
        event_type: str = "call.completed",
        observed_at: str = "2026-07-14T00:00:00Z",
        span_id: str | None = None,
    ) -> CallEvent:
        value = make_event(
            session_id=session_id,
            call_id=call_id,
            event_type=event_type,
            observed_at=observed_at,
        ).to_dict()
        value["trace_id"] = trace_id
        if span_id is not None:
            value["span_id"] = span_id
        return CallEvent.from_dict(value)

    def test_busy_timeout_and_session_summary(self) -> None:
        connection = self.ledger._connect()
        self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 1000)
        connection.close()
        self.assertEqual(self.ledger.sessions(), [])

    def test_busy_timeout_rejects_values_outside_the_bounded_integer_range(self) -> None:
        for value in (-1, 60001, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "busy_timeout_ms"):
                    CallLedger(self.db.with_name(f"invalid-{value}.sqlite3"), busy_timeout_ms=value)

    def test_session_summaries_and_inspection_group_by_trace(self) -> None:
        self.ledger.append(self.event_for_trace(
            trace_id="trace-a", session_id="turn-1", call_id="call-1",
            event_type="call.proposed", observed_at="2026-07-13T09:00:00Z",
        ))
        self.ledger.append(self.event_for_trace(
            trace_id="trace-a", session_id="turn-2", call_id="call-1",
            event_type="call.started", observed_at="2026-07-13T10:00:00Z",
        ))
        self.ledger.append(self.event_for_trace(
            trace_id="trace-b", session_id="turn-3", call_id="call-2",
            event_type="call.proposed", observed_at="2026-07-13T11:00:00Z",
        ))
        self.ledger.append(self.event_for_trace(
            trace_id="trace-b", session_id="turn-3", call_id="call-2",
            event_type="call.completed", observed_at="2026-07-13T12:00:00Z",
        ))

        summaries = {
            summary.session_id: summary for summary in self.ledger.sessions()
        }
        self.assertEqual(
            summaries,
            {
                "trace-a": SessionSummary(
                    session_id="trace-a",
                    first_observed_at="2026-07-13T09:00:00Z",
                    last_observed_at="2026-07-13T10:00:00Z",
                    call_count=1,
                    event_count=2,
                    final_status="active",
                ),
                "trace-b": SessionSummary(
                    session_id="trace-b",
                    first_observed_at="2026-07-13T11:00:00Z",
                    last_observed_at="2026-07-13T12:00:00Z",
                    call_count=1,
                    event_count=2,
                    final_status="call.completed",
                ),
            },
        )
        self.assertEqual(
            [event.event_type for event in self.ledger.inspect_session("trace-a")],
            ["call.proposed", "call.started"],
        )
        self.assertEqual(
            [event.event_type for event in self.ledger.events("turn-1")],
            ["call.proposed"],
        )

    def test_session_summary_reads_use_the_strict_canonical_decoder(self) -> None:
        self.ledger.append(make_event(session_id="trace", call_id="call-1"))
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "UPDATE call_events SET raw_input_stored = 1 WHERE trace_id = ?",
                ("trace",),
            )
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(ValueError, "raw_input_stored"):
            self.ledger.sessions()

    def test_retention_never_deletes_open_session(self) -> None:
        self.ledger.append(make_event(
            session_id="open", call_id="call-1", event_type="call.started",
            observed_at="2026-07-01T00:00:00Z"
        ))
        removed = self.ledger.prune_expired_sessions(7, now=NOW)
        self.assertEqual(removed, [])
        self.assertEqual(len(self.ledger.inspect_session("open")), 1)

    def test_retention_keeps_idle_codex_session_without_stop(self) -> None:
        self.ledger.append(make_event(
            session_id="idle", call_id="session-start", event_type="session.started",
            observed_at="2026-07-01T00:00:00Z"
        ))
        self.assertEqual(self.ledger.prune_expired_sessions(7, now=NOW), [])
        self.assertEqual(len(self.ledger.inspect_session("idle")), 1)

    def test_retention_deletes_only_expired_closed_traces(self) -> None:
        self.ledger.append(make_event(
            session_id="expired", call_id="call-1", event_type="call.completed",
            observed_at="2026-07-01T00:00:00Z",
        ))
        self.ledger.append(make_event(
            session_id="fresh", call_id="call-2", event_type="call.completed",
            observed_at="2026-07-14T00:00:00Z",
        ))

        self.assertEqual(self.ledger.prune_expired_sessions(7, now=NOW), ["expired"])
        self.assertEqual(self.ledger.inspect_session("expired"), [])
        self.assertEqual(len(self.ledger.inspect_session("fresh")), 1)

    def test_retention_and_recovery_validate_integer_thresholds(self) -> None:
        for value in (0, -1, True, 1.5):
            with self.subTest(operation="retention", value=value):
                with self.assertRaisesRegex(ValueError, "retention_days"):
                    self.ledger.prune_expired_sessions(value, now=NOW)
        for value in (-1, True, 1.5):
            with self.subTest(operation="recovery", value=value):
                with self.assertRaisesRegex(ValueError, "stale_after_seconds"):
                    self.ledger.recover_stale_reservations("trace", value, now=NOW)

    def test_distinct_spans_sharing_call_id_keep_independent_open_state(self) -> None:
        self.ledger.append(self.event_for_trace(
            trace_id="trace", session_id="turn-1", call_id="shared",
            event_type="call.started", observed_at="2026-07-01T00:00:00Z",
            span_id="span-open",
        ))
        self.ledger.append(self.event_for_trace(
            trace_id="trace", session_id="turn-1", call_id="shared",
            event_type="call.started", observed_at="2026-07-01T00:01:00Z",
            span_id="span-closed",
        ))
        self.ledger.append(self.event_for_trace(
            trace_id="trace", session_id="turn-1", call_id="shared",
            event_type="call.completed", observed_at="2026-07-01T00:02:00Z",
            span_id="span-closed",
        ))

        self.assertEqual(self.ledger.sessions()[0].final_status, "active")
        self.assertEqual(self.ledger.prune_expired_sessions(7, now=NOW), [])
        self.assertEqual(
            self.ledger.recover_stale_reservations("trace", 3600, now=NOW),
            1,
        )
        cancellations = [
            event
            for event in self.ledger.inspect_session("trace")
            if event.event_type == "call.cancelled"
        ]
        self.assertEqual([event.span_id for event in cancellations], ["span-open"])

    def test_stale_recovery_appends_cancellation_without_mutating_start(self) -> None:
        self.ledger.append(make_event(
            session_id="trace", call_id="call-1", event_type="call.started",
            observed_at="2026-07-13T00:00:00Z"
        ))
        count = self.ledger.recover_stale_reservations(
            "trace", 3600, now=NOW
        )
        events = self.ledger.inspect_session("trace")
        self.assertEqual(count, 1)
        self.assertEqual([event.event_type for event in events], ["call.started", "call.cancelled"])
        self.assertEqual(events[-1].reason_code, "stale_reservation_recovered")
        self.assertEqual(events[-1].progress, "unknown")
        self.assertEqual(events[-1].trace_id, events[0].trace_id)
        self.assertEqual(events[-1].span_id, events[0].span_id)
        self.assertEqual(self.ledger.recover_stale_reservations("trace", 3600, now=NOW), 0)

    def test_zero_threshold_disables_recovery(self) -> None:
        self.assertEqual(self.ledger.recover_stale_reservations("trace", 0, now=NOW), 0)

    def test_secure_delete_removes_only_requested_session(self) -> None:
        self.ledger.append(make_event(session_id="remove", call_id="one"))
        self.ledger.append(make_event(session_id="keep", call_id="two"))
        self.assertEqual(self.ledger.delete_session("remove"), 1)
        self.assertEqual(self.ledger.inspect_session("remove"), [])
        self.assertEqual(len(self.ledger.inspect_session("keep")), 1)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not Windows ACLs")
    def test_data_directory_and_database_are_owner_only(self) -> None:
        self.assertEqual(self.db.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
