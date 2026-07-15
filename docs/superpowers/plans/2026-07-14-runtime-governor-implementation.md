# Runtime Governor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authoritative runtime call recording and enforceable application-owned pre-call/post-call governance while preserving the existing Codex skill and policy CLI.

**Architecture:** Keep one standard-library Python policy core inside the installed skill, add a SQLite-first event ledger with an optional compatibility JSONL mirror, and expose synchronous/asynchronous wrappers. The bundled Codex adapter provides observation and warning and does not emit the host's supported `PreToolUse` denial shape; the optional OpenAI Agents SDK adapter adds workflow hooks and supported function-tool guardrail integration.

**Tech Stack:** Python 3.10+, `unittest`, SQLite, JSONL, optional `openai-agents>=0.18.2,<0.19`, GitHub Actions.

## Global Constraints

- Keep `balanced` as the default profile and `observe` as the default runtime mode.
- Preserve the existing `governor.py evaluate|fingerprint` CLI and exit codes `0`, `1`, and `2`.
- Keep the core dependency-free; OpenAI Agents SDK support must remain optional and import-safe.
- Use SQLite as the authoritative ledger and treat JSONL as an optional human-readable mirror.
- Do not persist raw prompts, material inputs, tool arguments, or results by default.
- Support Python 3.10 and newer on Windows, macOS, and Linux.
- Codex hooks may observe or warn only; do not claim they provide enforcement on unsupported hook contracts.
- Label replay evaluations and fresh-agent checks accurately; do not call them production benchmarks.

---

### Task 1: Package the existing policy as one reusable core

**Files:**
- Create: `pyproject.toml`
- Create: `agent-call-governor/scripts/agent_call_governor_runtime/__init__.py`
- Create: `agent-call-governor/scripts/agent_call_governor_runtime/policy.py`
- Modify: `agent-call-governor/scripts/governor.py`
- Modify: `tests/test_governor.py`

**Interfaces:**
- Produces: `evaluate(document: dict[str, Any]) -> dict[str, Any]`
- Produces: `fingerprint(proposal: dict[str, Any]) -> str`
- Preserves: `python agent-call-governor/scripts/governor.py evaluate <file>`

- [ ] **Step 1: Add a failing compatibility/import test**

```python
def test_packaged_policy_matches_legacy_module(self):
    from agent_call_governor_runtime import evaluate, fingerprint
    self.assertIs(evaluate, governor.evaluate)
    self.assertEqual(fingerprint(self.base_proposal()), governor.fingerprint(self.base_proposal()))
```

- [ ] **Step 2: Run the focused test and confirm the package import fails**

Run: `python -m unittest tests.test_governor.GovernorTests.test_packaged_policy_matches_legacy_module -v`

Expected: `ModuleNotFoundError: No module named 'agent_call_governor_runtime'`.

- [ ] **Step 3: Move policy code into the package and keep the CLI wrapper**

```python
from agent_call_governor_runtime.policy import (
    BUDGET_KINDS,
    MANDATORY_REASONS,
    PROFILE_LIMITS,
    PROFILE_NAMES,
    PROGRESS_VALUES,
    RISK_VALUES,
    evaluate,
    fingerprint,
)
```

Configure setuptools with package directory `agent-call-governor/scripts` and console scripts `agent-call-governor` and `agent-call-governor-runtime`.

- [ ] **Step 4: Run compatibility and full policy checks**

Run: `python -m unittest discover -s tests -v`

Expected: all existing tests plus the compatibility test pass.

- [ ] **Step 5: Commit the policy package refactor**

```text
Package the governor policy core
```

### Task 2: Add validated event models and the authoritative ledger

**Files:**
- Create: `agent-call-governor/scripts/agent_call_governor_runtime/models.py`
- Create: `agent-call-governor/scripts/agent_call_governor_runtime/ledger.py`
- Create: `tests/test_runtime_ledger.py`

**Interfaces:**
- Produces: `CallProposal`, `CallEvent`, `CallHandle`, `RuntimeDecision`
- Produces: `CallLedger.append(event)`, `CallLedger.history(session_id)`, `CallLedger.events(session_id)`

- [ ] **Step 1: Add failing ledger tests**

