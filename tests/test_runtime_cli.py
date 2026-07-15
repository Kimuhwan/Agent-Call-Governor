from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_call_governor_runtime import (
    CallLedger,
    CallProposal,
    GovernedRuntime,
    GovernanceBlocked,
)


ROOT = Path(__file__).resolve().parents[1]


def proposal(session_id: str = "report-session") -> CallProposal:
    return CallProposal(
        session_id=session_id,
        objective="Inspect one runtime failure",
        route="agent:runtime-reviewer",
        capability_gap="The failure spans multiple components",
        expected_new_information="A source-backed failure explanation",
        stop_condition="The failing path is identified",
        material_inputs={"scope": "runtime"},
        quality_risk="medium",
    )


class RuntimeCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.db_path = self.root / "events.sqlite3"
        self.ledger = CallLedger(self.db_path)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _seed(self) -> None:
        call = proposal()
        observe = GovernedRuntime(self.ledger, mode="observe")
        enforce = GovernedRuntime(self.ledger, mode="enforce")
        observe.run(call, lambda: "first", progress="material_progress")
        observe.run(call, lambda: "observed duplicate", progress="material_progress")
        with self.assertRaises(GovernanceBlocked):
            enforce.run(call, lambda: self.fail("blocked call must not run"))

    def _run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "agent_call_governor_runtime", *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_report_summarizes_policy_and_execution_counts(self) -> None:
        self._seed()

        result = self._run_cli("report", "--db", str(self.db_path), "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["calls"], 3)
        self.assertEqual(report["policy_allowed"], 1)
        self.assertEqual(report["policy_blocked"], 2)
        self.assertEqual(report["executed"], 2)
        self.assertEqual(report["execution_blocked"], 1)
        self.assertEqual(report["would_block_but_executed"], 1)
        self.assertEqual(report["final_states"]["completed"], 2)
        self.assertEqual(report["final_states"]["blocked"], 1)

    def test_text_report_is_human_readable(self) -> None:
        self._seed()

        result = self._run_cli("report", "--db", str(self.db_path))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Agent Call Governor runtime report", result.stdout)
        self.assertIn("Would block but executed: 1", result.stdout)

    def test_report_does_not_count_cancelled_reservation_as_executed(self) -> None:
        runtime = GovernedRuntime(self.ledger, mode="enforce")
        handle = runtime.begin(proposal())
        runtime.cancel(handle, metadata={"cancelled_before_execution": True})

        result = self._run_cli("report", "--db", str(self.db_path), "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["executed"], 0)
        self.assertEqual(report["would_block_but_executed"], 0)
        self.assertEqual(report["final_states"]["cancelled"], 1)

    def test_export_jsonl_writes_sanitized_events(self) -> None:
        self._seed()
        output_path = self.root / "export" / "events.jsonl"

        result = self._run_cli(
            "export-jsonl",
            "--db",
            str(self.db_path),
            "--output",
            str(output_path),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = output_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), len(self.ledger.events()))
        self.assertTrue(all(isinstance(json.loads(line), dict) for line in lines))
        self.assertNotIn("observed duplicate", output_path.read_text(encoding="utf-8"))

    def test_export_refuses_to_overwrite_database(self) -> None:
        result = self._run_cli(
            "export-jsonl",
            "--db",
            str(self.db_path),
            "--output",
            str(self.db_path),
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("must differ", result.stderr)

    def test_codex_hook_subcommand_delegates_stdin_event(self) -> None:
        hook_db = self.root / "hook.sqlite3"
        payload = (ROOT / "tests" / "fixtures" / "codex-hooks" / "pre_tool_use.json").read_text(
            encoding="utf-8"
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_call_governor_runtime",
                "codex-hook",
                "--db",
                str(hook_db),
            ],
            cwd=ROOT,
            input=payload,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            [
                event.event_type
                for event in CallLedger(hook_db).events(
                    "codex:trace:"
                    f"{hashlib.sha256(b'session-1').hexdigest()}:turn:"
                    f"{hashlib.sha256(b'turn-1').hexdigest()}"
                )
            ],
            ["call.proposed", "policy.decided", "call.started"],
        )

    def test_runtime_eval_has_at_least_sixty_variants(self) -> None:
        cases = json.loads((ROOT / "evals" / "runtime_workloads.json").read_text(encoding="utf-8"))
        self.assertIsInstance(cases, list)
        self.assertGreaterEqual(len(cases), 60)
        self.assertEqual(len({case["name"] for case in cases}), len(cases))

    def test_runtime_eval_preserves_needed_calls_and_blocks_waste(self) -> None:
        from evals.run_runtime_evals import run

        result = run()

        self.assertEqual(result["failures"], [])
        self.assertEqual(result["under_call_failures"], 0)
        self.assertEqual(result["unexpected_redundant_executions"], 0)
        self.assertEqual(result["necessary_call_preservation_rate"], 1.0)
        self.assertEqual(result["redundant_call_block_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
