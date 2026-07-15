# Agent Call Governor v0.3 Local Observability Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Agent Call Governor v0.3.0 as a real, local-first Codex plugin with privacy-safe lifecycle telemetry, deterministic fingerprint v2, safe SQLite migration, operational inspection, and honest public release evidence.

**Architecture:** The repository root becomes one Codex plugin so the plugin, skill, hook dispatcher, and Python runtime share a single source tree. Codex hooks normalize six host events into a versioned append-only SQLite ledger; focused fingerprint and redaction modules keep policy identity deterministic and persisted data non-plaintext. The existing Python runtime and Agents SDK adapter remain supported compatibility surfaces, while Codex hooks stay observe/warn and process-level fail-open.

**Tech Stack:** Python 3.10+, standard-library `sqlite3`, `dataclasses`, `hashlib`, `json`, `argparse`, `unittest`, setuptools dynamic metadata, Codex plugin manifests/hooks, JSON Schema, GitHub Actions, PowerShell and POSIX shell installers.

## Global Constraints

- Target product version is exactly `0.3.0`; preserve the published `v0.2.0` tag and artifacts.
- Canonical constants are `SCHEMA_VERSION = 2`, `FINGERPRINT_VERSION = 2`, `POLICY_FACTS_VERSION = 1`, and `POLICY_VERSION = "2026-07-14.1"`; none is derived from the package version.
- The Python package `agent_call_governor_runtime`, `agent-call-governor evaluate`, and existing wrapper/Agents SDK public APIs remain compatible.
- Installing the Codex plugin installs its skill and hooks but does not create Python console entry points. Document and test the separately installable v0.3.0 companion wheel for users who want `agent-call-governor-runtime` in their shell.
- The Codex plugin supports only `mode = observe | warn` and always uses `failure_policy = fail-open`; application-owned wrappers retain `enforce` and opt-in `fail-closed`.
- Plugin defaults are `observe`, `balanced`, `medium`, seven retention days, a 24-hour stale-reservation threshold, and a 1,000 ms SQLite busy timeout.
- SQLite is authoritative. The plugin never writes a live JSONL mirror; JSONL is a sanitized export format. The legacy `jsonl_path` argument and `export-jsonl` command warn for one release.
- Never persist raw prompts, objectives, tool inputs, tool outputs, exception messages, Codex host identifiers, authorization headers, API keys, email addresses, or home-directory paths.
- The host Codex `PreToolUse` contract supports denial for supported calls through `hookSpecificOutput.permissionDecision: "deny"`. Agent Call Governor v0.3 deliberately does not emit that shape: its bundled hooks return at most a documented `systemMessage`, remain observe/warn-only, and exit `0` without hook output after internal failure. Product copy must distinguish host capability from this product boundary and must not claim firewall behavior.
- Fingerprint canonicalization sorts JSON object keys only and preserves list order, string case, whitespace, and every material tool argument. Host adapters remove explicitly enumerated delivery identifiers from the host envelope before constructing material inputs; fingerprint code never strips matching key names recursively from tool arguments.
- `unknown` is the default progress for successful Codex tool and subagent completion. No LLM call is used to classify progress.
- Do not add policy replay, compare/explain commands, near-duplicate enforcement, cloud telemetry, a daemon, a dashboard, MCP proxying, or new framework integrations in v0.3.0.
- Every behavioral change begins with a focused failing `unittest`, then the minimal implementation, focused pass, relevant regression pass, and one commit.
- Run commands from the repository root. On Windows use the bundled Git executable when `git` is not on `PATH`: `C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe`.
- Before implementation, create an isolated worktree with `superpowers:using-git-worktrees`; do not implement directly on `main`.

## File Responsibility Map

- `.codex-plugin/plugin.json`: Codex plugin identity, version, interface copy, and skill discovery.
- `.agents/plugins/marketplace.json`: repository-root marketplace entry.
- `hooks/hooks.json`: six bundled Codex lifecycle commands using `PLUGIN_ROOT`.
- `hooks/dispatch.py`: stdin boundary, environment resolution, and process-level fail-open.
- `skills/agent-call-governor/`: moved skill, references, UI metadata, compatibility scripts, and Python package source.
- `skills/agent-call-governor/scripts/agent_call_governor_runtime/fingerprint.py`: fingerprint v2 canonicalization and digests.
- `skills/agent-call-governor/scripts/agent_call_governor_runtime/redaction.py`: source allowlists and recursive persistence/export sanitation.
- `skills/agent-call-governor/scripts/agent_call_governor_runtime/models.py`: validated proposal, decision, event, and summary records.
- `skills/agent-call-governor/scripts/agent_call_governor_runtime/ledger.py`: schema migration, atomic append/reservation, queries, retention, recovery, and deletion.
- `skills/agent-call-governor/scripts/agent_call_governor_runtime/runtime.py`: policy evaluation and canonical lifecycle emission.
- `skills/agent-call-governor/scripts/agent_call_governor_runtime/codex_hook.py`: six-event Codex normalization and warning output.
- `skills/agent-call-governor/scripts/agent_call_governor_runtime/cli.py`: doctor, sessions, inspect, delete, report, export, and compatibility commands.
- `schemas/event-v2.schema.json`: exported event contract.
- `schemas/policy-facts-v1.schema.json`: replay-ready privacy-safe fact contract.
- `tests/`: focused unit, migration, privacy, hook, CLI, multiprocess, packaging, and smoke tests.
- `evals/`: deterministic policy regressions plus the ten-case v0.3 instrumentation pilot.
- `docs/`, `README.md`, `README.ko.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`: public architecture, limitations, security, benchmark method, onboarding, and release record.

---

### Task 1: Convert the Repository Root into a Version-Locked Codex Plugin

**Files:**
- Move: `agent-call-governor/` -> `skills/agent-call-governor/`
- Create: `.codex-plugin/plugin.json`
- Create: `.agents/plugins/marketplace.json`
- Create: `hooks/hooks.json`
- Create: `hooks/dispatch.py`
- Create: `tests/test_plugin_package.py`
- Create: `tests/test_installers.py`
- Modify: `pyproject.toml`
- Modify: `install.ps1`
- Modify: `install.sh`
- Modify: `.github/workflows/validate.yml`
- Modify: path references under `tests/`, `evals/`, `examples/`, `README.md`, and `README.ko.md`

**Interfaces:**
- Consumes: existing `agent_call_governor_runtime.__version__`, skill files, installers, and test suite.
- Produces: root plugin contract, `skills/agent-call-governor/` source location, manifest version equality, and a safe dispatcher import boundary used by Task 7.

- [ ] **Step 1: Write the failing plugin-package tests**

Create `tests/test_plugin_package.py` with JSON-shape, version, path, and hook-command checks:

```python
from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
```

Create `tests/test_installers.py` so both compatibility installers are exercised in their native CI OS:

```python
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def assert_installed(self, codex_home: Path) -> None:
        destination = codex_home / "skills" / "agent-call-governor"
        self.assertTrue((destination / "SKILL.md").is_file())
        self.assertTrue((destination / "scripts" / "agent_call_governor_runtime" / "__init__.py").is_file())
        self.assertFalse((codex_home / "skills" / "skills" / "agent-call-governor").exists())

    @unittest.skipUnless(os.name == "nt", "PowerShell installer runs on Windows")
    def test_powershell_installer_copies_moved_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "CODEX_HOME": directory}
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "install.ps1")],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_installed(Path(directory))

    @unittest.skipIf(os.name == "nt", "POSIX installer runs on Linux")
    def test_posix_installer_copies_moved_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "CODEX_HOME": directory}
            result = subprocess.run(
                ["sh", str(ROOT / "install.sh")], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_installed(Path(directory))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the package test and verify the intended failure**

Run: `python -m unittest tests.test_plugin_package tests.test_installers -v`

Expected: FAIL with `FileNotFoundError` for `.codex-plugin/plugin.json` or `skills/agent-call-governor/SKILL.md`, and the native installer test fails until its source path is moved.

- [ ] **Step 3: Move the skill, add exact manifests, and make `__version__` canonical**

Run the move through Git:

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" mv agent-call-governor skills/agent-call-governor
```

Set `skills/agent-call-governor/scripts/agent_call_governor_runtime/__init__.py` to `__version__ = "0.3.0"`. Replace static version metadata in `pyproject.toml` with:

```toml
[project]
name = "agent-call-governor-runtime"
dynamic = ["version"]
description = "Local-first, quality-preserving call governance and observability for Codex"
readme = "README.md"
requires-python = ">=3.10"
license = "MIT"
license-files = ["LICENSE"]
authors = [{ name = "Kimuhwan" }]
dependencies = []

[project.optional-dependencies]
agents = ["openai-agents>=0.18.2,<0.19"]
dev = ["build>=1.2", "PyYAML>=6", "jsonschema>=4.23"]

[project.urls]
Repository = "https://github.com/Kimuhwan/Agent-Call-Governor"
Issues = "https://github.com/Kimuhwan/Agent-Call-Governor/issues"

[project.scripts]
agent-call-governor = "agent_call_governor_runtime.policy:main"
agent-call-governor-runtime = "agent_call_governor_runtime.cli:main"

[tool.setuptools.dynamic]
version = { attr = "agent_call_governor_runtime.__version__" }

[tool.setuptools.packages.find]
where = ["skills/agent-call-governor/scripts"]
include = ["agent_call_governor_runtime*"]
```

Create `.codex-plugin/plugin.json`:

```json
{
  "name": "agent-call-governor",
  "version": "0.3.0",
  "description": "Local-first, quality-preserving call governance and observability for Codex.",
  "author": {
    "name": "Kimuhwan",
    "url": "https://github.com/Kimuhwan"
  },
  "homepage": "https://github.com/Kimuhwan/Agent-Call-Governor",
  "repository": "https://github.com/Kimuhwan/Agent-Call-Governor",
  "license": "MIT",
  "keywords": ["codex", "governance", "observability", "hooks"],
  "skills": "./skills/",
  "interface": {
    "displayName": "Agent Call Governor",
    "shortDescription": "Observe and govern Codex calls locally",
    "longDescription": "Local-first Codex call observability with deterministic, quality-preserving policy decisions.",
    "developerName": "Kimuhwan",
    "category": "Productivity",
    "capabilities": ["Read"],
    "websiteURL": "https://github.com/Kimuhwan/Agent-Call-Governor",
    "defaultPrompt": [
      "Use Agent Call Governor to inspect this task's call plan.",
      "Show my local Agent Call Governor session summary."
    ]
  }
}
```

Create `.agents/plugins/marketplace.json`:

```json
{
  "name": "agent-call-governor",
  "interface": {"displayName": "Agent Call Governor"},
  "plugins": [
    {
      "name": "agent-call-governor",
      "source": {
        "source": "url",
        "url": "https://github.com/Kimuhwan/Agent-Call-Governor.git",
        "ref": "main"
      },
      "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
      "category": "Productivity"
    }
  ]
}
```

