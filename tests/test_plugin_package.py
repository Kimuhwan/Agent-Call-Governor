from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_call_governor_runtime import __version__


ROOT = Path(__file__).resolve().parents[1]
EVENTS = {
    "SessionStart",
    "PreToolUse",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
}


class PluginPackageTests(unittest.TestCase):
    def test_manifest_and_python_versions_match(self) -> None:
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "agent-call-governor")
        self.assertEqual(manifest["version"], __version__)
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertNotIn("hooks", manifest)

    def test_marketplace_points_to_repository_root_plugin(self) -> None:
        marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        plugin = marketplace["plugins"][0]
        self.assertEqual(plugin["name"], "agent-call-governor")
        self.assertEqual(plugin["source"]["source"], "url")
        self.assertEqual(plugin["source"]["ref"], "main")
        self.assertEqual(plugin["policy"], {"installation": "AVAILABLE", "authentication": "ON_INSTALL"})

    def test_all_six_hooks_use_plugin_root_and_windows_override(self) -> None:
        config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config["hooks"]), EVENTS)
        for groups in config["hooks"].values():
            handler = groups[0]["hooks"][0]
            self.assertEqual(handler["type"], "command")
            self.assertIn("$PLUGIN_ROOT", handler["command"])
            self.assertIn("%PLUGIN_ROOT%", handler["commandWindows"])
            self.assertEqual(handler["timeout"], 10)

    def test_moved_skill_and_dispatcher_exist(self) -> None:
        self.assertTrue((ROOT / "skills" / "agent-call-governor" / "SKILL.md").is_file())
        self.assertTrue((ROOT / "hooks" / "dispatch.py").is_file())

    def test_dispatcher_fails_open_without_leaking_malformed_input(self) -> None:
        canary = "RAW_SECRET_CANARY_42"
        result = subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "dispatch.py")],
            cwd=ROOT,
            env={**os.environ, "PLUGIN_ROOT": str(ROOT)},
            input=f'{{"hook_event_name":"PreToolUse","canary":"{canary}"',
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "agent-call-governor hook unavailable: JSONDecodeError\n")
        self.assertNotIn(canary, result.stderr)

    def test_dispatcher_preserves_successful_hook_stdout(self) -> None:
        dispatcher = ROOT / "hooks" / "dispatch.py"
        payload = json.loads(
            (ROOT / "tests" / "fixtures" / "codex-hooks" / "pre_tool_use.json").read_text(
                encoding="utf-8"
            )
        )
        replay = {**payload, "tool_use_id": "tool-call-2"}
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, str(dispatcher)]
            env = {
                **os.environ,
                "PLUGIN_ROOT": str(ROOT),
                "PLUGIN_DATA": directory,
                "AGENT_CALL_GOVERNOR_MODE": "warn",
            }
            first = subprocess.run(
                command, cwd=ROOT, env=env, input=json.dumps(payload),
                text=True, capture_output=True, check=False,
            )
            second = subprocess.run(
                command, cwd=ROOT, env=env, input=json.dumps(replay),
                text=True, capture_output=True, check=False,
            )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "")
        self.assertEqual(first.stderr, "")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("systemMessage", json.loads(second.stdout))
        self.assertEqual(second.stderr, "")


if __name__ == "__main__":
    unittest.main()
