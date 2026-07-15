from __future__ import annotations

import json
import os
import re
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
COMMAND_FENCE = re.compile(
    r"```(?:bash|sh|powershell|console)\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


def command_blocks(text: str) -> list[str]:
    return [
        "\n".join(line.rstrip() for line in block.strip().splitlines())
        for block in COMMAND_FENCE.findall(text.replace("\r\n", "\n"))
    ]


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

    def test_skill_metadata_is_portable_and_generator_shaped(self) -> None:
        skill_root = ROOT / "skills" / "agent-call-governor"
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\n"))
        frontmatter = skill.split("---\n", 2)[1]
        keys = [line.split(":", 1)[0] for line in frontmatter.splitlines() if line]
        self.assertEqual(keys, ["name", "description"])
        self.assertLess(len(skill.splitlines()), 500)

        metadata = (skill_root / "agents" / "openai.yaml").read_text(encoding="utf-8")
        lines = metadata.splitlines()
        self.assertEqual(lines[0], "interface:")
        self.assertTrue(
            all(re.fullmatch(r'  [a-z_]+: ".*"', line) for line in lines[1:]),
            metadata,
        )
        self.assertIn("$agent-call-governor", metadata)

    def test_public_docs_cover_install_security_and_honest_limits(self) -> None:
        english = (ROOT / "README.md").read_text(encoding="utf-8")
        korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
        required_commands = (
            "codex plugin marketplace add Kimuhwan/Agent-Call-Governor --ref main",
            "python -m pip install https://github.com/Kimuhwan/Agent-Call-Governor/releases/download/v0.3.0/agent_call_governor_runtime-0.3.0-py3-none-any.whl",
            "agent-call-governor-runtime doctor --plugin-root PATH_TO_CHECKOUT --db PATH_TO_EVENTS",
            "agent-call-governor-runtime sessions --db PATH_TO_EVENTS",
            "agent-call-governor-runtime inspect SESSION_ID --db PATH_TO_EVENTS",
            "agent-call-governor-runtime report --db PATH_TO_EVENTS",
            "agent-call-governor-runtime export --format jsonl --output PATH_TO_EXPORT --db PATH_TO_EVENTS",
            "agent-call-governor-runtime delete-session SESSION_ID --yes --db PATH_TO_EVENTS",
        )
        official_links = (
            "https://learn.chatgpt.com/docs/hooks.md",
            "https://learn.chatgpt.com/docs/build-plugins.md",
        )

        for text in (english, korean):
            self.assertIn("v0.3.0", text)
            for event in EVENTS:
                self.assertIn(event, text)
            for command in required_commands:
                self.assertIn(command, text)
            for link in official_links:
                self.assertIn(link, text)
            self.assertIn("PLUGIN_DATA", text)
            self.assertIn("AGENT_CALL_GOVERNOR_DB", text)
            self.assertIn("/hooks", text)
            self.assertIn("exact hash", text.lower())
            self.assertGreater(
                text.index("/hooks"),
                text.index("codex plugin marketplace add"),
                "hook trust must be a separate step after plugin installation",
            )
            self.assertIn('hookSpecificOutput.permissionDecision: "deny"', text)
            self.assertIn("observe/warn-only", text.lower())
            for policy_axis in ("strict", "balanced", "quality-first", "low", "medium", "high"):
                self.assertIn(policy_axis, text)
            lowered = text.lower()
            for unsupported_claim in (
                "complete agent-call firewall",
                "codex hooks cannot block",
                "live jsonl mirroring",
                "`quality-first` mode",
                "mode `quality-first`",
            ):
                self.assertNotIn(unsupported_claim, lowered)

        self.assertIn("## Modes, profiles, and risk", english)
        self.assertIn("## 모드, 프로필, 위험도", korean)
        self.assertIn("runner and dated results are checked in", english)
        self.assertIn("실행기와 날짜가 있는 결과가 체크인된 뒤", korean)
        self.assertEqual(command_blocks(english), command_blocks(korean))

        for path in (
            "docs/architecture.md",
            "docs/limitations.md",
            "docs/security.md",
            "docs/benchmark-methodology.md",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
        ):
            self.assertTrue((ROOT / path).is_file(), path)

        architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        self.assertIn(
            "Codex event -> dispatcher -> normalizer -> policy -> SQLite -> CLI/export",
            architecture,
        )
        self.assertIn("PLUGIN_DATA/events.sqlite3", architecture)
        self.assertIn("exact hash", architecture.lower())
        self.assertIn("five-second delivery window", architecture)

        limitations = (ROOT / "docs" / "limitations.md").read_text(encoding="utf-8")
        for statement in (
            "observe/warn-only",
            "near-duplicates",
            "Policy replay",
            "Usage, tokens, and cost are nullable",
            "session.stopped",
            "legacy-v1",
            "fingerprint-v2",
        ):
            self.assertIn(statement, limitations)
        self.assertIn("partitions matching history by budget kind separately", limitations)

        security = (ROOT / "docs" / "security.md").read_text(encoding="utf-8")
        self.assertIn("exact hash", security.lower())
        self.assertIn("no live JSONL mirror", security)
        self.assertIn("seven days", security)
        self.assertIn("Windows", security)

        benchmark = (ROOT / "docs" / "benchmark-methodology.md").read_text(
            encoding="utf-8"
        )
        priorities = (
            benchmark.index("**Task success**"),
            benchmark.index("**Under-call rate**"),
            benchmark.index("**False-block rate**"),
            benchmark.index("**Call efficiency**"),
        )
        self.assertEqual(priorities, tuple(sorted(priorities)))
        self.assertIn("does not measure model response quality", benchmark)

        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(changelog, r"(?m)^## \[0\.3\.0\] - Unreleased$")
        self.assertRegex(changelog, r"(?m)^## \[0\.2\.0\]")
        self.assertRegex(changelog, r"(?m)^## \[0\.1\.0\]")

        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("Test-driven workflow", contributing)
        self.assertIn("Privacy testing", contributing)

        root_security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("does not currently have GitHub private vulnerability reporting enabled", root_security)
        self.assertIn("include no vulnerability details", root_security)
        self.assertIn("no fixed response or remediation SLA", root_security)

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