```python
def test_started_call_counts_once_and_blocked_call_does_not(self):
    ledger.append(event("blocked", call_id="blocked"))
    ledger.append(event("started", call_id="real"))
    ledger.append(event("completed", call_id="real", progress="sufficient"))
    self.assertEqual(
        ledger.history("session-1"),
        [{"fingerprint": "fp-real", "progress": "sufficient", "budget_kind": "agent"}],
    )

def test_jsonl_mirror_omits_raw_material_inputs(self):
    ledger.append(event("started", metadata={"safe": "value"}))
    self.assertNotIn("secret prompt", jsonl_path.read_text(encoding="utf-8"))
```

Cover session isolation, duplicate call IDs, latest-phase selection, SQLite reopen, and concurrent thread appends.

- [ ] **Step 2: Run the ledger tests and confirm imports fail**

Run: `python -m unittest tests.test_runtime_ledger -v`

Expected: missing `models` or `ledger` module.

- [ ] **Step 3: Implement dataclasses and SQLite-first storage**

```python
class CallLedger:
    def __init__(self, sqlite_path: Path, jsonl_path: Path | None = None) -> None: ...
    def append(self, event: CallEvent) -> None: ...
    def history(self, session_id: str) -> list[dict[str, str]]: ...
    def events(self, session_id: str | None = None) -> list[CallEvent]: ...
```

Create schema version `1`, parameterized SQL, explicit transactions, and indexes on session/call and session/fingerprint.

- [ ] **Step 4: Run ledger and policy tests**

Run: `python -m unittest tests.test_runtime_ledger tests.test_governor -v`

Expected: all tests pass without warnings.

- [ ] **Step 5: Commit the ledger**

```text
Add authoritative runtime call ledger
```

### Task 3: Implement runtime modes and sync/async wrappers

**Files:**
- Create: `agent-call-governor/scripts/agent_call_governor_runtime/runtime.py`
- Create: `tests/test_governed_runtime.py`
- Modify: `agent-call-governor/scripts/agent_call_governor_runtime/__init__.py`

**Interfaces:**
- Produces: `GovernedRuntime.begin`, `complete`, `fail`, `run`, and `run_async`
- Produces: `GovernanceBlocked`

- [ ] **Step 1: Add failing mode and lifecycle tests**

```python
def test_enforce_blocks_before_callable_runs(self):
    called = False
    with self.assertRaises(GovernanceBlocked):
        runtime.run(duplicate_proposal, lambda: self.fail("must not execute"))
    self.assertFalse(called)

def test_observe_executes_would_block_call_and_records_decision(self):
    result = runtime.run(duplicate_proposal, lambda: "ok")
    self.assertEqual(result, "ok")
    self.assertFalse(ledger.events(session_id)[-2].policy_allowed)
```

Cover `warn`, `fail-open`, `fail-closed`, exception re-raise, duration, progress, separate budget kinds, and `run_async`.

- [ ] **Step 2: Run the runtime tests and verify the missing implementation failure**

Run: `python -m unittest tests.test_governed_runtime -v`

Expected: missing `runtime` module or symbols.

- [ ] **Step 3: Implement decision and lifecycle orchestration**

```python
class GovernedRuntime:
    def run(self, proposal: CallProposal, func: Callable[..., T], *args: Any,
            progress: str = "material_progress", **kwargs: Any) -> T: ...

    async def run_async(self, proposal: CallProposal, func: Callable[..., Awaitable[T]],
                        *args: Any, progress: str = "material_progress", **kwargs: Any) -> T: ...
```

Only internal policy or ledger exceptions use the failure policy. Policy denials in `enforce` always raise before the callable.

- [ ] **Step 4: Run all runtime tests and existing suites**

Run: `python -m unittest discover -s tests -v && python evals/run_evals.py`

Expected: all tests and all deterministic cases pass.

- [ ] **Step 5: Commit the wrapper**

```text
Enforce governed runtime calls
```

### Task 4: Add Codex hook observation and warning integration

