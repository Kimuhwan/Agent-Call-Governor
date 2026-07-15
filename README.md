# Agent Call Governor

[한국어](README.ko.md) · **English**

[![Validate](https://github.com/Kimuhwan/Agent-Call-Governor/actions/workflows/validate.yml/badge.svg)](https://github.com/Kimuhwan/Agent-Call-Governor/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Codex Plugin](https://img.shields.io/badge/Codex-Plugin-111827)](.codex-plugin/plugin.json)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB)](pyproject.toml)

Local-first, quality-preserving governance and observability for Codex calls.

## What it is

Agent Call Governor v0.3.0 is a root Codex plugin that records six lifecycle events in a local SQLite ledger and applies a deterministic call policy. It helps remove exact duplicate or exhausted calls while preserving mandatory work, verification, and risk-appropriate retries. Its bundled Codex hook is **observe/warn-only**; application-owned wrappers remain the enforcement surface.

## Three-line summary

1. Observe Codex tool and subagent activity locally without storing raw hook payloads.
2. Inspect, report, export, retain, or delete session records with explicit CLI commands.
3. Govern toward the minimum sufficient calls while measuring under-calling before call reduction.

The plugin handles exactly these host events: `SessionStart`, `PreToolUse`, `PostToolUse`, `SubagentStart`, `SubagentStop`, and `Stop`.

## Install the Codex plugin

The supported interactive path is Codex Desktop's Plugins directory or **Settings -> Plugins**. Add the repository root `Kimuhwan/Agent-Call-Governor` at ref `main`. This installs the plugin manifest, skill, dispatcher, and hooks together.

If the Codex marketplace shell command is available, this is the separate optional equivalent:

```console
codex plugin marketplace add Kimuhwan/Agent-Call-Governor --ref main
```

Installing or enabling a plugin does not trust its hooks automatically. Open `/hooks`, review the hook content and its **exact hash**, and explicitly trust that exact version. Codex skips a new or changed hook until you review and trust its new exact hash; never bypass this boundary. Current contract references: [Codex hooks](https://learn.chatgpt.com/docs/hooks.md) and [build plugins](https://learn.chatgpt.com/docs/build-plugins.md), checked for compatibility on **2026-07-15**.

The host supports denying a supported `PreToolUse` call with `hookSpecificOutput.permissionDecision: "deny"`. Agent Call Governor v0.3 deliberately does not emit that response. Its installed hook is observe/warn-only and is not a firewall.

### Install the companion CLI wheel

The plugin runs from its own bundled source. Install the companion wheel only when you want the global `agent-call-governor-runtime` commands shown below:

```console
python -m pip install https://github.com/Kimuhwan/Agent-Call-Governor/releases/download/v0.3.0/agent_call_governor_runtime-0.3.0-py3-none-any.whl
```

### Compatibility-only skill installers

These scripts copy only the skill to `CODEX_HOME/skills/agent-call-governor`. Use them for older or skill-only Codex setups, not as a substitute for installing the root plugin:

```console
git clone https://github.com/Kimuhwan/Agent-Call-Governor.git
cd Agent-Call-Governor
```

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

```sh
./install.sh
```

## First local session

After plugin installation and exact-hash trust, restart Codex and perform a normal tool or subagent task. The hook writes to `events.sqlite3` under the host-provided `PLUGIN_DATA` directory by default. The database appears only after the plugin hook receives an event; installing the compatibility skill alone does not create event data.

To choose a different database, set `AGENT_CALL_GOVERNOR_DB` **before launching Codex**, then pass that same path to every CLI command as `--db`:

```powershell
$env:AGENT_CALL_GOVERNOR_DB = "PATH_TO_EVENTS"
$env:AGENT_CALL_GOVERNOR_RETENTION_DAYS = "7"
```

```sh
export AGENT_CALL_GOVERNOR_DB="PATH_TO_EVENTS"
export AGENT_CALL_GOVERNOR_RETENTION_DAYS="7"
```

Run the seven-check doctor after the database path is known. A Windows permissions result can be a best-effort warning rather than proof of an owner-only ACL.

## Inspect, export, retain, and delete

Use the same database path used by the plugin:

```console
agent-call-governor-runtime doctor --plugin-root PATH_TO_CHECKOUT --db PATH_TO_EVENTS
agent-call-governor-runtime sessions --db PATH_TO_EVENTS
agent-call-governor-runtime inspect SESSION_ID --db PATH_TO_EVENTS
agent-call-governor-runtime report --db PATH_TO_EVENTS
agent-call-governor-runtime export --format jsonl --output PATH_TO_EXPORT --db PATH_TO_EVENTS
agent-call-governor-runtime delete-session SESSION_ID --yes --db PATH_TO_EVENTS
```

`sessions` returns privacy-safe trace references; use one as `SESSION_ID`. `inspect` and `report` read the authoritative SQLite ledger. `export` writes a point-in-time, sanitized JSONL file. JSONL is export and legacy compatibility only: the plugin does not maintain a live mirror.

The default retention period is seven days (`AGENT_CALL_GOVERNOR_RETENTION_DAYS=7`). On `SessionStart` and `Stop`, the plugin securely prunes only closed traces whose last event is older than the cutoff. An interrupted session without `session.stopped` is treated as active and skipped by automatic retention; remove it deliberately with `delete-session`. Deletion uses SQLite secure deletion and storage cleanup, but filesystem, SSD, backup, or snapshot behavior can prevent a guarantee that every historical byte is unrecoverable.

## Modes, profiles, and risk

The root plugin supports only `observe` and `warn`. `observe` records decisions without a user warning; `warn` can return a `systemMessage`, but the call still proceeds. `enforce` belongs only to supported application-owned wrappers.

Choose `balanced` by default, `strict` for reversible low-risk work, and `quality-first` for uncertain or costly-to-correct work. These are profiles, not modes. Classify risk as `low`, `medium`, or `high`; the plugin defaults to `medium`. A risk floor can raise an overly small configured budget, and high-risk work preserves at least one materially changed-strategy retry. Budgets are ceilings for ordinary work, not targets or blanket bans.

## Supported integrations

| Surface | Purpose | Behavior |
| --- | --- | --- |
| Root Codex plugin | Six-event lifecycle capture and policy warnings | Observe/warn-only, process-level fail-open |
| Codex skill | Plan minimum sufficient agent, tool, and model calls | Prompt-level governance |
| Companion runtime | Wrap application-owned sync/async calls | `observe`, `warn`, or `enforce` |
| OpenAI Agents SDK adapters | Wrap runs and supported function tools | Application-owned wrapper/guardrail behavior |
| Policy CLI | Evaluate a JSON proposal | Deterministic allow/deny result |

`strict`, `balanced`, and `quality-first` are policy **profiles**. Hook/runtime modes are `observe`, `warn`, and—only for supported application-owned wrappers—`enforce`.

The plugin data path is:

`Codex event -> dispatcher -> normalizer -> policy -> SQLite -> CLI/export`

See [architecture](docs/architecture.md) for component and trust boundaries.

## Security and privacy

- SQLite is authoritative and local; the plugin configures no remote telemetry.
- Raw objectives, prompts, tool arguments, tool results, transcripts, and exception messages are not persisted by the bundled hook. Host identifiers and relevant material inputs become SHA-256 references.
- Hash references are identifiers, not encryption. Low-entropy values can still be guessable, and sanitized exports still deserve access control.
- Hook errors fail open with an exception type only so a recorder outage does not expose the host payload or halt Codex.
- The exact-hash trust review protects hook execution, while filesystem permissions protect stored data. Windows owner-only ACL enforcement is best effort.

Read the [security architecture](docs/security.md) and [vulnerability reporting policy](SECURITY.md) before production use.

## Evidence and limitations

Evaluation priority is: **task success**, **under-call rate**, **false-block rate**, then **call efficiency**. A lower call count is useful only after the quality floor holds.

The checked-in deterministic policy suite, runtime replay, and small matched A/B study are directional evidence. The v0.3 ten-case instrumentation pilot is not a model-quality benchmark; rely on it only after its runner and dated results are checked in. None of these results establishes production task success, savings, latency, or generalization. See [benchmark methodology](docs/benchmark-methodology.md) and [evaluation details](evals/README.md).

Known limits:

- Fingerprint v2 enforces exact duplicates, not semantic or near-duplicates.
- Schema-v2 records enable inspection, but policy replay is deferred.
- Usage and cost can be null when the host event does not supply trustworthy values.
- Migrated legacy-v1 rows still count for budget and progress, but they are not exact fingerprint-v2 duplicate candidates. Upgrading from v0.2 therefore starts a new exact-duplicate epoch.
- An open or crashed session requires manual deletion if it never receives `session.stopped`.
- Codex hook telemetry is observe/warn-only. Use a supported application-owned runtime wrapper when a call must be stopped.

The complete list is in [limitations](docs/limitations.md).

## Update, disable, and uninstall

Update the companion wheel independently:

```console
python -m pip install --upgrade https://github.com/Kimuhwan/Agent-Call-Governor/releases/download/v0.3.0/agent_call_governor_runtime-0.3.0-py3-none-any.whl
python -m pip uninstall agent-call-governor-runtime
```

Disable or remove the root plugin from Codex Desktop **Settings -> Plugins**. Re-enable it only after reviewing and trusting the current hook hash. Remove a compatibility-only skill separately if you installed one:

```powershell
Remove-Item -LiteralPath "$HOME\.codex\skills\agent-call-governor" -Recurse -Force
```

```sh
rm -rf -- "$HOME/.codex/skills/agent-call-governor"
```

Plugin removal, wheel uninstall, compatibility-skill removal, and SQLite-data deletion are four independent actions. None automatically performs the others. Preserve or delete `events.sqlite3` deliberately.

## Validate locally

```console
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python evals/run_evals.py
python evals/run_runtime_evals.py
python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/agent-call-governor
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
python -m build
```

## Contributing and roadmap

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), include tests for both over-calling and under-calling, and keep public claims tied to reproducible evidence. The roadmap favors policy replay only after schema-v2 evidence is sufficient, followed by carefully measured enforcement experiments.

## License

[MIT](LICENSE)
