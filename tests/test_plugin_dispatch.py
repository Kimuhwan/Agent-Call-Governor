from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from agent_call_governor_runtime import CallLedger


ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = ROOT / "hooks" / "dispatch.py"
FIXTURES = ROOT / "tests" / "fixtures" / "codex-hooks"
CONFIG_KEYS = {
    "AGENT_CALL_GOVERNOR_MODE",
    "AGENT_CALL_GOVERNOR_PROFILE",
    "AGENT_CALL_GOVERNOR_RISK",
    "AGENT_CALL_GOVERNOR_RETENTION_DAYS",
    "AGENT_CALL_GOVERNOR_STALE_SECONDS",
    "AGENT_CALL_GOVERNOR_DB",
}


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class PluginDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.data = self.root / "data"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def dispatch(
        self,
        payload: object,
        *,
        updates: dict[str, str] | None = None,
        raw: str | None = None,
        plugin_root: Path = ROOT,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {key: value for key, value in os.environ.items() if key not in CONFIG_KEYS}
        if plugin_root != ROOT:
            env.pop("PYTHONPATH", None)
        env.update({"PLUGIN_ROOT": str(plugin_root), "PLUGIN_DATA": str(self.data)})
        env.update(updates or {})
        return subprocess.run(
            [sys.executable, str(DISPATCHER)],
            cwd=cwd or ROOT,
            env=env,
            input=raw if raw is not None else json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_valid_input_writes_sqlite_only(self) -> None:
        result = self.dispatch(fixture("session_start.json"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        db = self.data / "events.sqlite3"
        self.assertTrue(db.is_file())
        self.assertEqual(
            [event.event_type for event in CallLedger(db).events()],
            ["session.started"],
        )
        self.assertEqual(list(self.data.glob("*.jsonl")), [])

    def test_warn_output_is_the_only_stdout_shape(self) -> None:
        first = fixture("pre_tool_use.json")
        second = fixture("pre_tool_use.json")
        second["tool_use_id"] = "RAW_SECOND_TOOL_ID"
        updates = {"AGENT_CALL_GOVERNOR_MODE": "warn"}
        self.assertEqual(self.dispatch(first, updates=updates).stdout, "")

        result = self.dispatch(second, updates=updates)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(set(json.loads(result.stdout)), {"systemMessage"})
        self.assertNotIn("RAW_SECOND_TOOL_ID", result.stdout)

    def test_malformed_json_fails_open_with_type_only(self) -> None:
        canary = "RAW_STDIN_CANARY"
        result = self.dispatch({}, raw=f'{{"secret":"{canary}"')

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "agent-call-governor hook unavailable: JSONDecodeError\n",
        )
        self.assertNotIn(canary, result.stderr)
        self.assertNotIn("column", result.stderr)

    def test_invalid_configuration_and_ranges_fail_open_without_values(self) -> None:
        cases = [
            ("AGENT_CALL_GOVERNOR_MODE", "RAW_MODE"),
            ("AGENT_CALL_GOVERNOR_PROFILE", "RAW_PROFILE"),
            ("AGENT_CALL_GOVERNOR_RISK", "RAW_RISK"),
            ("AGENT_CALL_GOVERNOR_RETENTION_DAYS", "0"),
            ("AGENT_CALL_GOVERNOR_RETENTION_DAYS", "3651"),
            ("AGENT_CALL_GOVERNOR_STALE_SECONDS", "-1"),
            ("AGENT_CALL_GOVERNOR_STALE_SECONDS", "31536001"),
            ("AGENT_CALL_GOVERNOR_STALE_SECONDS", "RAW_INTEGER"),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                result = self.dispatch(fixture("session_start.json"), updates={name: value})
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(
                    result.stderr,
                    "agent-call-governor hook unavailable: ValueError\n",
                )
                self.assertNotIn(value, result.stderr)

    def test_runtime_import_and_database_open_failures_are_sanitized(self) -> None:
        missing_root = self.root / "RAW_PRIVATE_MISSING_ROOT"
        missing_package = (
            missing_root
            / "skills"
            / "agent-call-governor"
            / "scripts"
            / "agent_call_governor_runtime"
        )
        missing_package.mkdir(parents=True)
        (missing_package / "__init__.py").write_text(
            "import sys\n"
            "print('RAW_PRIVATE_IMPORT_STDOUT')\n"
            "print('RAW_PRIVATE_IMPORT_STDERR', file=sys.stderr)\n"
            "raise ImportError('RAW_PRIVATE_IMPORT_MESSAGE')\n",
            encoding="utf-8",
        )
        import_failure = self.dispatch(
            fixture("session_start.json"),
            plugin_root=missing_root,
            cwd=self.root,
        )
        self.assertEqual(import_failure.returncode, 0)
        self.assertEqual(import_failure.stdout, "")
        self.assertEqual(
            import_failure.stderr,
            "agent-call-governor hook unavailable: ImportError\n",
        )
        self.assertNotIn(str(missing_root), import_failure.stderr)
        self.assertNotIn("RAW_PRIVATE_IMPORT_MESSAGE", import_failure.stderr)
        self.assertNotIn("RAW_PRIVATE_IMPORT_STDERR", import_failure.stderr)

        invalid_db = self.root / "RAW_PRIVATE_DB_DIRECTORY"
        invalid_db.mkdir()
        open_failure = self.dispatch(
            fixture("session_start.json"),
            updates={"AGENT_CALL_GOVERNOR_DB": str(invalid_db)},
        )
        self.assertEqual(open_failure.returncode, 0)
        self.assertEqual(open_failure.stdout, "")
        self.assertEqual(
            open_failure.stderr,
            "agent-call-governor hook unavailable: OperationalError\n",
        )
        self.assertNotIn(str(invalid_db), open_failure.stderr)

    def test_profile_and_risk_environment_defaults_reach_proposal(self) -> None:
        payload = fixture("pre_tool_use.json")
        payload["governor_profile"] = "strict"
        payload["governor_quality_risk"] = "low"
        result = self.dispatch(
            payload,
            updates={
                "AGENT_CALL_GOVERNOR_PROFILE": "quality-first",
                "AGENT_CALL_GOVERNOR_RISK": "high",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        events = CallLedger(self.data / "events.sqlite3").events()
        self.assertEqual({event.profile for event in events}, {"quality-first"})
        self.assertEqual({event.quality_risk for event in events}, {"high"})

    def test_live_dispatch_constructs_one_second_sqlite_only_ledger(self) -> None:
        fake_root = self.root / "fake-plugin"
        package = fake_root / "skills" / "agent-call-governor" / "scripts" / "agent_call_governor_runtime"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(
            textwrap.dedent(
                """
                import json
                import os
                from pathlib import Path

                class CallLedger:
                    def __init__(self, path, jsonl_path=None, *, busy_timeout_ms=30000):
                        Path(os.environ["PLUGIN_DATA"]).mkdir(parents=True, exist_ok=True)
                        (Path(os.environ["PLUGIN_DATA"]) / "constructor.json").write_text(
                            json.dumps({"path": str(path), "jsonl": jsonl_path, "busy": busy_timeout_ms})
                        )

                class GovernedRuntime:
                    def __init__(self, ledger, **kwargs):
                        self.ledger = ledger
                """
            ),
            encoding="utf-8",
        )
        (package / "codex_hook.py").write_text(
            "def handle_codex_hook(payload, runtime, **kwargs):\n    return None\n",
            encoding="utf-8",
        )

        result = self.dispatch(fixture("session_start.json"), plugin_root=fake_root, cwd=self.root)

        self.assertEqual(result.returncode, 0, result.stderr)
        constructor = json.loads((self.data / "constructor.json").read_text(encoding="utf-8"))
        self.assertEqual(constructor["busy"], 1000)
        self.assertIsNone(constructor["jsonl"])

    def test_payload_and_environment_canaries_never_reach_output_or_storage(self) -> None:
        payload = fixture("subagent_start.json")
        payload.update({
            "session_id": "RAW_SESSION_CANARY",
            "turn_id": "RAW_TURN_CANARY",
            "agent_id": "RAW_AGENT_CANARY",
            "parent_agent_id": "RAW_PARENT_CANARY",
            "task": "RAW_TASK_CANARY",
            "prompt": "RAW_PROMPT_CANARY",
            "output": "RAW_OUTPUT_CANARY",
            "error": "RAW_ERROR_CANARY",
        })
        result = self.dispatch(payload)
        self.assertEqual(result.returncode, 0, result.stderr)

        storage = (self.data / "events.sqlite3").read_bytes().decode("utf-8", errors="ignore")
        combined = result.stdout + result.stderr + storage
        for canary in (
            "RAW_SESSION_CANARY",
            "RAW_TURN_CANARY",
            "RAW_AGENT_CANARY",
            "RAW_PARENT_CANARY",
            "RAW_TASK_CANARY",
            "RAW_PROMPT_CANARY",
            "RAW_OUTPUT_CANARY",
            "RAW_ERROR_CANARY",
        ):
            self.assertNotIn(canary, combined)


if __name__ == "__main__":
    unittest.main()