**Files:**
- Create: `agent-call-governor/scripts/agent_call_governor_runtime/codex_hook.py`
- Create: `agent-call-governor/scripts/codex_hook.py`
- Create: `agent-call-governor/references/codex-hooks.md`
- Create: `examples/codex-hooks.json`
- Create: `tests/fixtures/codex-hooks/*.json`
- Create: `tests/test_codex_hook.py`

**Interfaces:**
- Produces: `handle_codex_hook(payload, runtime) -> dict[str, Any] | None`
- Produces: stdin/stdout command entrypoint compatible with official Codex hooks

- [ ] **Step 1: Add failing hook fixture tests**

```python
def test_pre_tool_use_warns_without_returning_unsupported_stop_fields(self):
    output = handle_codex_hook(load_fixture("pre_tool_use.json"), runtime)
    self.assertIn("systemMessage", output)
    self.assertNotIn("continue", output)

def test_subagent_stop_closes_the_started_call(self):
    handle_codex_hook(load_fixture("subagent_start.json"), runtime)
    handle_codex_hook(load_fixture("subagent_stop.json"), runtime)
    self.assertEqual(ledger.history("session-1")[0]["progress"], "material_progress")
```

- [ ] **Step 2: Run fixtures and confirm the adapter is missing**

Run: `python -m unittest tests.test_codex_hook -v`

Expected: missing `codex_hook` module.

- [ ] **Step 3: Implement tolerant event mapping and CLI**

Map official events to proposal/start/end records, derive stable call IDs from supplied IDs, and sanitize payloads. Reject `enforce` configuration with a message explaining that this adapter is deliberately observe/warn-only and does not emit the host's supported `PreToolUse` denial shape.

- [ ] **Step 4: Run hook tests and command-line smoke fixtures**

Run: `python -m unittest tests.test_codex_hook -v`

Run: `Get-Content tests/fixtures/codex-hooks/pre_tool_use.json | python agent-call-governor/scripts/codex_hook.py --mode warn --db .tmp/hook-test.sqlite3`

Expected: valid JSON or empty success output, exit code `0`, and one recorded event.

- [ ] **Step 5: Commit the Codex adapter**

```text
Record Codex lifecycle hooks
```

### Task 5: Add optional OpenAI Agents SDK integration

**Files:**
- Create: `agent-call-governor/scripts/agent_call_governor_runtime/agents_sdk.py`
- Create: `agent-call-governor/references/openai-agents-sdk.md`
- Create: `tests/test_agents_sdk_adapter.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `build_run_hooks(runtime, proposal_factory=None)`
- Produces: `GovernedRunner`
- Produces: `build_function_tool_guardrail(runtime, proposal_factory)`

- [ ] **Step 1: Add failing optional-import and fake-SDK tests**

```python
def test_core_import_does_not_require_openai_agents(self):
    import agent_call_governor_runtime
    self.assertTrue(hasattr(agent_call_governor_runtime, "GovernedRuntime"))

def test_build_run_hooks_reports_missing_distribution_cleanly(self):
    with self.assertRaisesRegex(RuntimeError, "openai-agents"):
        build_run_hooks(runtime)
```

When the optional extra is installed, assert that the returned object is a `RunHooksBase` and records agent/tool/LLM/handoff phases.

- [ ] **Step 2: Run adapter tests without the optional dependency**

Run: `python -m unittest tests.test_agents_sdk_adapter -v`

Expected: missing adapter module, followed by clean skip behavior after implementation.

- [ ] **Step 3: Implement lazy, version-checked integration**

Use `importlib.metadata.version("openai-agents")`, import `agents` only inside factories, inject hooks through the facade, and implement function-tool rejection through the SDK's supported input guardrail result.

- [ ] **Step 4: Install and test the optional extra in an isolated environment**

Run: `python -m venv .tmp/agents-sdk-venv`

Run: `.tmp/agents-sdk-venv/Scripts/python -m pip install -e ".[agents]"`

Run: `.tmp/agents-sdk-venv/Scripts/python -m unittest tests.test_agents_sdk_adapter -v`

Expected: all adapter tests pass with `openai-agents>=0.18.2,<0.19`; no API call or API key is required.

- [ ] **Step 5: Commit the optional adapter**

```text
Integrate OpenAI Agents SDK lifecycle hooks
```

### Task 6: Add runtime CLI, replay evaluation, and public documentation

**Files:**
- Create: `agent-call-governor/scripts/agent_call_governor_runtime/cli.py`
- Create: `agent-call-governor/scripts/agent_call_governor_runtime/__main__.py`
- Create: `evals/runtime_workloads.json`
- Create: `evals/run_runtime_evals.py`
- Create: `evals/runtime-results-2026-07-14.md`
- Create: `tests/test_runtime_cli.py`
- Modify: `README.md`
- Modify: `README.ko.md`
- Modify: `evals/README.md`
- Modify: `agent-call-governor/SKILL.md`
- Modify: `agent-call-governor/agents/openai.yaml`

**Interfaces:**
- Produces: `agent-call-governor-runtime report`, `export-jsonl`, and `codex-hook`
- Produces: replay metrics for necessary-call preservation, redundant-call blocking, duplicates, and under-calling

- [ ] **Step 1: Add failing CLI and metric tests**

```python
def test_report_summarizes_policy_and_execution_counts(self):
    result = run_cli("report", "--db", str(db_path), "--json")
    self.assertEqual(result["policy_blocked"], 1)
    self.assertEqual(result["executed"], 2)