Create `hooks/hooks.json` with one handler per supported event:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 \"$PLUGIN_ROOT/hooks/dispatch.py\"", "commandWindows": "py -3 \"%PLUGIN_ROOT%\\hooks\\dispatch.py\"", "timeout": 10}]}],
    "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 \"$PLUGIN_ROOT/hooks/dispatch.py\"", "commandWindows": "py -3 \"%PLUGIN_ROOT%\\hooks\\dispatch.py\"", "timeout": 10}]}],
    "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 \"$PLUGIN_ROOT/hooks/dispatch.py\"", "commandWindows": "py -3 \"%PLUGIN_ROOT%\\hooks\\dispatch.py\"", "timeout": 10}]}],
    "SubagentStart": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 \"$PLUGIN_ROOT/hooks/dispatch.py\"", "commandWindows": "py -3 \"%PLUGIN_ROOT%\\hooks\\dispatch.py\"", "timeout": 10}]}],
    "SubagentStop": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 \"$PLUGIN_ROOT/hooks/dispatch.py\"", "commandWindows": "py -3 \"%PLUGIN_ROOT%\\hooks\\dispatch.py\"", "timeout": 10}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "python3 \"$PLUGIN_ROOT/hooks/dispatch.py\"", "commandWindows": "py -3 \"%PLUGIN_ROOT%\\hooks\\dispatch.py\"", "timeout": 10}]}]
  }
}
```

Create a functional no-output fail-open `hooks/dispatch.py`; Task 7 will connect its event path:

```python
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    try:
        plugin_root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1])).resolve()
        package_root = plugin_root / "skills" / "agent-call-governor" / "scripts"
        sys.path.insert(0, str(package_root))
        from agent_call_governor_runtime.codex_hook import main as hook_main

        return int(hook_main())
    except BaseException as exc:
        print(f"agent-call-governor hook unavailable: {type(exc).__name__}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Update every old repository path to `skills/agent-call-governor`, change both installers' `SOURCE` path, and change CI skill validation to `skills/agent-call-governor/SKILL.md`.

- [ ] **Step 4: Install editable package and run packaging plus existing regressions**

Run:

```powershell
python -m pip install -e ".[dev]"
python -m unittest tests.test_plugin_package tests.test_installers -v
python -m unittest discover -s tests -v
python evals/run_evals.py
python evals/run_runtime_evals.py
```

Expected: plugin tests PASS; all existing unit tests PASS; both eval scripts exit `0` with zero failures and preserve the 19/19 deterministic policy baseline.

- [ ] **Step 5: Commit the root plugin conversion**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add .codex-plugin .agents hooks skills pyproject.toml install.ps1 install.sh .github tests evals examples README.md README.ko.md
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Build root Codex plugin package"
```

---

### Task 2: Add Deterministic Tool-Aware Fingerprint v2

**Files:**
- Create: `skills/agent-call-governor/scripts/agent_call_governor_runtime/fingerprint.py`
- Create: `tests/test_fingerprint.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/policy.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/models.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/__init__.py`

**Interfaces:**
- Consumes: `CallProposal.objective`, `.route`, `.material_inputs`, optional `metadata["cwd"]`, and optional `metadata["state_token"]`.
- Produces: `FINGERPRINT_VERSION`, immutable `FingerprintResult`, `build_fingerprint(*, objective, route, material_inputs, cwd, tool_version, state_token)`, and the backwards-compatible `policy.fingerprint(proposal) -> str` wrapper.

- [ ] **Step 1: Write the failing fingerprint contract tests**

Create `tests/test_fingerprint.py`:

```python
from __future__ import annotations

import unittest

from agent_call_governor_runtime.fingerprint import FINGERPRINT_VERSION, build_fingerprint


class FingerprintTests(unittest.TestCase):
    def build(self, route: str, inputs: dict, *, cwd: str | None = None, state: str | None = None):
        return build_fingerprint(
            objective="Verify a release candidate",
            route=route,
            material_inputs=inputs,
            cwd=cwd,
            tool_version="host-v1",
            state_token=state,
        )

    def test_object_key_order_is_equivalent(self) -> None:
        self.assertEqual(self.build("mcp:lookup", {"a": 1, "b": 2}).digest,
                         self.build("mcp:lookup", {"b": 2, "a": 1}).digest)

    def test_list_order_case_and_whitespace_are_material(self) -> None:
        self.assertNotEqual(self.build("mcp:lookup", {"q": ["A", "b"]}).digest,
                            self.build("mcp:lookup", {"q": ["b", "A"]}).digest)
        self.assertNotEqual(self.build("Bash", {"command": "git status"}).digest,
                            self.build("Bash", {"command": "Git status"}).digest)
        self.assertNotEqual(self.build("Bash", {"command": "git  status"}).digest,
                            self.build("Bash", {"command": "git status"}).digest)

    def test_bash_direction_and_cwd_are_material(self) -> None:
        self.assertNotEqual(self.build("Bash", {"command": "git diff main..HEAD"}, cwd="C:/a").digest,
                            self.build("Bash", {"command": "git diff HEAD..main"}, cwd="C:/a").digest)
        self.assertNotEqual(self.build("Bash", {"command": "git status"}, cwd="C:/a").digest,
                            self.build("Bash", {"command": "git status"}, cwd="C:/b").digest)

    def test_exact_patch_and_state_token_are_material(self) -> None:
        first = self.build("apply_patch", {"patch": "*** Add File: a\n+x"}, cwd="C:/repo")
        second = self.build("apply_patch", {"patch": "*** Add File: a\n+y"}, cwd="C:/repo")
        replay = self.build("apply_patch", {"patch": "*** Add File: a\n+x"}, cwd="C:/repo", state="after-read")
        self.assertNotEqual(first.digest, second.digest)
        self.assertNotEqual(first.digest, replay.digest)

    def test_material_call_id_argument_is_not_stripped(self) -> None:
        first = self.build("mcp:lookup", {"call_id": "one", "business_id": "A"})
        second = self.build("mcp:lookup", {"call_id": "two", "business_id": "A"})
        self.assertNotEqual(first.digest, second.digest)

    def test_result_contains_only_digests_and_versions(self) -> None:
        result = self.build("mcp:lookup", {"query": "private customer"})
        self.assertEqual(result.version, FINGERPRINT_VERSION)
        self.assertEqual(FINGERPRINT_VERSION, 2)
        self.assertTrue(result.digest.startswith("sha256:"))
        self.assertTrue(result.input_digest.startswith("sha256:"))
        self.assertTrue(result.objective_digest.startswith("sha256:"))
        self.assertNotIn("private customer", repr(result))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests and verify the missing-module failure**

Run: `python -m unittest tests.test_fingerprint -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_call_governor_runtime.fingerprint'`.

- [ ] **Step 3: Implement exact canonicalization and keep the policy wrapper stable**

Implement `fingerprint.py` with this public shape and canonical rules:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


FINGERPRINT_VERSION = 2


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _canonicalizer(route: str) -> str:
    if route == "Bash":
        return "bash-v1"
    if route in {"apply_patch", "Edit", "Write"}:
        return "workspace-write-v1"
    if route.startswith("mcp:") or route.startswith("mcp__"):
        return "mcp-json-v1"
    return "generic-json-v1"


@dataclass(frozen=True)
class FingerprintResult:
    digest: str
    input_digest: str
    objective_digest: str
    canonicalizer_version: str
    state_token_digest: str | None
    version: int = FINGERPRINT_VERSION


def build_fingerprint(
    *,
    objective: str,
    route: str,
    material_inputs: Mapping[str, Any],
    cwd: str | None = None,
    tool_version: str | None = None,
    state_token: str | None = None,
) -> FingerprintResult:
    if not isinstance(objective, str) or not objective.strip():
        raise ValueError("objective must be a non-empty string")
    if not isinstance(route, str) or not route.strip():
        raise ValueError("route must be a non-empty string")
    input_digest = _digest(_canonical_json(dict(material_inputs)))
    objective_digest = _digest(objective)
    state_token_digest = _digest(state_token) if state_token is not None else None
    canonicalizer_version = _canonicalizer(route)
    identity = {
        "canonicalizer_version": canonicalizer_version,
        "cwd": cwd,
        "fingerprint_version": FINGERPRINT_VERSION,
        "input_digest": input_digest,
        "objective_digest": objective_digest,
        "route": route,
        "state_token_digest": state_token_digest,
        "tool_version": tool_version,
    }
    return FingerprintResult(
        digest=_digest(_canonical_json(identity)),
        input_digest=input_digest,
        objective_digest=objective_digest,
        canonicalizer_version=canonicalizer_version,
        state_token_digest=state_token_digest,
    )
```

Change `policy.fingerprint(proposal)` to call `build_fingerprint` with the proposal's objective, route, material inputs, optional `cwd`, optional `tool_version`, and optional `state_token`, returning `.digest`. Change `CallProposal.fingerprint` to the same call and expose a `fingerprint_result` property so later tasks do not recompute it. Delivery IDs are an adapter-envelope concern: the Codex normalizer in Task 7 must never copy `session_id`, `turn_id`, `tool_use_id`, raw `agent_id`, hook timestamps, or event IDs into tool material inputs. A real tool argument with any of those key names remains material.

- [ ] **Step 4: Verify fingerprint and policy regressions**

Run:

```powershell
python -m unittest tests.test_fingerprint tests.test_governor tests.test_governed_runtime -v
python evals/run_evals.py
python evals/run_runtime_evals.py
```

Expected: all focused tests PASS; deterministic evaluation reports zero failures and retains full necessary-call preservation and redundant-call blocking.

- [ ] **Step 5: Commit fingerprint v2**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add skills/agent-call-governor/scripts/agent_call_governor_runtime tests/test_fingerprint.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Add deterministic fingerprint v2"
```

---

### Task 3: Add Source-Aware Redaction Before Persistence and Export

**Files:**
- Create: `skills/agent-call-governor/scripts/agent_call_governor_runtime/redaction.py`
- Create: `tests/test_redaction.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/models.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/ledger.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/cli.py`

**Interfaces:**
- Consumes: arbitrary metadata mappings at model, ledger, and export boundaries.
- Produces: `SAFE_METADATA_BY_SOURCE`, `redact_text`, `sanitize_metadata(metadata, *, source, home_directory)`, and `sanitize_event_dict(value, *, home_directory)`; callers pass the event `source` explicitly.

- [ ] **Step 1: Write privacy-canary tests that fail against current storage**

Create `tests/test_redaction.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and verify the missing-module failure**

Run: `python -m unittest tests.test_redaction -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_call_governor_runtime.redaction'`.

- [ ] **Step 3: Implement recursive redaction and wire both write boundaries**

Implement these exact public definitions in `redaction.py`:

```python
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


SAFE_METADATA_BY_SOURCE = {
    "runtime": frozenset({"cancelled_before_execution", "exit_code", "file_changed", "test_status"}),
    "codex-hook": frozenset({
        "acceptance_criterion_status", "cancelled_before_execution", "exit_code",
        "file_changed", "new_unique_source_count", "result_digest_changed", "test_status",
    }),
    "agents-sdk": frozenset({"exit_code", "file_changed", "test_status"}),
    "migration": frozenset({"legacy_schema_version"}),
}
SAFE_ENUMS = {
    "acceptance_criterion_status": frozenset({"satisfied", "not_satisfied", "unknown"}),
    "test_status": frozenset({"passed", "failed", "unknown"}),
}
SAFE_SCALAR_TYPES = {
    "cancelled_before_execution": bool,
    "exit_code": int,
    "file_changed": bool,
    "legacy_schema_version": int,
    "new_unique_source_count": int,
    "result_digest_changed": bool,
}
SENSITIVE_KEYS = frozenset({
    "authorization", "exception", "exception_message", "input", "objective", "output",
    "prompt", "raw_input", "raw_output", "tool_input", "tool_output",
})
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_API_KEY = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def _hash(value: str) -> str:
    if value.startswith("sha256:") and len(value) == 71:
        return value
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def redact_text(value: str, *, home_directory: str | None = None) -> str:
    safe = _BEARER.sub("[REDACTED_AUTHORIZATION]", value)
    safe = _API_KEY.sub("[REDACTED_API_KEY]", safe)
    safe = _EMAIL.sub("[REDACTED_EMAIL]", safe)
    home = str(Path(home_directory).expanduser()) if home_directory else str(Path.home())
    if home:
        safe = re.sub(re.escape(home), "[REDACTED_HOME]", safe, flags=re.IGNORECASE)
        safe = re.sub(re.escape(home.replace("\\", "/")), "[REDACTED_HOME]", safe, flags=re.IGNORECASE)
    return safe


def _safe_value(value: Any, *, known: bool, home_directory: str | None) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        redacted = redact_text(value, home_directory=home_directory)
        return redacted if known else _hash(redacted)
    if isinstance(value, list):
        return [_safe_value(item, known=known, home_directory=home_directory) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item, known=known and str(key) not in SENSITIVE_KEYS,
                                  home_directory=home_directory)
            for key, item in value.items()
        }
    return _hash(repr(type(value).__name__))


def sanitize_metadata(
    metadata: Mapping[str, Any],
    *,
    source: str,
    home_directory: str | None = None,
) -> dict[str, Any]:
    allowed = SAFE_METADATA_BY_SOURCE.get(source, frozenset())
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        name = str(key)
        if name.startswith("custom:") and len(name) == 71:
            result[name] = _safe_value(value, known=False, home_directory=home_directory)
        elif name in SENSITIVE_KEYS:
            result[name] = "[REDACTED]"
        elif name in allowed:
            if name in SAFE_ENUMS and value not in SAFE_ENUMS[name]:
                result[name] = _hash(str(value))
            elif name in SAFE_SCALAR_TYPES and type(value) is not SAFE_SCALAR_TYPES[name]:
                result[name] = _hash(str(value))
            else:
                result[name] = _safe_value(value, known=True, home_directory=home_directory)
        else:
            result["custom:" + _hash(name)[7:]] = _safe_value(
                value, known=False, home_directory=home_directory
            )
    return result


def sanitize_event_dict(
    value: Mapping[str, Any], *, home_directory: str | None = None
) -> dict[str, Any]:
    event = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    source = str(event.get("source", "runtime"))
    metadata = event.pop("metadata", event.get("safe_metadata_json", {}))
    event["safe_metadata_json"] = sanitize_metadata(
        metadata if isinstance(metadata, Mapping) else {},
        source=source,
        home_directory=home_directory,
    )
    for key in SENSITIVE_KEYS:
        event.pop(key, None)
    event["raw_input_stored"] = False
    return event
```

Call `sanitize_metadata(event.metadata, source=event.source)` in `CallEvent.__post_init__` and again immediately before the ledger serializes `safe_metadata_json`. Change CLI export to pass `event.to_dict()` through `sanitize_event_dict` before `json.dumps`. Preserve only `error_type`; never serialize an exception's message.

- [ ] **Step 4: Run privacy tests and existing persistence/export tests**

Run:

```powershell
python -m unittest tests.test_redaction tests.test_runtime_ledger tests.test_runtime_cli tests.test_codex_hook -v
```

Expected: all tests PASS; the canary strings are absent from stored and exported representations.

- [ ] **Step 5: Commit redaction boundaries**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add skills/agent-call-governor/scripts/agent_call_governor_runtime tests/test_redaction.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Sanitize persisted and exported metadata"
```

---

### Task 4: Introduce Event Schema v2 and Idempotent v0.2 Migration

**Files:**
- Create: `schemas/event-v2.schema.json`
- Create: `schemas/policy-facts-v1.schema.json`
- Create: `tests/test_schema_migration.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/models.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/ledger.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/policy.py`
- Modify: `tests/test_runtime_ledger.py`
- Modify: `tests/test_governor.py`

**Interfaces:**
- Consumes: v0.2 `call_events` databases with `PRAGMA user_version = 0` and current known columns.
- Produces: `SCHEMA_VERSION`, `POLICY_VERSION`, `POLICY_FACTS_VERSION`, canonical event fields, ordered migration, future-version rejection, and an explicit legacy-v1/current-v2 duplicate boundary.

- [ ] **Step 1: Write fresh-schema, migration, reopen, future-version, and privacy tests**

Create `tests/test_schema_migration.py` with a helper that creates the released v0.2 shape and inserts one legacy row:

```python
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from agent_call_governor_runtime import CallEvent
from agent_call_governor_runtime.ledger import (
    CallLedger, FutureSchemaError, SCHEMA_VERSION, UnsupportedLegacySchemaError,
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


def create_v02_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(LEGACY_COLUMNS)
    connection.execute(
        "INSERT INTO call_events VALUES "
        "(1,'event-1',1,'session-ref','call-ref',NULL,'completed','2026-07-13T00:00:00Z',"
        "'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',"
        "'Bash','sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',"
        "'direct-tool','balanced','medium','observe',1,1,'allowed','material_progress',1.5,"
        "'runtime',?,NULL)",
        (json.dumps({"custom_note": "private legacy value"}),),
    )
    connection.commit()
    connection.close()


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
        self.assertTrue({"event_type", "trace_id", "span_id", "safe_metadata_json", "policy_facts_json"} <= columns)
        connection.close()

    def test_v02_migration_preserves_event_and_removes_plaintext_metadata(self) -> None:
        create_v02_database(self.path)
        ledger = CallLedger(self.path)
        events = ledger.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "call.completed")
        self.assertEqual(events[0].fingerprint_version, 1)
        self.assertEqual(events[0].policy_version, "legacy-v0.2")
        self.assertNotIn("private legacy value", self.path.read_bytes().decode("utf-8", errors="ignore"))

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

    def test_unknown_user_version_zero_shape_is_rejected(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE call_events (seq INTEGER PRIMARY KEY, unknown TEXT)")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(UnsupportedLegacySchemaError, "unrecognized legacy schema"):
            CallLedger(self.path)

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
            session_id="sha256:" + "1" * 64, call_id="sha256:" + "2" * 64,
            phase="proposed", occurred_at="2026-07-14T00:00:00Z",
            objective="sha256:" + "a" * 64, route="Bash",
            fingerprint="sha256:" + "b" * 64, budget_kind="agent",
            profile="balanced", quality_risk="medium", mode="observe",
            event_type="call.proposed", observed_at="2026-07-14T00:00:00Z",
            trace_id="sha256:" + "1" * 64, turn_id="sha256:" + "3" * 64,
            span_id="sha256:" + "2" * 64, parent_span_id=None,
            source="runtime", source_event="test", input_digest="sha256:" + "c" * 64,
            fingerprint_version=2, failure_policy="fail-open",
            policy_facts=facts, raw_input_stored=False,
        )
        event_schema = json.loads((ROOT / "schemas" / "event-v2.schema.json").read_text(encoding="utf-8"))
        facts_schema = json.loads((ROOT / "schemas" / "policy-facts-v1.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(event_schema)
        Draft202012Validator.check_schema(facts_schema)
        Draft202012Validator(event_schema).validate(event.to_dict())
        Draft202012Validator(facts_schema).validate(event.to_dict()["policy_facts_json"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the migration tests and verify missing schema symbols**

Run: `python -m unittest tests.test_schema_migration -v`

Expected: FAIL importing `FutureSchemaError` or `SCHEMA_VERSION`.

- [ ] **Step 3: Add the v2 model, schemas, and exclusive ordered migration**

Define each constant with its owning responsibility: `SCHEMA_VERSION` in `ledger.py`, `POLICY_FACTS_VERSION` in `models.py`, and `POLICY_VERSION` in `policy.py`. Import `FINGERPRINT_VERSION` only from `fingerprint.py`:

```python
# ledger.py
SCHEMA_VERSION = 2

# models.py
POLICY_FACTS_VERSION = 1

# policy.py
POLICY_VERSION = "2026-07-14.1"


class FutureSchemaError(RuntimeError):
    pass


class UnsupportedLegacySchemaError(RuntimeError):
    pass


class UnsupportedSchemaVersionError(RuntimeError):
    pass
```

Extend `CallEvent` with exact fields while retaining existing `phase`, `session_id`, and `call_id`:

```python
event_type: str
observed_at: str
trace_id: str
turn_id: str | None
span_id: str
parent_span_id: str | None
source_event: str
agent_id: str | None = None
tool_name: str | None = None
input_digest: str | None = None
fingerprint_version: int | None = None
failure_policy: str = "fail-open"
decision: str | None = None
reason_code: str | None = None
policy_version: str | None = None
budget_before: int | None = None
budget_after: int | None = None
decision_latency_ms: float | None = None
execution_latency_ms: float | None = None
status: str | None = None
prompt_tokens: int | None = None
completion_tokens: int | None = None
total_tokens: int | None = None
estimated_cost_usd: float | None = None
pricing_version: str | None = None
raw_input_stored: bool = False
policy_facts: Mapping[str, Any] | None = None
schema_version: int = 2
```

Validate `event_type` against the ten canonical values, force `raw_input_stored is False`, validate non-negative nullable numeric fields, and require `pricing_version` whenever `estimated_cost_usd` is non-null. Keep internal mappings as `metadata` and `policy_facts`, but export them from `to_dict()` under the approved keys `safe_metadata_json` and `policy_facts_json` as JSON objects.

In `CallLedger._initialize`, set WAL, start `BEGIN EXCLUSIVE`, read `PRAGMA user_version`, and apply exactly one path:

```python
if user_version > SCHEMA_VERSION:
    raise FutureSchemaError(
        f"database schema {user_version} requires a newer Agent Call Governor; supported={SCHEMA_VERSION}"
    )
if user_version not in {0, SCHEMA_VERSION}:
    raise UnsupportedSchemaVersionError(
        f"unsupported schema version {user_version}; supported migration is released v0.2 user_version 0 to schema 2"
    )
if user_version == 0 and not self._table_exists(connection, "call_events"):
    self._create_schema_v2(connection)
elif user_version == 0:
    self._verify_legacy_v02_columns(connection)
    self._migrate_v02_to_v2(connection)
elif user_version == SCHEMA_VERSION:
    self._verify_schema_v2(connection)
connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
connection.commit()
```

`_verify_legacy_v02_columns` compares `PRAGMA table_info(call_events)` to the exact released v0.2 column set in the fixture. If a user-version-zero database has a different shape, roll back and raise an actionable `UnsupportedLegacySchemaError` rather than guessing a migration.

Add these nullable columns during migration: `event_type`, `observed_at`, `trace_id`, `turn_id`, `span_id`, `parent_span_id`, `source_event`, `agent_id`, `tool_name`, `input_digest`, `fingerprint_version`, `failure_policy`, `decision`, `reason_code`, `policy_version`, `budget_before`, `budget_after`, `decision_latency_ms`, `execution_latency_ms`, `status`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `estimated_cost_usd`, `pricing_version`, `raw_input_stored`, `safe_metadata_json`, and `policy_facts_json`. Backfill event type with this exact phase map:

```python
LEGACY_EVENT_TYPES = {
    "proposed": "call.proposed",
    "blocked": "call.blocked",
    "started": "call.started",
    "completed": "call.completed",
    "failed": "call.failed",
    "cancelled": "call.cancelled",
}
```

Backfill `observed_at = occurred_at`, `trace_id = session_id`, `span_id = call_id`, `source_event = 'legacy'`, `fingerprint_version = 1`, `failure_policy = 'legacy-unknown'`, `policy_version = 'legacy-v0.2'`, and `raw_input_stored = 0`. Version 1 is an honest marker for the released v0.2 identity algorithm; never rewrite the digest or label it v2 because raw identity inputs are intentionally unavailable. Enable `PRAGMA secure_delete = ON` before rewriting every `metadata_json` through the Python sanitizer, write the safe result to both `metadata_json` and `safe_metadata_json`, and never synthesize `call.proposed` rows. After the migration transaction commits, run `PRAGMA wal_checkpoint(TRUNCATE)` and `VACUUM` outside the transaction so the released v0.2 plaintext canary is absent from database, WAL, and free pages.

Include `fingerprint_version` in ledger history only when the stored column is non-null. Update policy duplicate lookup so a matching-budget history entry always contributes to `matching_history_count` and progress, while it enters the exact-duplicate set only when `fingerprint_version` is absent (pre-1.0 in-memory compatibility) or equals `FINGERPRINT_VERSION`. Add a governor regression where a version-1 history fingerprint equals the current v2 digest: the call is not blocked as `duplicate_fingerprint`, but the legacy row still consumes one budget unit. Keep the existing missing-version and explicit-v2 duplicate tests blocking as before.

Create both JSON Schemas with `additionalProperties: false`, required privacy flag `raw_input_stored` fixed to `false`, the ten event types, SHA-256 string patterns, event `fingerprint_version` restricted to `null`, `1`, or `2`, nullable usage fields, `safe_metadata_json` and `policy_facts_json` object fields, and the exact policy-facts fields from Task 6. Policy facts describe new proposals and therefore require version 2. Add a schema test that loads both files with `json.loads`, runs `jsonschema.Draft202012Validator.check_schema`, validates a fresh event's `to_dict()` against `event-v2.schema.json`, and validates its `policy_facts_json` against `policy-facts-v1.schema.json`.

- [ ] **Step 4: Run schema, ledger, and privacy regressions**

Run:

```powershell
python -m unittest tests.test_schema_migration tests.test_runtime_ledger tests.test_redaction -v
```

Expected: all tests PASS; reopen adds no rows; future schema produces the actionable error; legacy plaintext canary is absent.

- [ ] **Step 5: Commit schema v2 and migration**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add schemas skills/agent-call-governor/scripts/agent_call_governor_runtime tests/test_schema_migration.py tests/test_runtime_ledger.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Migrate runtime ledger to event schema v2"
```

---

### Task 5: Add Ledger Operations, Retention, Recovery, and Secure Deletion

**Files:**
- Create: `tests/test_ledger_operations.py`
- Create: `tests/helpers.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/models.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/ledger.py`
- Modify: `tests/test_runtime_ledger.py`

**Interfaces:**
- Consumes: schema-v2 rows and sanitized `CallEvent` objects from Tasks 3-4.
- Produces: `SessionSummary`, configurable busy timeout, session queries, retention, stale recovery, secure deletion, and private path creation.

- [ ] **Step 1: Write operation tests with fixed timestamps**

Create `tests/test_ledger_operations.py` covering these public calls:

```python
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_call_governor_runtime import CallEvent, CallLedger
from tests.helpers import make_event


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


class LedgerOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "private" / "events.sqlite3"
        self.ledger = CallLedger(self.db, busy_timeout_ms=1000)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_busy_timeout_and_session_summary(self) -> None:
        connection = self.ledger._connect()
        self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 1000)
        connection.close()
        self.assertEqual(self.ledger.sessions(), [])

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
```

Create this test-only factory in `tests/helpers.py`; production code must not contain test factories:

```python
from __future__ import annotations

from agent_call_governor_runtime import CallEvent


PHASES = {
    "call.proposed": "proposed", "policy.decided": "proposed",
    "call.started": "started", "call.blocked": "blocked",
    "call.completed": "completed", "call.failed": "failed",
    "call.cancelled": "cancelled", "session.started": "started",
    "progress.observed": "completed", "session.stopped": "completed",
}


def make_event(
    *,
    session_id: str,
    call_id: str,
    event_type: str = "call.completed",
    observed_at: str = "2026-07-14T00:00:00Z",
) -> CallEvent:
    return CallEvent.create(
        session_id=session_id,
        call_id=call_id,
        phase=PHASES[event_type],
        occurred_at=observed_at,
        objective="sha256:" + "a" * 64,
        route="test:fixture",
        fingerprint="sha256:" + "b" * 64,
        budget_kind="agent",
        profile="balanced",
        quality_risk="medium",
        mode="observe",
        event_type=event_type,
        observed_at=observed_at,
        trace_id=session_id,
        turn_id=None,
        span_id=call_id,
        parent_span_id=None,
        source="runtime",
        source_event="test",
        fingerprint_version=2,
        failure_policy="fail-open",
        progress="unknown" if event_type == "call.completed" else None,
        raw_input_stored=False,
    )
```

- [ ] **Step 2: Run operation tests and verify missing constructor/method failures**

Run: `python -m unittest tests.test_ledger_operations -v`

Expected: FAIL because `busy_timeout_ms`, `sessions`, `inspect_session`, `prune_expired_sessions`, `recover_stale_reservations`, or `delete_session` is not implemented.

- [ ] **Step 3: Implement exact operation interfaces and transaction boundaries**

Add to `models.py`:

```python
@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    first_observed_at: str
    last_observed_at: str
    call_count: int
    event_count: int
    final_status: str
```

`SessionSummary.session_id` is the event `trace_id`, not the turn-scoped compatibility `session_id`. For application-owned runtimes, `trace_id` defaults to the proposal session ID; for Codex it is the SHA-256 host-session reference. `sessions`, `inspect_session`, retention, recovery, and deletion group/query by `trace_id`. Keep `events(session_id)` and `history(session_id)` as the pre-1.0 policy-scope compatibility APIs.

For summaries, `call_count` is the count of distinct `call_id` values having a `call.proposed` row, `event_count` counts every row in the trace, and `final_status` is the latest canonical event type. A trace with any open call or a `session.started` row without a later `session.stopped` reports `active`.

Change the constructor to `CallLedger(sqlite_path: str | Path, jsonl_path: str | Path | None = None, *, busy_timeout_ms: int = 30000)`. Add the exact methods `sessions(self) -> list[SessionSummary]`, `inspect_session(self, session_id: str) -> list[CallEvent]`, `delete_session(self, session_id: str) -> int`, `prune_expired_sessions(self, retention_days: int, *, now: datetime | None = None) -> list[str]`, and `recover_stale_reservations(self, trace_id: str, stale_after_seconds: int, *, now: datetime | None = None) -> int`.

Validate `busy_timeout_ms` as an integer from `0` through `60000`, `retention_days` as a positive integer, and `stale_after_seconds` as a non-negative integer where zero disables recovery. Set `PRAGMA busy_timeout` on every connection, and use 1000 only when the plugin constructs the ledger. Create the parent directory with `0o700`, create the DB, then `chmod(0o600)` on POSIX; Windows returns a best-effort status for `doctor` instead of claiming equivalent ACL enforcement.

For retention, group by `trace_id`, select traces whose maximum `observed_at` is older than the cutoff, and exclude every trace with a `call.started` lacking a later terminal event. Also exclude a Codex trace containing `session.started` without a later `session.stopped`; v0.3 treats an abandoned no-Stop trace conservatively as active and requires manual deletion. For recovery, select open starts for the requested `trace_id` older than the threshold and append `call.cancelled` with the original call/span references, `progress = "unknown"`, and `reason_code = "stale_reservation_recovered"`; never infer no-progress from age and never update the start row.

For deletion use these transaction boundaries:

```python
connection.execute("PRAGMA secure_delete = ON")
connection.execute("BEGIN IMMEDIATE")
cursor = connection.execute("DELETE FROM call_events WHERE trace_id = ?", (session_id,))
deleted = cursor.rowcount
connection.commit()
connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
connection.execute("VACUUM")
return deleted
```

Issue `DeprecationWarning` when `jsonl_path` is not `None`; retain existing mirror behavior only for compatibility tests, and never pass it from plugin code.

- [ ] **Step 4: Run operation, concurrency, and ledger regressions**

Run:

```powershell
python -m unittest tests.test_ledger_operations tests.test_runtime_ledger tests.test_governed_runtime -v
```

Expected: all tests PASS; recovery appends rather than mutates; deletion keeps unrelated sessions; legacy wrapper concurrency remains green.

- [ ] **Step 5: Commit ledger operations**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add skills/agent-call-governor/scripts/agent_call_governor_runtime tests/test_ledger_operations.py tests/helpers.py tests/test_runtime_ledger.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Add private ledger maintenance operations"
```

---

### Task 6: Emit Privacy-Safe Policy Facts and Canonical v2 Call Events

**Files:**
- Create: `tests/test_event_v2_runtime.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/models.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/runtime.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/ledger.py`
- Modify: `tests/test_governed_runtime.py`
- Modify: `tests/test_governor.py`

**Interfaces:**
- Consumes: `CallProposal.fingerprint_result`, schema-v2 `CallEvent`, and `CallLedger.atomic_transition`.
- Produces: `build_policy_facts`, three-event atomic reservation sequence, budget/latency fields, and strict same-call-ID decision replay.

- [ ] **Step 1: Write event-sequence, policy-facts, and idempotency tests**

Create `tests/test_event_v2_runtime.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_call_governor_runtime import CallLedger, CallProposal, GovernedRuntime
from agent_call_governor_runtime.ledger import DuplicateCallIdError


def proposal() -> CallProposal:
    return CallProposal(
        session_id="sha256:" + "1" * 64,
        objective="private objective",
        route="Bash",
        capability_gap="private gap",
        expected_new_information="private expectation",
        stop_condition="private stop condition",
        material_inputs={"command": "git status"},
        budget_kind="direct-tool",
        budget_limit=3,
        profile="balanced",
        quality_risk="medium",
        metadata={"cwd": "C:/repo"},
    )


class EventV2RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.ledger = CallLedger(Path(self.tempdir.name) / "events.sqlite3")
        self.runtime = GovernedRuntime(self.ledger, mode="observe")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_begin_is_one_atomic_three_event_sequence(self) -> None:
        handle = self.runtime.begin(proposal(), call_id="host-call-ref")
        events = self.ledger.events(proposal().session_id)
        self.assertEqual([event.event_type for event in events], [
            "call.proposed", "policy.decided", "call.started"
        ])
        self.assertEqual(events[0].policy_facts["fingerprint_version"], 2)
        self.assertEqual(events[1].policy_version, "2026-07-14.1")
        self.assertIsNotNone(events[1].decision_latency_ms)
        self.assertEqual(events[1].budget_before, 6)
        self.assertEqual(events[1].budget_after, 5)

    def test_same_call_id_and_fingerprint_returns_persisted_decision(self) -> None:
        first = self.runtime.begin(proposal(), call_id="host-call-ref")
        second = self.runtime.begin(proposal(), call_id="host-call-ref")
        self.assertEqual(first.decision, second.decision)
        self.assertEqual(len(self.ledger.events(proposal().session_id)), 3)

    def test_same_call_id_with_different_fingerprint_is_rejected(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        changed = proposal().__class__(**{
            **proposal().__dict__, "material_inputs": {"command": "git diff"}
        })
        with self.assertRaises(DuplicateCallIdError):
            self.runtime.begin(changed, call_id="host-call-ref")

    def test_policy_facts_and_database_exclude_raw_proposal_text(self) -> None:
        self.runtime.begin(proposal(), call_id="host-call-ref")
        encoded = json.dumps([event.to_dict() for event in self.ledger.events()], sort_keys=True)
        raw_db = self.ledger.sqlite_path.read_bytes().decode("utf-8", errors="ignore")
        for canary in ("private objective", "private gap", "private expectation", "private stop condition"):
            self.assertNotIn(canary, encoded)
            self.assertNotIn(canary, raw_db)


if __name__ == "__main__":
    unittest.main()
```

Add this regression to `GovernorTests`:

```python
def test_unknown_progress_consumes_budget_without_triggering_a_stop_rule(self):
    result = governor.evaluate({
        "proposal": proposal(route="different-agent"),
        "profile": "balanced",
        "budget": {"limit": 2},
        "history": [{"budget_kind": "agent", "progress": "unknown"}],
    })
    self.assertTrue(result["allowed"])
    self.assertEqual(result["reason"], "allowed")
    self.assertEqual(result["matching_history_count"], 1)
    self.assertEqual(result["remaining_after_call"], 0)
```

The expected atomic begin sequence has three rows: `call.proposed`, `policy.decided`, and `call.started` or `call.blocked`. Completion is the fourth lifecycle row and is tested separately.

- [ ] **Step 2: Run the focused tests and verify event-sequence failures**

Run: `python -m unittest tests.test_event_v2_runtime tests.test_governor -v`

Expected: FAIL because the current begin sequence lacks `policy.decided`, policy facts, and same-call-ID decision replay, and `unknown` is not yet a valid progress value.

- [ ] **Step 3: Build exact safe facts and reserve the sequence in one transaction**

Add to `models.py`:

```python
def build_policy_facts(proposal: CallProposal) -> dict[str, Any]:
    result = proposal.fingerprint_result
    return {
        "fingerprint": result.digest,
        "fingerprint_version": result.version,
        "budget_kind": proposal.budget_kind,
        "requested_budget_limit": proposal.budget_limit,
        "profile": proposal.profile,
        "risk": proposal.quality_risk,
        "mandatory_reason": proposal.mandatory_reason,
        "changed_strategy_present": proposal.changed_strategy is not None,
        "required_fields_present": all((
            bool(proposal.capability_gap),
            bool(proposal.expected_new_information),
            bool(proposal.stop_condition),
        )),
        "duplicate_scope": "turn",
        "state_token_digest": result.state_token_digest,
        "policy_facts_version": 1,
    }
```

Extend `CallProposal` with nullable `trace_id` and `turn_id`. `trace_id` defaults to `session_id` for application-owned wrappers; `session_id` remains the policy/duplicate scope, while Codex supplies a turn-scoped `session_id`, a hashed host-session `trace_id`, and a hashed host-turn `turn_id`. Event creation copies those values without reconstructing raw IDs.

Add `unknown` to `policy.PROGRESS_VALUES`. Count every history row, including unknown, for budget use and exact duplicate lookup. Derive `actionable_progress = [value for value in progress if value != "unknown"]` and use only that filtered sequence for sufficient, no-progress, low-progress, and changed-strategy stopping rules.

Do not add objective, gap, expectation, stop text, material inputs, cwd, tool result, or raw state token to this mapping.

Change `RuntimeDecision` to include exact nullable `budget_before`, `budget_after`, and `decision_latency_ms` fields while retaining `remaining_after_call` as a compatibility alias of `budget_after`. Measure only policy evaluation with `time.perf_counter_ns()`.

Budget telemetry records the effective quality-preserving policy limit, not the raw requested limit. For example, balanced/medium direct-tool work requesting `3` inherits the profile floor of `6`, so the first executable call records `budget_before = 6` and `budget_after = 5`. This keeps observability consistent with the decision that was actually enforced.

Persist event `decision` as `allow` when policy allows, `would_block` when policy denies in observe/warn mode, `block` when policy denies in an application-owned enforce mode, and `internal_error` for evaluator/ledger fail-open or fail-closed paths. Persist canonical `status` values `proposed`, `decided`, `running`, `blocked`, `completed`, `failed`, `cancelled`, `observed`, `started`, and `stopped` according to event type; keep `reason_code` equal to the stable policy reason rather than human warning copy.

Change ledger atomic reservation to this interface:

```python
def atomic_transition(
    self,
    session_id: str,
    call_id: str,
    fingerprint: str,
    operation: Callable[[list[dict[str, str]]], tuple[RuntimeDecision, list[CallEvent]]],
) -> RuntimeDecision:
```

Inside one `BEGIN IMMEDIATE`, first query rows for `(session_id, call_id)`. If present with the same fingerprint, reconstruct and return the stored `policy.decided` decision without inserting. Replay may contain the three-row reservation plus one canonical terminal row that a later completion API appended. If present with a different fingerprint, raise `DuplicateCallIdError`. Otherwise calculate history, evaluate policy, and require the fresh callback to insert exactly the ordered three-row sequence `call.proposed`, `policy.decided`, then `call.started` or `call.blocked` before commit; a fresh callback cannot pre-complete the call.

Rework `CallLedger.history` to emit exactly one policy-history item per logical call. It considers only `call.started`, `call.completed`, `call.failed`, and pre-execution `call.cancelled`, groups by `(session_id, call_id)`, counts started/completed/failed calls as consumed, releases a start followed by pre-execution cancellation, and ignores `session.*`, `call.proposed`, `policy.decided`, and `progress.observed` rows for budget counts.

Map compatibility phase as `proposed` for both proposal and policy decision, `started` for call start, and `blocked` for block. Set `input_digest`, fingerprint version, policy version, failure policy, reason code, budget fields, and policy facts on their applicable rows. Terminal events set `execution_latency_ms`; wrapper defaults remain compatible, while Codex-specific unknown progress is applied in Task 7.

- [ ] **Step 4: Run event, runtime, concurrency, and eval regressions**

Run:

```powershell
python -m unittest tests.test_event_v2_runtime tests.test_governed_runtime tests.test_runtime_ledger tests.test_governor -v
python evals/run_runtime_evals.py
```

Expected: focused tests PASS; concurrent final-budget reservation still permits only one counted call; runtime eval has zero under-call and redundant-execution failures.

- [ ] **Step 5: Commit canonical event emission**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add skills/agent-call-governor/scripts/agent_call_governor_runtime tests/test_event_v2_runtime.py tests/test_governed_runtime.py tests/test_governor.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Record v2 policy decisions atomically"
```

---

### Task 7: Implement All Six Codex Hooks with Process-Level Fail-Open

**Files:**
- Create: `tests/fixtures/codex-hooks/session_start.json`
- Create: `tests/fixtures/codex-hooks/stop.json`
- Create: `tests/test_plugin_dispatch.py`
- Modify: `hooks/dispatch.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/codex_hook.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/cli.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/ledger.py`
- Modify: `tests/test_codex_hook.py`
- Modify: `tests/test_plugin_package.py`

**Interfaces:**
- Consumes: Codex hook JSON on stdin, `PLUGIN_ROOT`, `PLUGIN_DATA`, schema-v2 ledger, and `GovernedRuntime`.
- Produces: six supported host normalizers, hashed identifiers, five-second SessionStart deduplication, Stop turn idempotency, safe `systemMessage`, and an always-zero dispatcher failure boundary.

- [ ] **Step 1: Add fixtures and write failing lifecycle/dispatcher tests**

Create `session_start.json`:

```json
{
  "hook_event_name": "SessionStart",
  "session_id": "private-session-id",
  "cwd": "C:/Users/Ada/private-repo",
  "source": "startup"
}
```

Create `stop.json`:

```json
{
  "hook_event_name": "Stop",
  "session_id": "private-session-id",
  "turn_id": "private-turn-id",
  "cwd": "C:/Users/Ada/private-repo",
  "stop_hook_active": false
}
```

Extend `tests/test_codex_hook.py` and create `tests/test_plugin_dispatch.py` with these assertions:

```python
def test_all_six_events_record_canonical_lifecycle(self) -> None:
    for fixture in (
        "session_start.json", "pre_tool_use.json", "post_tool_use.json",
        "subagent_start.json", "subagent_stop.json", "stop.json",
    ):
        self.assertIsNone(handle_codex_hook(load_fixture(fixture), self.runtime()))
    self.assertEqual(
        [event.event_type for event in self.ledger.events()],
        [
            "session.started", "call.proposed", "policy.decided", "call.started",
            "call.completed", "call.proposed", "policy.decided", "call.started",
            "call.completed", "session.stopped",
        ],
    )
    self.assertEqual(
        [event.progress for event in self.ledger.events() if event.event_type == "call.completed"],
        ["unknown", "unknown"],
    )

def test_session_start_deduplicates_only_inside_five_second_window(self) -> None:
    payload = load_fixture("session_start.json")
    handle_codex_hook(payload, self.runtime(), observed_at="2026-07-14T00:00:00Z")
    handle_codex_hook(payload, self.runtime(), observed_at="2026-07-14T00:00:04Z")
    handle_codex_hook(payload, self.runtime(), observed_at="2026-07-14T00:00:06Z")
    starts = [event for event in self.ledger.events() if event.event_type == "session.started"]
    self.assertEqual(len(starts), 2)

def test_stop_uses_turn_identifier_for_idempotency_when_present(self) -> None:
    payload = load_fixture("stop.json")
    handle_codex_hook(payload, self.runtime())
    handle_codex_hook(payload, self.runtime())
    self.assertEqual(
        len([event for event in self.ledger.events() if event.event_type == "session.stopped"]), 1
    )

def test_raw_host_identifiers_never_persist(self) -> None:
    for fixture in ("session_start.json", "pre_tool_use.json", "stop.json"):
        handle_codex_hook(load_fixture(fixture), self.runtime())
    raw = self.ledger.sqlite_path.read_bytes().decode("utf-8", errors="ignore")
    for canary in ("private-session-id", "private-turn-id", "tool-call-1"):
        self.assertNotIn(canary, raw)

def test_deterministic_file_change_emits_progress_observation(self) -> None:
    handle_codex_hook(load_fixture("pre_tool_use.json"), self.runtime())
    payload = load_fixture("post_tool_use.json")
    payload["tool_response"]["file_changed"] = True
    handle_codex_hook(payload, self.runtime())
    events = self.ledger.events()
    self.assertEqual([event.event_type for event in events[-2:]], [
        "call.completed", "progress.observed"
    ])
    self.assertEqual(events[-2].progress, "material_progress")
    self.assertEqual(events[-1].progress, "material_progress")
```

In `tests/test_plugin_dispatch.py`, launch `hooks/dispatch.py` with a temporary `PLUGIN_DATA`, assert valid input writes `events.sqlite3`, malformed JSON exits `0` with empty stdout, and stderr contains only `agent-call-governor hook unavailable: JSONDecodeError` without raw stdin or exception message.

- [ ] **Step 2: Run hook tests and verify unsupported-event/dispatcher failures**

Run:

```powershell
python -m unittest tests.test_codex_hook tests.test_plugin_dispatch -v
```

Expected: FAIL because `SessionStart` and `Stop` are ignored, successful Codex completion is not `unknown`, or dispatcher configuration is incomplete.

- [ ] **Step 3: Normalize six events and make the dispatcher a strict fail-open boundary**

In `codex_hook.py`, set:

```python
SUPPORTED_EVENTS = frozenset({
    "SessionStart", "PreToolUse", "PostToolUse",
    "SubagentStart", "SubagentStop", "Stop",
})


def host_reference(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
```

Use hashed session, turn, tool-use, and agent IDs for `trace_id`, `turn_id`, `span_id`, and `call_id`. Never place raw IDs in metadata. Use exact host call IDs for strict tool/subagent delivery idempotency only after hashing them.

Change the handler signature so tests can inject time without sleeping:

```python
def handle_codex_hook(
    payload: Mapping[str, Any],
    runtime: GovernedRuntime,
    *,
    observed_at: str | None = None,
    retention_days: int = 7,
    stale_reservation_seconds: int = 86400,
) -> dict[str, str] | None:
```

Implement the six branches exactly:

- `SessionStart`: migrate/open ledger, prune expired inactive sessions, recover stale starts for this hashed trace when threshold is positive, and call `append_bounded_delivery_if_new(event, delivery_digest, window_seconds=5)`.
- `PreToolUse`: build a `CallProposal` with hashed trace/turn references, tool name, exact sanitized material-input digest, plugin profile/risk, and atomically begin the call.
- `PostToolUse`: close the hashed tool-use call once; use `call.failed` with `error_type = "CodexToolError"` when `is_error` is true, otherwise `call.completed`; both default to `progress = "unknown"` unless exit-code/file-change/test-status evidence maps deterministically.
- `SubagentStart`: atomically begin one agent-budget call; a denied policy in warn mode emits a warning but never claims the subagent was prevented.
- `SubagentStop`: close the hashed agent call once with `progress = "unknown"`.
- `Stop`: append `session.stopped` once for the hashed turn ID when present; without a turn ID, use a five-second delivery digest window; then run best-effort retention.

Wrap retention and stale recovery as independent best-effort maintenance operations: a maintenance exception records no raw diagnostic data and does not prevent the valid `session.started` or `session.stopped` event from being processed. Migration/open failure remains at the outer dispatcher boundary and exits zero without hook output.

Add the exact ledger boundary `append_bounded_delivery_if_new(self, event: CallEvent, delivery_digest: str, *, window_seconds: int = 5) -> bool`. It runs in `BEGIN IMMEDIATE`, compares `input_digest = delivery_digest` only against rows with the same `event_type` and hashed trace whose `observed_at` is inside the window, inserts when no row matches, and returns whether it inserted. It stores only the SHA-256 delivery digest. Stop with a turn ID uses a transaction-safe append-if-new ledger boundary keyed by hashed call identity and event type; a precheck followed by ordinary append is not sufficient. Stop without one uses this same bounded-digest method with `event_type = "session.stopped"`.

For each SessionStart accepted outside the five-second window, generate a new UUID event/span/call reference so a legitimate later resume is not blocked by the strict tool-delivery index. Stop without a turn ID follows the same rule. Stop with a turn ID derives its call reference from the hashed turn ID and remains strictly idempotent for that turn.

For Codex call proposals, set `trace_id = host_reference(session_id)`, `turn_id = host_reference(turn_id)`, and compatibility policy scope `session_id = f"codex:trace:{trace_id[7:]}:turn:{turn_id[7:]}"`. Exact fingerprint duplicate lookup therefore resets at the turn boundary while CLI sessions continue to group the whole hashed trace.

When the host omits `turn_id`, retain the existing call-local fallback policy scope and store `turn_id = None`; do not reject the hook and do not synthesize a turn identifier from raw host data.

For `SubagentStart`, use a delegated-task digest from the first non-empty string among the explicit host fields `task`, `prompt`, and `description`. When the host exposes only `agent_type` and `agent_id`, include `opaque_invocation_digest = host_reference(agent_id)` in material inputs. This conservative fallback prevents two same-type agents with hidden tasks from becoming a false exact duplicate; repeated delivery of the same host ID is still collapsed by strict call-ID idempotency. Never persist the task text or raw agent ID.

Implement `deterministic_progress(payload) -> tuple[str, dict[str, bool | int | str]] | None`. Read strict-typed signals from either the top level or `tool_response`, and resolve conflicts with precedence `sufficient > material_progress > low_progress`. It returns `material_progress` for `file_changed is True`, a changed result digest, a positive new-source count, or test status `passed`; `sufficient` for explicit acceptance-criterion status `satisfied`; `low_progress` for a nonzero integer exit code; `material_progress` for exit code zero; and `None` when no listed valid signal exists. Persist only the allowlisted signal, never the result body. When a signal exists, set it on the terminal event and append a separate `progress.observed` event only if the terminal row was newly inserted; without a signal, set only terminal `progress = "unknown"` and do not fabricate a progress event.

Set `parent_span_id` only when the host explicitly provides a parent identifier and hash that identifier first. Leave it null when the host omits ancestry; never infer a parent from event order.

Only a warn decision returns this shape:

```python
return {
    "systemMessage": (
        "Agent Call Governor warning: policy would block this call "
        f"({decision.reason}). Codex hooks are observe/warn only."
    )
}
```

Replace `hooks/dispatch.py` with an env-only boundary that parses environment configuration before constructing the runtime. Update the earlier argv-based dispatcher assertions in `tests/test_plugin_package.py`; the package CLI retains its separate compatibility command, while the plugin dispatcher does not accept a second configuration surface:

```python
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _integer(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    value = default if raw is None else int(raw)
    if value < minimum or value > maximum:
        raise ValueError(f"invalid {name}")
    return value


def main() -> int:
    try:
        plugin_root = Path(os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1])).resolve()
        plugin_data = Path(
            os.environ.get("PLUGIN_DATA", Path.home() / ".codex" / "agent-call-governor")
        ).expanduser()
        sys.path.insert(0, str(plugin_root / "skills" / "agent-call-governor" / "scripts"))
        from agent_call_governor_runtime import CallLedger, GovernedRuntime
        from agent_call_governor_runtime.codex_hook import handle_codex_hook

        mode = os.environ.get("AGENT_CALL_GOVERNOR_MODE", "observe")
        profile = os.environ.get("AGENT_CALL_GOVERNOR_PROFILE", "balanced")
        risk = os.environ.get("AGENT_CALL_GOVERNOR_RISK", "medium")
        if mode not in {"observe", "warn"}:
            raise ValueError("invalid AGENT_CALL_GOVERNOR_MODE")
        if profile not in {"strict", "balanced", "quality-first"}:
            raise ValueError("invalid AGENT_CALL_GOVERNOR_PROFILE")
        if risk not in {"low", "medium", "high"}:
            raise ValueError("invalid AGENT_CALL_GOVERNOR_RISK")
        retention = _integer("AGENT_CALL_GOVERNOR_RETENTION_DAYS", 7, minimum=1, maximum=3650)
        stale = _integer("AGENT_CALL_GOVERNOR_STALE_SECONDS", 86400, minimum=0, maximum=31536000)
        db_path = Path(os.environ.get("AGENT_CALL_GOVERNOR_DB", plugin_data / "events.sqlite3"))
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        ledger = CallLedger(db_path, busy_timeout_ms=1000)
        runtime = GovernedRuntime(
            ledger, mode=mode, failure_policy="fail-open", source="codex-hook",
            default_profile=profile, default_risk=risk,
        )
        output = handle_codex_hook(
            payload, runtime, retention_days=retention, stale_reservation_seconds=stale
        )
        if output is not None:
            sys.stdout.write(json.dumps(output, ensure_ascii=False))
        return 0
    except BaseException as exc:
        print(f"agent-call-governor hook unavailable: {type(exc).__name__}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add `default_profile` and `default_risk` keyword arguments to `GovernedRuntime`; Codex proposal normalization uses them without changing explicit proposal values in application-owned wrappers. No failure path prints `str(exc)`, stdin, a filesystem path, or raw configuration value.

- [ ] **Step 4: Run all hook, privacy, and runtime tests**

Run:

```powershell
python -m unittest tests.test_codex_hook tests.test_plugin_dispatch tests.test_redaction tests.test_event_v2_runtime tests.test_governed_runtime -v
```

Expected: all tests PASS; the six canonical lifecycles are present; warning JSON contains only `systemMessage`; malformed input exits `0` with empty stdout.

- [ ] **Step 5: Commit the six-event hook lifecycle**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add hooks skills/agent-call-governor/scripts/agent_call_governor_runtime tests/fixtures/codex-hooks tests/test_codex_hook.py tests/test_plugin_dispatch.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Observe six Codex hook events safely"
```

---

### Task 8: Add Doctor, Session Inspection, Deletion, and Sanitized Export CLI

**Files:**
- Create: `tests/test_operational_cli.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/cli.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/models.py`
- Modify: `tests/test_runtime_cli.py`

**Interfaces:**
- Consumes: `SessionSummary`, ledger operations, event sanitizer, plugin root, and database path.
- Produces: `DoctorCheck`, `doctor`, `sessions`, `inspect`, `delete-session`, `export --format jsonl`, and one-release `export-jsonl` alias.

- [ ] **Step 1: Write failing parser and output tests**

Create `tests/test_operational_cli.py` with subprocess tests for these exact contracts:

```python
def test_sessions_json_lists_only_hashed_references(self) -> None:
    self.seed_session("sha256:" + "a" * 64)
    result = self.run_cli("sessions", "--db", str(self.db), "--json")
    self.assertEqual(result.returncode, 0, result.stderr)
    payload = json.loads(result.stdout)
    self.assertEqual(payload[0]["session_id"], "sha256:" + "a" * 64)
    self.assertEqual(payload[0]["call_count"], 1)

def test_inspect_is_sequence_ordered_and_sanitized(self) -> None:
    session = "sha256:" + "a" * 64
    self.seed_session(session)
    result = self.run_cli("inspect", session, "--db", str(self.db), "--json")
    self.assertEqual(result.returncode, 0, result.stderr)
    events = json.loads(result.stdout)
    self.assertEqual([event["event_type"] for event in events], [
        "call.proposed", "policy.decided", "call.started", "call.completed"
    ])
    self.assertTrue(all(event["raw_input_stored"] is False for event in events))

def test_delete_requires_yes_and_deletes_one_session(self) -> None:
    session = "sha256:" + "a" * 64
    self.seed_session(session)
    refused = self.run_cli("delete-session", session, "--db", str(self.db))
    self.assertEqual(refused.returncode, 2)
    deleted = self.run_cli("delete-session", session, "--yes", "--db", str(self.db))
    self.assertEqual(deleted.returncode, 0, deleted.stderr)
    self.assertIn("Deleted 4 events", deleted.stdout)

def test_export_alias_warns_and_new_command_re_redacts(self) -> None:
    self.seed_session("sha256:" + "a" * 64)
    output = self.root / "events.jsonl"
    result = self.run_cli("export", "--format", "jsonl", "--db", str(self.db),
                          "--output", str(output))
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertNotIn("private", output.read_text(encoding="utf-8"))
    alias = self.run_cli("export-jsonl", "--db", str(self.db), "--output", str(self.root / "alias.jsonl"))
    self.assertIn("deprecated", alias.stderr.lower())

def test_doctor_json_reports_required_checks(self) -> None:
    result = self.run_cli("doctor", "--db", str(self.db), "--plugin-root", str(ROOT), "--json")
    checks = {item["name"]: item for item in json.loads(result.stdout)}
    self.assertTrue({"python", "plugin", "hooks", "permissions", "sqlite", "privacy", "telemetry"} <= set(checks))
    self.assertNotEqual(checks["telemetry"]["status"], "fail")
```

- [ ] **Step 2: Run the operational CLI tests and verify parser failures**

Run: `python -m unittest tests.test_operational_cli -v`

Expected: FAIL with argparse `invalid choice` for `sessions`, `inspect`, `delete-session`, `doctor`, or `export`.

- [ ] **Step 3: Implement exact command objects, exit codes, and checks**

Add to `models.py`:

```python
@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str

    def __post_init__(self) -> None:
        if self.status not in {"pass", "warn", "fail"}:
            raise ValueError("doctor status must be pass, warn, or fail")
```

Create parser subcommands exactly as documented:

```text
doctor [--db PATH] [--plugin-root PATH] [--json]
sessions [--db PATH] [--json]
inspect SESSION_ID [--db PATH] [--json]
delete-session SESSION_ID --yes [--db PATH]
report [--db PATH] [--session SESSION_ID] [--json]
export --format jsonl --output PATH [--db PATH] [--session SESSION_ID]
export-jsonl --output PATH [--db PATH] [--session SESSION_ID]
codex-hook [existing compatibility flags]
```

Default DB path is `$PLUGIN_DATA/events.sqlite3` when `PLUGIN_DATA` exists, otherwise `~/.codex/agent-call-governor/events.sqlite3`. `sessions` serializes `SessionSummary` fields only. `inspect` and `report --session` treat the argument as a trace reference, re-sanitize output, and read no transcript; for a migrated v0.2 database, fall back to exact legacy `session_id` only when no `trace_id` rows match. `delete-session` makes `--yes` required in argparse and prints only the deleted count plus hashed session reference.

Implement `run_doctor(db_path, plugin_root) -> list[DoctorCheck]` with exact checks:

- Python is at least 3.10.
- `.codex-plugin/plugin.json`, skill, `hooks/hooks.json`, and dispatcher exist.
- Hook JSON has the six events and each command has `commandWindows`.
- Data directory/database permission state is owner-only on POSIX or `warn` with `Windows ACL inspection is best-effort` on Windows.
- Read `PRAGMA user_version` through a read-only SQLite connection before constructing `CallLedger`, report whether a v0.2 migration is pending, then verify the opened database is schema 2, WAL is active, busy timeout is readable, and `BEGIN IMMEDIATE` plus a temporary insert can be rolled back.
- Raw storage flags are false, retention default is 7, and no live JSONL mirror is active.
- Remote telemetry configuration is absent and status is `pass`.

Return exit `0` when checks contain only pass/warn, `1` when any check fails, and `2` for CLI usage or operational errors. Keep existing report calculations compatible with schema-v2 event types.

- [ ] **Step 4: Run CLI, ledger, and export privacy regressions**

Run:

```powershell
python -m unittest tests.test_operational_cli tests.test_runtime_cli tests.test_ledger_operations tests.test_redaction -v
$doctorDb = Join-Path ([IO.Path]::GetTempPath()) ("acg-doctor-" + [guid]::NewGuid().ToString() + ".sqlite3")
python -m agent_call_governor_runtime doctor --db $doctorDb --plugin-root . --json
@($doctorDb, "$doctorDb-wal", "$doctorDb-shm") | ForEach-Object { if (Test-Path -LiteralPath $_) { Remove-Item -LiteralPath $_ -Force } }
```

Expected: all unit tests PASS; doctor emits valid JSON and exits `0`; the temporary DB is removed after the check.

- [ ] **Step 5: Commit operational CLI commands**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add skills/agent-call-governor/scripts/agent_call_governor_runtime tests/test_operational_cli.py tests/test_runtime_cli.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Add local observability CLI operations"
```

---

### Task 9: Prove Multi-Process Hook Safety and Lock-Bounded Fail-Open

**Files:**
- Create: `tests/test_multiprocess_hooks.py`
- Modify: `skills/agent-call-governor/scripts/agent_call_governor_runtime/ledger.py`
- Modify: `hooks/dispatch.py`
- Modify if a schema evolution is proven necessary: `tests/test_schema_migration.py`

**Interfaces:**
- Consumes: real `hooks/dispatch.py` subprocesses, shared SQLite WAL database, 1,000 ms plugin busy timeout.
- Produces: process-safe duplicate collapse, atomic final-budget decision, lock-bounded exit, and no plugin JSONL file.

- [ ] **Step 1: Write real-subprocess contention tests**

Create `tests/test_multiprocess_hooks.py` with a helper that sets `PLUGIN_ROOT`, `PLUGIN_DATA`, and runs the dispatcher. Add these tests:

```python
def test_eight_processes_collapse_same_delivery(self) -> None:
    payload = self.fixture("pre_tool_use.json")
    results = self.dispatch_many([payload] * 8)
    self.assertTrue(all(result.returncode == 0 for result in results))
    events = CallLedger(self.db).events()
    self.assertEqual([event.event_type for event in events], [
        "call.proposed", "policy.decided", "call.started"
    ])

def test_two_processes_compete_for_one_policy_budget_slot(self) -> None:
    for index in range(5):
        started = self.fixture("pre_tool_use.json")
        completed = self.fixture("post_tool_use.json")
        started["tool_use_id"] = completed["tool_use_id"] = f"seed-{index}"
        started["tool_input"] = completed["tool_input"] = {"command": f"echo seed-{index}"}
        self.assertEqual(self.dispatch(started).returncode, 0)
        self.assertEqual(self.dispatch(completed).returncode, 0)
    first = self.fixture("pre_tool_use.json")
    second = self.fixture("pre_tool_use.json")
    first["tool_use_id"], first["tool_input"] = "call-a", {"command": "git status"}
    second["tool_use_id"], second["tool_input"] = "call-b", {"command": "git diff"}
    results = self.dispatch_many([first, second], mode="warn")
    self.assertTrue(all(result.returncode == 0 for result in results))
    decisions = [
        event for event in CallLedger(self.db).events() if event.event_type == "policy.decided"
    ][-2:]
    self.assertEqual(sum(event.decision == "allow" for event in decisions), 1)
    self.assertEqual(sum(event.decision == "would_block" for event in decisions), 1)

def test_exclusive_database_lock_returns_zero_within_three_seconds(self) -> None:
    locker = sqlite3.connect(self.db)
    locker.execute("BEGIN EXCLUSIVE")
    started = time.monotonic()
    result = self.dispatch(self.fixture("pre_tool_use.json"))
    elapsed = time.monotonic() - started
    locker.rollback()
    locker.close()
    self.assertEqual(result.returncode, 0)
    self.assertLess(elapsed, 3.0)
    self.assertEqual(result.stdout, "")
    self.assertNotIn("private", result.stderr)

def test_plugin_never_creates_live_jsonl(self) -> None:
    self.dispatch(self.fixture("session_start.json"))
    self.dispatch(self.fixture("pre_tool_use.json"))
    self.assertEqual(list(self.plugin_data.glob("*.jsonl")), [])
```

Run subprocesses concurrently with `ThreadPoolExecutor` only as a launcher; each worker is a separate Python process. Assert every stderr diagnostic contains only its fixed prefix and exception type.

The five completed seeds consume five of the balanced/medium direct-tool profile's six effective slots. Do not place an unsupported `budget_limit` field in the host payload and do not lower the quality floor for this test; the two concurrent proposals must compete for the one genuinely remaining policy slot.

- [ ] **Step 2: Run contention tests and capture the first concrete failure**

Run: `python -m unittest tests.test_multiprocess_hooks -v`

Expected: at least one test FAIL from duplicate rows, an overspent decision, a lock wait above three seconds, or unexpected JSONL creation.

- [ ] **Step 3: Harden only the failing transaction and dispatcher paths**

Keep every proposal lookup, history read, policy evaluation, and event reservation within the existing `BEGIN IMMEDIATE`. First rely on the transaction-safe Task 6 reservation and Task 7 append-if-new boundaries; do not add a redundant index when all real subprocess races already collapse correctly.

Only if the RED subprocess test proves a remaining event-type delivery race, add a unique index that prevents duplicate event-type delivery per call while still allowing the proposal/decision/start sequence:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_call_events_delivery
ON call_events(session_id, call_id, event_type, COALESCE(source_event, ''));
```

Catch `sqlite3.IntegrityError` inside the transaction, re-read the persisted decision/event, and return it only when the stored fingerprint matches. Different fingerprints for the same call ID continue to raise `DuplicateCallIdError`.

Adding that index changes the strictly admitted database shape. In that case, add schema-migration tests first, recognize only the exact prior canonical v2 table plus its two named indexes, create the third index transactionally, and then require the new exact three-index shape on reopen. Never broadly accept a missing-index or lookalike database and never make the index outside the verified migration boundary.

Ensure plugin construction always passes `busy_timeout_ms=1000`; do not retry past that bound in the dispatcher. Keep runtime-wrapper default at 30000 ms. Never supply `jsonl_path` from dispatcher. Do not add process-global file locks or sleeps.

- [ ] **Step 4: Run contention tests repeatedly and then the full ledger suite**

Run:

```powershell
1..5 | ForEach-Object { python -m unittest tests.test_multiprocess_hooks -v; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
python -m unittest tests.test_runtime_ledger tests.test_ledger_operations tests.test_event_v2_runtime tests.test_plugin_dispatch -v
```

Expected: five consecutive multiprocess passes; all ledger/dispatcher regressions PASS; each final-budget run has one `allow` and one `would_block` decision.

- [ ] **Step 5: Commit multiprocess hardening**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add tests/test_multiprocess_hooks.py skills/agent-call-governor/scripts/agent_call_governor_runtime/ledger.py hooks/dispatch.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Harden plugin hooks across processes"
```

---

### Task 10: Rewrite the Skill and Public Documentation Around the Plugin

**Files:**
- Create: `docs/architecture.md`
- Create: `docs/limitations.md`
- Create: `docs/security.md`
- Create: `docs/benchmark-methodology.md`
- Create: `CHANGELOG.md`
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Create: `evals/skill-baseline-v0.2.md`
- Modify: `skills/agent-call-governor/SKILL.md`
- Modify: `skills/agent-call-governor/agents/openai.yaml`
- Modify: `skills/agent-call-governor/references/codex-hooks.md`
- Modify: `skills/agent-call-governor/references/profiles.md`
- Modify: `README.md`
- Modify: `README.ko.md`
- Modify: `install.ps1`
- Modify: `install.sh`

**Interfaces:**
- Consumes: the working plugin/CLI contracts and measured v0.1-v0.2 evidence.
- Produces: fresh-agent-validated skill instructions, matched English/Korean onboarding, security and contribution policy, honest limitations, and update/disable/uninstall guidance.

- [ ] **Step 1: Use `superpowers:writing-skills` and record a RED baseline before editing the skill**

Start this task by loading `superpowers:writing-skills`. Extract the released v0.2 skill with `git show v0.2.0:agent-call-governor/SKILL.md` and give that unmodified snapshot to a fresh agent with this exact prompt:

```text
You have Agent Call Governor v0.2 instructions only. Explain how to install the root Codex plugin, where its six hook events store data, how to inspect and delete one session, whether Agent Call Governor v0.3's installed hook currently blocks a tool call, and separately whether the host Codex PreToolUse contract supports denial. Cite the repository paths or commands you relied on.
```

Record the verbatim prompt, agent answer, tag and commit SHA, run date, available agent/model identifier, and four-part failure matrix in `evals/skill-baseline-v0.2.md`. RED criteria are missing root-plugin install, missing any of the six events or `PLUGIN_DATA`, missing exact inspect/delete commands, or failure to distinguish host-supported denial from ACG v0.3's deliberate observe/warn-only implementation. Do not modify `SKILL.md` until this baseline is saved.

- [ ] **Step 2: Add exact documentation assertions before changing prose**

Extend `tests/test_plugin_package.py` with:

```python
def test_public_docs_cover_install_security_and_honest_limits(self) -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    korean = (ROOT / "README.ko.md").read_text(encoding="utf-8")
    for text in (english, korean):
        self.assertIn("v0.3.0", text)
        self.assertIn("SessionStart", text)
        self.assertIn("delete-session", text)
        self.assertIn("agent_call_governor_runtime-0.3.0-py3-none-any.whl", text)
        self.assertIn("observe", text.lower())
        self.assertIn("warn", text.lower())
        self.assertNotIn("complete agent-call firewall", text.lower())
    for path in (
        "docs/architecture.md", "docs/limitations.md", "docs/security.md",
        "docs/benchmark-methodology.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
    ):
        self.assertTrue((ROOT / path).is_file(), path)
```

Run: `python -m unittest tests.test_plugin_package.PluginPackageTests.test_public_docs_cover_install_security_and_honest_limits -v`

Expected: FAIL because the new documents and v0.3 command coverage do not exist.

- [ ] **Step 3: Write the skill, metadata, and matched public docs with exact claims**

Update `SKILL.md` frontmatter to keep exactly `name` and `description`; its workflow must route users through: identify objective and call kind, choose minimum sufficient calls, apply quality/risk floor, use v2 exact duplicate evidence, treat unknown progress neutrally, inspect local session data, and state Codex observe/warn limits. Keep `quality-first` as a profile, never a mode.

Use these top-level README sections in both languages and keep commands identical:

```text
What it is / 무엇인가요
Three-line summary / 3줄 요약
Install the Codex plugin / Codex 플러그인 설치
First local session / 첫 로컬 세션
Modes, profiles, and risk / 모드, 프로필, 위험도
Inspect, export, retain, and delete / 조회, 내보내기, 보존, 삭제
Supported integrations / 지원 통합
Security and privacy / 보안 및 개인정보
Evidence and limitations / 평가 근거와 한계
Update, disable, and uninstall / 업데이트, 비활성화, 제거
Contributing and roadmap / 기여 및 로드맵
```

Primary install instructions are:

```text
1. In Codex Desktop, open the Plugins directory or Settings -> Plugins and add/install `Kimuhwan/Agent-Call-Governor` at ref `main`. When the shell command is available, `codex plugin marketplace add Kimuhwan/Agent-Call-Governor --ref main` is the separate CLI equivalent.
2. Open `/hooks`, review the current hook content and exact hash, and explicitly trust that exact version. Installation or enablement does not imply hook trust; changed hook content must be reviewed and trusted again.
3. Start a new task only after the trusted hook is shown as active.
4. The plugin alone now records local events. To add the shell CLI, run `python -m pip install https://github.com/Kimuhwan/Agent-Call-Governor/releases/download/v0.3.0/agent_call_governor_runtime-0.3.0-py3-none-any.whl`.
5. For CLI access to plugin events, set `AGENT_CALL_GOVERNOR_DB` to an owner-only path before starting Codex and pass that same path with `--db`; without an override, plugin hooks keep their database under host-provided `PLUGIN_DATA`.
6. Run `agent-call-governor-runtime doctor --plugin-root PATH_TO_CHECKOUT --db PATH_TO_EVENTS` when validating a checkout.
```

State that app installation is the supported interactive path, hooks require trust, state stays under `PLUGIN_DATA` unless the user explicitly overrides the database, the companion wheel is required for global console commands, and skill-only installers are compatibility-only. Update those installers to copy `skills/agent-call-governor` and document Windows as `powershell -ExecutionPolicy Bypass -File .\install.ps1`. Document separate plugin disable/remove, wheel upgrade/uninstall, and database retention/deletion commands so removing one surface is not misrepresented as removing the others.

Write `docs/architecture.md` with data flow `Codex event -> dispatcher -> normalizer -> policy -> SQLite -> CLI/export`; `docs/limitations.md` with the host-supported `PreToolUse` denial shape separated from ACG v0.3's deliberate observe/warn-only implementation, no near-duplicate enforcement, no replay, nullable usage/cost, directional evidence, the conservative rule that a crashed Codex session lacking `session.stopped` is skipped by automatic retention until manually deleted, and the one-time v0.2-to-v0.3 duplicate epoch reset (legacy-v1 rows still count for budget/progress but are not exact v2 duplicate candidates); `docs/security.md` with threat model, redaction, exact-hash hook trust, permissions, seven-day retention, deletion caveat, no telemetry, and Windows ACL best effort; `docs/benchmark-methodology.md` ordered by task success, under-call rate, false-block rate, then call efficiency.

Link hook and installation claims directly to the official Codex sources `https://learn.chatgpt.com/docs/hooks.md` and `https://learn.chatgpt.com/docs/build-plugins.md`. Do not copy long passages; summarize the current contract and date the compatibility note `2026-07-15`.

Write `CHANGELOG.md` with released `0.1.0`, `0.2.0`, and `## [0.3.0] - Unreleased`; `CONTRIBUTING.md` with setup, TDD, full verification, privacy canaries, and PR checklist; root `SECURITY.md` with supported `0.3.x`, no public secret disclosure, and no fixed response SLA. Private GitHub vulnerability reporting is currently disabled, so do not claim it is available; state the missing private channel honestly unless the user separately authorizes and completes that repository-setting change.

- [ ] **Step 4: Forward-test the revised skill with a fresh agent and validate both languages**

Give a new agent the same prompt from Step 1 plus the revised skill. PASS requires all four answers: root plugin installation, all six events and `PLUGIN_DATA`, inspect/delete commands, and observe/warn-only hook semantics. Append the answer and pass matrix to `evals/skill-baseline-v0.2.md` under `## v0.3 forward test`.

Run:

```powershell
python -m unittest tests.test_plugin_package -v
python "C:\Users\Students\.codex\skills\.system\skill-creator\scripts\quick_validate.py" skills/agent-call-governor
```

Expected: documentation assertions PASS; official skill validation prints a success result and exits `0`.

- [ ] **Step 5: Commit public product documentation**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add README.md README.ko.md CHANGELOG.md CONTRIBUTING.md SECURITY.md docs evals/skill-baseline-v0.2.md skills/agent-call-governor install.ps1 install.sh tests/test_plugin_package.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Document the v0.3 Codex plugin"
```

---

### Task 11: Run and Publish the Ten-Case Instrumentation Pilot

**Files:**
- Create: `evals/instrumentation_tasks.json`
- Create: `evals/run_instrumentation_evals.py`
- Create: `evals/instrumentation-results-2026-07-15.md`
- Modify: `evals/README.md`
- Modify: `tests/test_fingerprint.py`
- Modify: `tests/test_runtime_cli.py`

**Interfaces:**
- Consumes: plugin dispatcher, schema-v2 ledger, fingerprint v2, hook fixtures, and lock/recovery controls.
- Produces: deterministic ten-case instrumentation report; this measures recording/governance accuracy, not model-answer quality.

- [ ] **Step 1: Define the exact ten cases and write the failing harness contract test**

Create `evals/instrumentation_tasks.json`:

```json
[
  {"name": "same-file-read-twice", "expected": "same_fingerprint", "test_id": "tests.test_fingerprint.FingerprintTests.test_exact_file_read_repeats_same_fingerprint"},
  {"name": "state-token-after-file-change", "expected": "new_fingerprint", "test_id": "tests.test_fingerprint.FingerprintTests.test_exact_patch_and_state_token_are_material"},
  {"name": "same-bash-command-twice", "expected": "same_fingerprint", "test_id": "tests.test_fingerprint.FingerprintTests.test_exact_bash_repeat_same_fingerprint"},
  {"name": "bash-direction-change", "expected": "new_fingerprint", "test_id": "tests.test_fingerprint.FingerprintTests.test_bash_direction_and_cwd_are_material"},
  {"name": "mcp-object-key-order", "expected": "same_fingerprint", "test_id": "tests.test_fingerprint.FingerprintTests.test_object_key_order_is_equivalent"},
  {"name": "failed-call-changed-strategy-retry", "expected": "retry_allowed", "test_id": "tests.test_governor.GovernorTests.test_strict_high_risk_allows_one_changed_strategy_retry"},
  {"name": "parallel-final-budget-slot", "expected": "one_allow_one_would_block", "test_id": "tests.test_multiprocess_hooks.MultiprocessHookTests.test_two_processes_compete_for_one_policy_budget_slot"},
  {"name": "locked-database-dispatch", "expected": "zero_exit_under_three_seconds", "test_id": "tests.test_multiprocess_hooks.MultiprocessHookTests.test_exclusive_database_lock_returns_zero_within_three_seconds"},
  {"name": "stale-reservation-recovery", "expected": "append_cancelled", "test_id": "tests.test_ledger_operations.LedgerOperationTests.test_stale_recovery_appends_cancellation_without_mutating_start"},
  {"name": "process-restart-persistence", "expected": "history_preserved", "test_id": "tests.test_runtime_ledger.RuntimeLedgerTests.test_reopening_sqlite_preserves_events"}
]
```

Add these two exact cases to `FingerprintTests` so the pilot names correspond to real behavior rather than aliases:

```python
def test_exact_file_read_repeats_same_fingerprint(self) -> None:
    first = self.build("Read", {"path": "src/app.py"}, cwd="C:/repo")
    second = self.build("Read", {"path": "src/app.py"}, cwd="C:/repo")
    self.assertEqual(first.digest, second.digest)

def test_exact_bash_repeat_same_fingerprint(self) -> None:
    first = self.build("Bash", {"command": "git status --short"}, cwd="C:/repo")
    second = self.build("Bash", {"command": "git status --short"}, cwd="C:/repo")
    self.assertEqual(first.digest, second.digest)
```

Add to `tests/test_runtime_cli.py`:

```python
def test_instrumentation_pilot_has_ten_unique_cases_and_passes(self) -> None:
    from evals.run_instrumentation_evals import run

    cases = json.loads((ROOT / "evals" / "instrumentation_tasks.json").read_text(encoding="utf-8"))
    self.assertEqual(len(cases), 10)
    self.assertEqual(len({case["name"] for case in cases}), 10)
    result = run()
    self.assertEqual(result["failures"], [])
    self.assertEqual(result["passed"], 10)
    self.assertEqual(result["total"], 10)
```

- [ ] **Step 2: Run the contract and verify the missing-runner failure**

Run: `python -m unittest tests.test_runtime_cli.RuntimeCLITests.test_instrumentation_pilot_has_ten_unique_cases_and_passes -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'evals.run_instrumentation_evals'`.

- [ ] **Step 3: Build a deterministic runner with one isolated database per case**

Implement `run_instrumentation_evals.py` as a deterministic selector over the ten already-isolated unit/integration checks:

```python
from __future__ import annotations

import io
import json
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run() -> dict[str, object]:
    cases = json.loads((ROOT / "evals" / "instrumentation_tasks.json").read_text(encoding="utf-8"))
    results: list[dict[str, object]] = []
    loader = unittest.defaultTestLoader
    for case in cases:
        suite = loader.loadTestsFromName(case["test_id"])
        stream = io.StringIO()
        started = time.perf_counter_ns()
        result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
        elapsed_ms = round((time.perf_counter_ns() - started) / 1_000_000, 3)
        passed = result.wasSuccessful() and result.testsRun == 1
        detail = (
            f"observed={case['expected']}; test={case['test_id']}; elapsed_ms={elapsed_ms}"
            if passed
            else stream.getvalue().strip()
        )
        results.append({
            "name": case["name"], "expected": case["expected"],
            "passed": passed, "detail": detail,
        })
    failures = [item["name"] for item in results if not item["passed"]]
    return {"total": len(results), "passed": len(results) - len(failures),
            "failures": failures, "results": results}


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(1 if result["failures"] else 0)
```

Each selected test already creates its own temporary database or pure in-memory inputs. The lock check launches the real dispatcher and measures monotonic time; the parallel-budget check launches two real processes; the restart check closes all objects and constructs a new ledger before reading history. The harness must not call a language model and must not label these results as response-quality evidence.

- [ ] **Step 4: Run the pilot, generate the dated result, and update evaluation docs**

Run:

```powershell
python evals/run_instrumentation_evals.py | Tee-Object -FilePath evals/instrumentation-results-2026-07-15.json
python -m unittest tests.test_runtime_cli -v
```

Expected: JSON reports `total: 10`, `passed: 10`, and `failures: []`; tests PASS.

Convert the measured JSON to `evals/instrumentation-results-2026-07-15.md` with sections `Scope`, `Environment`, `Results`, `Failures`, and `Interpretation`. Include all ten measured detail strings, state that failures are empty only if the run says so, and explicitly say: `This pilot tests instrumentation and deterministic governance accuracy; it does not measure model response quality.` Remove the transient `.json` after the Markdown result captures it. Update `evals/README.md` to link the pilot after the existing policy and matched A/B evidence.

- [ ] **Step 5: Commit the reproducible pilot and measured result**

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add evals/instrumentation_tasks.json evals/run_instrumentation_evals.py evals/instrumentation-results-2026-07-15.md evals/README.md tests/test_fingerprint.py tests/test_runtime_cli.py
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Publish v0.3 instrumentation pilot"
```

---

### Task 12: Validate, Package, Publish, and Release v0.3.0

**Files:**
- Create: `scripts/check_versions.py`
- Create: `scripts/build_plugin_archive.py`
- Create: `scripts/write_checksums.py`
- Create: `tests/test_release_package.py`
- Create: `docs/releases/v0.3.0.md`
- Modify: `.github/workflows/validate.yml`
- Modify: `CHANGELOG.md`
- Modify: `.codex-plugin/plugin.json`

**Interfaces:**
- Consumes: completed plugin, skill, runtime, docs, schemas, tests, and evaluation evidence.
- Produces: reproducible wheel, source distribution, plugin archive, green CI, GitHub PR, merged `main`, immutable `v0.3.0` tag, and release assets.

- [ ] **Step 1: Write failing version/archive contract tests**

Create `tests/test_release_package.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from agent_call_governor_runtime import __version__
from scripts.build_plugin_archive import build_archive
from scripts.check_versions import collect_versions
from scripts.write_checksums import write_checksums


ROOT = Path(__file__).resolve().parents[1]


class ReleasePackageTests(unittest.TestCase):
    def test_all_product_versions_are_exactly_equal(self) -> None:
        versions = collect_versions(ROOT)
        self.assertEqual(versions, {
            "package": "0.3.0", "plugin": "0.3.0", "changelog": "0.3.0"
        })
        self.assertEqual(__version__, "0.3.0")

    def test_plugin_archive_contains_runtime_and_excludes_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "agent-call-governor-plugin-0.3.0.zip"
            second_output = Path(directory) / "agent-call-governor-plugin-0.3.0-second.zip"
            build_archive(ROOT, output)
            build_archive(ROOT, second_output)
            self.assertEqual(output.read_bytes(), second_output.read_bytes())
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
            self.assertIn("agent-call-governor/.codex-plugin/plugin.json", names)
            self.assertIn("agent-call-governor/hooks/dispatch.py", names)
            self.assertIn("agent-call-governor/skills/agent-call-governor/SKILL.md", names)
            self.assertFalse(any(".git/" in name or "__pycache__" in name for name in names))
            self.assertFalse(any(name.endswith((".sqlite3", ".db", ".jsonl")) for name in names))

    def test_checksum_asset_is_stable_and_does_not_hash_itself(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.whl"
            second = root / "b.tar.gz"
            output = root / "SHA256SUMS.txt"
            first.write_bytes(b"wheel")
            second.write_bytes(b"source")
            write_checksums([second, first], output)
            lines = output.read_text(encoding="utf-8").splitlines()
            self.assertEqual([line.split("  ", 1)[1] for line in lines], ["a.whl", "b.tar.gz"])
            self.assertNotIn("SHA256SUMS.txt", output.read_text(encoding="utf-8"))

    def test_release_notes_state_replay_timing_and_evidence_limit(self) -> None:
        notes = (ROOT / "docs" / "releases" / "v0.3.0.md").read_text(encoding="utf-8")
        self.assertIn("Policy replay is deferred to v0.4 after schema-v2 logs exist.", notes)
        self.assertIn(
            "Current deterministic, instrumentation, and small matched A/B results are directional evidence, not production benchmarks.",
            notes,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the release test and verify missing-script failure**

Run: `python -m unittest tests.test_release_package -v`

Expected: FAIL importing `scripts.build_plugin_archive` or `scripts.check_versions`.

- [ ] **Step 3: Add deterministic validators, archive builder, CI gates, and release notes**

Implement `scripts/check_versions.py`:

```python
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
CHANGELOG_PATTERN = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def collect_versions(root: Path) -> dict[str, str]:
    package_text = (
        root / "skills" / "agent-call-governor" / "scripts"
        / "agent_call_governor_runtime" / "__init__.py"
    ).read_text(encoding="utf-8")
    package_match = VERSION_PATTERN.search(package_text)
    if package_match is None:
        raise ValueError("package __version__ is missing")
    plugin = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    changelog_match = CHANGELOG_PATTERN.search((root / "CHANGELOG.md").read_text(encoding="utf-8"))
    if changelog_match is None:
        raise ValueError("released changelog version is missing")
    return {
        "package": package_match.group(1),
        "plugin": str(plugin["version"]),
        "changelog": changelog_match.group(1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    versions = collect_versions(args.root.resolve())
    if set(versions.values()) != {"0.3.0"}:
        raise SystemExit(f"version mismatch: {versions}")
    print(json.dumps(versions, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Implement `scripts/build_plugin_archive.py` so the ZIP is deterministic and private state cannot enter it. The candidate set must come from `git ls-files`, filtered through the explicit release allowlist; never recursively enumerate the worktree. Reject symlinks, Windows reparse points, non-regular files, and any resolved path outside the repository root:

```python
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath


INCLUDE = (
    ".codex-plugin", ".agents", "hooks", "skills", "schemas", "docs",
    "README.md", "README.ko.md", "LICENSE", "CHANGELOG.md",
    "CONTRIBUTING.md", "SECURITY.md", "install.ps1", "install.sh",
)
EXCLUDED_PARTS = frozenset({
    ".git", ".github", "__pycache__", ".pytest_cache", "dist", "build", "superpowers",
})
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".sqlite3", ".db", ".jsonl", ".env")


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _allowed(relative: Path) -> bool:
    return any(relative == Path(name) or Path(name) in relative.parents for name in INCLUDE)


def _included_files(root: Path) -> list[Path]:
    files: list[Path] = []
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        pure = PurePosixPath(raw.decode("utf-8"))
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError("unsafe tracked path")
        relative = Path(*pure.parts)
        if not _allowed(relative):
            continue
        if any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in relative.parts):
            continue
        if relative.name.endswith(EXCLUDED_SUFFIXES):
            continue
        candidate = root / relative
        current = root
        for part in relative.parts:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise ValueError("release input cannot be a link or reparse point")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("release input must be a regular file")
        resolved = candidate.resolve(strict=True)
        if os.path.commonpath((str(root), str(resolved))) != str(root):
            raise ValueError("release input escapes repository root")
        files.append(candidate)
    return sorted(set(files), key=lambda item: item.relative_to(root).as_posix())


def build_archive(root: Path, output: Path) -> None:
    root = root.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in _included_files(root):
            name = "agent-call-governor/" + path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if path.suffix in {".sh", ".py"} else 0o644) << 16
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_archive(args.root, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Implement `scripts/write_checksums.py`; its output is a release asset and is not committed into artifacts that it hashes:

```python
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def write_checksums(files: list[Path], output: Path) -> None:
    lines = []
    for path in sorted(files, key=lambda item: item.name):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    write_checksums(args.files, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Extend `.github/workflows/validate.yml` so Linux and Windows Python 3.10/3.12 run unit tests and three eval scripts; the package job runs:

```yaml
- name: Verify product versions
  run: python scripts/check_versions.py
- name: Validate plugin package contract
  run: python -m unittest tests.test_plugin_package tests.test_release_package -v
- name: Validate portable skill metadata contract
  run: python -m unittest tests.test_plugin_package.PluginPackageTests.test_moved_skill_and_dispatcher_exist -v
- name: Build Python distributions
  run: python -m build
- name: Build plugin archive
  run: python scripts/build_plugin_archive.py --output dist/agent-call-governor-plugin-0.3.0.zip
```

Do not make CI depend on a Codex installation: the checked-in package/skill tests are mandatory everywhere; the official local validator is mandatory in the release checklist below. Add artifact upload for wheel, sdist, and plugin ZIP.

Write `docs/releases/v0.3.0.md` with `Highlights`, `Compatibility`, `Privacy`, `Measured validation`, `Known limitations`, `Install/update`, and `Checksums`. The checksums section says that exact SHA-256 values ship in the attached `SHA256SUMS.txt`, avoiding a self-referential committed artifact hash. Claims must say local-first observe/warn plugin, not firewall; include the actual ten-case result and existing directional A/B caveat only after verification. Compatibility must state that migrated legacy-v1 rows keep budget/progress accounting but start a new exact-duplicate epoch under fingerprint v2. The limitations section must contain the exact statements `Policy replay is deferred to v0.4 after schema-v2 logs exist.` and `Current deterministic, instrumentation, and small matched A/B results are directional evidence, not production benchmarks.`

- [ ] **Step 4: Run the complete local release gate from a clean worktree**

Run:

```powershell
python -m pip install -e ".[dev]"
python scripts/check_versions.py
python -m unittest discover -s tests -v
python evals/run_evals.py
python evals/run_runtime_evals.py
python evals/run_instrumentation_evals.py
python "C:\Users\Students\.codex\skills\.system\skill-creator\scripts\quick_validate.py" skills/agent-call-governor
python "C:\Users\Students\.codex\skills\.system\plugin-creator\scripts\validate_plugin.py" .
python -m build
python scripts/build_plugin_archive.py --output dist/agent-call-governor-plugin-0.3.0.zip
$releaseSmokeDb = Join-Path ([IO.Path]::GetTempPath()) ("acg-release-" + [guid]::NewGuid().ToString() + ".sqlite3")
python -m agent_call_governor_runtime doctor --plugin-root . --db $releaseSmokeDb --json
@($releaseSmokeDb, "$releaseSmokeDb-wal", "$releaseSmokeDb-shm") | ForEach-Object { if (Test-Path -LiteralPath $_) { Remove-Item -LiteralPath $_ -Force } }
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" diff --check
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" status --short
```

Expected: every command exits `0`; all unit tests pass; all three evals report zero failures; both official validators pass; wheel, sdist, and plugin ZIP exist; doctor has no fail check; `git diff --check` is silent. The only untracked/modified release outputs before commit may be `docs/releases/v0.3.0.md` and the intended changelog edit; `dist/` remains ignored.

Change `CHANGELOG.md` heading from `## [0.3.0] - Unreleased` to `## [0.3.0] - 2026-07-15`, then commit:

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" add .github scripts tests/test_release_package.py docs/releases/v0.3.0.md CHANGELOG.md
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" commit -m "Prepare Agent Call Governor v0.3.0"
```

- [ ] **Step 5: Push, review CI, merge, pass the pre-tag install smoke, then publish immutable assets**

Push the feature branch and open the PR:

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" push -u origin agent/v0.3-observability-foundation
gh pr create --base main --head agent/v0.3-observability-foundation --title "Release v0.3 local observability foundation" --body-file docs/releases/v0.3.0.md
gh pr checks --watch
```

Expected: all required GitHub checks pass. Review the PR diff for raw privacy canaries, unsupported enforcement claims, version drift, and unintended generated files. Merge only after that review:

```powershell
gh pr merge --squash
$mainWorktree = "C:\Users\Students\Documents\AgentCall"
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" -C $mainWorktree pull --ff-only origin main
$releaseCommit = & "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" -C $mainWorktree rev-parse HEAD
Set-Location -LiteralPath $mainWorktree
```

Do not create the tag yet. Build the final artifacts from the merged commit into a unique external directory, generate the standalone checksum asset, and install the exact wheel into a clean virtual environment:

```powershell
$releaseRoot = Join-Path ([IO.Path]::GetTempPath()) ("agent-call-governor-v0.3.0-" + [guid]::NewGuid().ToString())
$releaseDist = Join-Path $releaseRoot "dist"
$releaseVenv = Join-Path $releaseRoot "venv"
$smokeDb = Join-Path $releaseRoot "plugin-data\events.sqlite3"
if (Test-Path -LiteralPath $releaseRoot) { throw "Refusing to reuse release root: $releaseRoot" }
New-Item -ItemType Directory -Path $releaseDist | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $smokeDb) | Out-Null
python -m build --outdir $releaseDist
python scripts/build_plugin_archive.py --output "$releaseDist\agent-call-governor-plugin-0.3.0.zip"
python scripts/write_checksums.py --output "$releaseDist\SHA256SUMS.txt" "$releaseDist\agent_call_governor_runtime-0.3.0-py3-none-any.whl" "$releaseDist\agent_call_governor_runtime-0.3.0.tar.gz" "$releaseDist\agent-call-governor-plugin-0.3.0.zip"
python -m venv $releaseVenv
$venvPython = Join-Path $releaseVenv "Scripts\python.exe"
$runtimeCli = Join-Path $releaseVenv "Scripts\agent-call-governor-runtime.exe"
& $venvPython -m pip install --disable-pip-version-check "$releaseDist\agent_call_governor_runtime-0.3.0-py3-none-any.whl"
& $runtimeCli doctor --plugin-root . --db $smokeDb --json
```

Expected: the clean wheel install succeeds and `doctor` has no fail check. Before tagging, perform one visible Codex Desktop smoke against `main` while it still resolves to `$releaseCommit`, using the same controlled database. Fully quit any already-running Codex app first so it cannot retain an old environment. In the release PowerShell, set the database override, start a fresh Codex process that inherits it, add this repository at `main` through the Codex Desktop Plugins directory or Settings -> Plugins, and install Agent Call Governor. Then pause for the user to open `/hooks`, review the current content and exact hash, and explicitly trust that exact version. This is an interactive trust boundary: installing/enabling does not imply trust, and no agent may approve, bypass, or infer it. Any hook content change invalidates the smoke and requires re-trust and a full rerun.

```powershell
$env:AGENT_CALL_GOVERNOR_DB = $smokeDb
$codexPackage = Get-AppxPackage OpenAI.Codex
if ($null -eq $codexPackage) { throw "OpenAI Codex Desktop package is not installed" }
$codexApp = Join-Path $codexPackage.InstallLocation "app\Codex.exe"
Start-Process -FilePath $codexApp
```

Inside the fresh app, add `Kimuhwan/Agent-Call-Governor` at ref `main` using the plugin marketplace UI. When the shell plugin command is available, the equivalent command is `codex plugin marketplace add Kimuhwan/Agent-Call-Governor --ref main`. Install the plugin and start a new task. Submit a prompt containing the deliberately non-secret canaries `ACG_RELEASE_RAW_PROMPT_9D2F` and `sk-test-acg-release-not-a-secret`, and ask the task to perform at least one direct tool call and one subagent call before stopping.

Back in the clean wheel environment, inspect the exact database written by the plugin:

```powershell
$doctor = & $runtimeCli doctor --plugin-root . --db $smokeDb --json | ConvertFrom-Json
if (@($doctor | Where-Object status -eq "fail").Count -ne 0) { throw "Pre-tag doctor failed" }
$sessions = @(& $runtimeCli sessions --db $smokeDb --json | ConvertFrom-Json)
if ($sessions.Count -lt 1) { throw "Pre-tag smoke recorded no session" }
$sessionRef = $sessions[0].session_id
$events = @(& $runtimeCli inspect $sessionRef --db $smokeDb --json | ConvertFrom-Json)
$requiredTypes = @("session.started", "call.proposed", "policy.decided", "call.started", "call.completed", "session.stopped")
$missingTypes = @($requiredTypes | Where-Object { $_ -notin @($events.event_type) })
if ($missingTypes.Count -ne 0) { throw "Missing canonical events: $($missingTypes -join ', ')" }
$eventJson = $events | ConvertTo-Json -Depth 20 -Compress
$stateBytes = @($smokeDb, "$smokeDb-wal", "$smokeDb-shm") |
    Where-Object { Test-Path -LiteralPath $_ } |
    ForEach-Object { [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($_)) }
foreach ($canary in @("ACG_RELEASE_RAW_PROMPT_9D2F", "sk-test-acg-release-not-a-secret")) {
    if ($eventJson.Contains($canary) -or ($stateBytes -join "").Contains($canary)) {
        throw "Privacy canary leaked into persisted or inspected state"
    }
}
$proposalSources = @($events | Where-Object event_type -eq "call.proposed" | ForEach-Object source_event)
if ("PreToolUse" -notin $proposalSources -or "SubagentStart" -notin $proposalSources) {
    throw "Smoke must include both direct_tool and subagent calls"
}
```

Expected: the plugin records a complete session lifecycle, both proposal sources (`PreToolUse` and `SubagentStart`), and no raw canary in SQLite, WAL, SHM, or CLI output. Record the commit SHA, clean-wheel version, event-type list, proposal sources, and privacy result in the PR final comment. Treat any failure as a release blocker: fix it on a branch, rerun CI and this entire pre-tag smoke, and do not move or overwrite a release tag.

After the smoke passes, prove `origin/main` still points to the tested commit, create the immutable tag on that exact commit, then publish the exact pre-smoked artifacts:

```powershell
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" fetch origin main
$originMain = & "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" rev-parse origin/main
if ($originMain -ne $releaseCommit) { throw "origin/main changed after smoke; rebuild and rerun the pre-tag gate" }
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" tag -a v0.3.0 $releaseCommit -m "Agent Call Governor v0.3.0"
& "C:\Users\Students\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe" push origin v0.3.0
gh repo edit Kimuhwan/Agent-Call-Governor --description "Local-first, quality-preserving call governance and observability plugin for Codex"
gh release create v0.3.0 "$releaseDist\agent_call_governor_runtime-0.3.0-py3-none-any.whl" "$releaseDist\agent_call_governor_runtime-0.3.0.tar.gz" "$releaseDist\agent-call-governor-plugin-0.3.0.zip" "$releaseDist\SHA256SUMS.txt" --title "Agent Call Governor v0.3.0" --notes-file docs/releases/v0.3.0.md
gh release view v0.3.0
```

Expected: release page shows the exact pre-smoked wheel, source distribution, plugin ZIP, and `SHA256SUMS.txt`; the repository description uses the approved wording; the annotated tag resolves to `$releaseCommit`; and `v0.2.0` remains unchanged. No code or artifact rebuild occurs after tagging.