def test_runtime_eval_has_at_least_sixty_variants(self):
    cases = json.loads(Path("evals/runtime_workloads.json").read_text())
    self.assertGreaterEqual(len(cases), 60)
```

- [ ] **Step 2: Run tests and confirm CLI/eval assets are missing**

Run: `python -m unittest tests.test_runtime_cli -v`

Expected: missing CLI module or workload file.

- [ ] **Step 3: Implement CLI, 60+ replay variants, and bilingual docs**

Document install commands, observe-first rollout, enforce/failure-policy selection, privacy defaults, Codex limitations, Agents SDK coverage, and migration from v0.1.0.

- [ ] **Step 4: Run replay evaluation and write checked-in results**

Run: `python evals/run_runtime_evals.py`

Expected: zero under-call regressions, all required calls preserved, and all expected duplicate/redundant calls blocked in enforce mode.

- [ ] **Step 5: Validate the updated skill and all tests**

Run: `python -m unittest discover -s tests -v`

Run: `python evals/run_evals.py && python evals/run_runtime_evals.py`

Run: `python C:/Users/Students/.codex/skills/.system/skill-creator/scripts/quick_validate.py agent-call-governor`

Expected: all commands exit `0`.

- [ ] **Step 6: Commit runtime evaluation and docs**

```text
Document and evaluate runtime governance
```

### Task 7: Review, publish, and release v0.2.0

**Files:**
- Modify: `.github/workflows/validate.yml`
- Verify: every file changed since `v0.1.0`

**Interfaces:**
- Produces: a green GitHub PR and `v0.2.0` release

- [ ] **Step 1: Make CI install the package and run both eval suites**

Add editable core installation, full unit discovery, both eval runners, skill validation, and an optional-dependency adapter job.

- [ ] **Step 2: Run a fresh-context forward test and independent code review**

Give the tester the repository and a realistic integration request without the intended answer. Give the reviewer the full `v0.1.0..HEAD` diff plus this plan and design.

- [ ] **Step 3: Fix all Critical and Important findings and rerun focused tests**

Record any remaining Minor limitations in the release notes rather than silently discarding them.

- [ ] **Step 4: Run the complete fresh verification gate**

Run: `python -m pip install -e .`

Run: `python -m unittest discover -s tests -v`

Run: `python evals/run_evals.py && python evals/run_runtime_evals.py`

Run: `python C:/Users/Students/.codex/skills/.system/skill-creator/scripts/quick_validate.py agent-call-governor`

Run: `python -m build`

Expected: all tests and eval cases pass, skill validation passes, and both wheel and source distribution build.

- [ ] **Step 5: Publish a ready PR, wait for green CI, and squash-merge**

Use branch `agent/runtime-firewall-mvp`, summarize enforcement coverage and limitations, and include exact verification counts.

- [ ] **Step 6: Tag and publish `v0.2.0`**

Release notes must distinguish application-wrapper enforcement, Codex observe/warn hooks, optional Agents SDK integration, and replay rather than production evidence.
