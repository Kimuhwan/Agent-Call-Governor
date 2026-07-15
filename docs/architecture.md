# Architecture

Agent Call Governor v0.3.0 is a local-first Codex plugin plus an optional Python runtime. The plugin observes six Codex lifecycle events, normalizes privacy-safe facts, evaluates deterministic policy, and records one authoritative local history.

Compatibility note: this document reflects the Codex hook and plugin contracts checked on **2026-07-15**. See the official [Codex hooks documentation](https://learn.chatgpt.com/docs/hooks.md) and [plugin documentation](https://learn.chatgpt.com/docs/build-plugins.md).

## Plugin data flow

`Codex event -> dispatcher -> normalizer -> policy -> SQLite -> CLI/export`

1. **Codex event** — Codex delivers `SessionStart`, `PreToolUse`, `PostToolUse`, `SubagentStart`, `SubagentStop`, or `Stop` to the configured command hook.
2. **Dispatcher** — `hooks/dispatch.py` locates the plugin through `PLUGIN_ROOT`, chooses the database under `PLUGIN_DATA` or the explicit `AGENT_CALL_GOVERNOR_DB`, invokes bundled code, and fails open with only an exception type when recording is unavailable.
3. **Normalizer** — the Codex adapter maps the host event to schema-v2 lifecycle events. Raw host payloads are not forwarded to persistence. Host IDs and material-input identity become SHA-256 references.
4. **Policy** — start events are evaluated under `strict`, `balanced`, or `quality-first`. The policy separates agent and direct-tool budgets, protects mandatory calls and risk floors, and uses fingerprint v2 for exact-duplicate evidence.
5. **SQLite** — `events.sqlite3` is the authoritative ledger, and lifecycle facts are appended in sequence. Explicit deletion and retention can remove whole traces. Policy decisions and call reservations use transactions so concurrent processes cannot independently consume the same remaining policy slot.
6. **CLI/export** — the companion wheel lists sessions, inspects one trace, reports policy outcomes, securely deletes one trace, and produces a sanitized point-in-time JSONL export.

The dispatcher does not configure a JSONL path. JSONL is an export and legacy-compatibility format, not a live mirror of plugin activity.

## Event model

Host events become canonical lifecycle records:

| Codex event | Canonical purpose |
| --- | --- |
| `SessionStart` | Start a trace, recover stale reservations, and attempt retention |
| `PreToolUse` | Propose and evaluate a direct-tool call |
| `PostToolUse` | Record terminal tool state and available progress evidence |
| `SubagentStart` | Propose and evaluate an agent call |
| `SubagentStop` | Record terminal subagent state and available progress evidence |
| `Stop` | Close a trace and attempt retention |

Session, turn, tool-use, agent, and parent identifiers are persisted only as references. A Codex turn scopes policy history when the host supplies a turn ID; the trace ID continues to group operational inspection.

Repeated tool or subagent start/terminal delivery with the same stable host call ID is idempotent. A reused call ID with conflicting material-input identity is rejected internally and the dispatcher still fails open, reporting only the error type. Session boundaries are different: identical `SessionStart` delivery and `Stop` without a turn ID collapse only inside a five-second delivery window, while `Stop` with a turn ID is permanently idempotent for that turn. This prevents ambiguous call records without suppressing a legitimate later resume.

## Governance and enforcement boundaries

The Codex host supports denying a supported `PreToolUse` call with `hookSpecificOutput.permissionDecision: "deny"`. Agent Call Governor v0.3 intentionally does not emit that response. Its plugin hook accepts only `observe` or `warn`, and process failures are fail-open. In warn mode a would-block decision can surface a supported message while the host call proceeds.

Actual pre-call enforcement remains available only where an application deliberately wraps its own call with `GovernedRuntime`, `GovernedRunner`, or a supported SDK function-tool guardrail. The deterministic policy CLI also returns an allow/deny result, but the caller decides whether to honor it.

The three policy profiles are not runtime modes:

- `strict` uses smaller ordinary budgets while retaining the high-risk retry floor.
- `balanced` is the default quality/cost trade-off.
- `quality-first` allows larger risk floors and verification space.

## Storage and maintenance

The plugin database defaults to `PLUGIN_DATA/events.sqlite3`. Set `AGENT_CALL_GOVERNOR_DB` before launching Codex to override it; operational commands must receive the same path through `--db`.

Schema v2 records immutable policy facts alongside lifecycle outcomes. Usage and cost remain nullable because the hook contract does not guarantee trustworthy values. The ledger migrates the exact released v0.2 schema transactionally. Legacy-v1 rows continue to count for budget and progress, but their older fingerprint representation cannot be an exact fingerprint-v2 duplicate candidate.

Retention defaults to seven days and runs best effort on `SessionStart` and `Stop`. It deletes only closed traces older than the cutoff. A crashed session with no `session.stopped` remains active and requires explicit deletion. Secure cleanup enables SQLite secure deletion and reclaims database space; storage devices, filesystems, backups, and snapshots remain outside the application's control.

## Trust boundaries

- **Plugin source and hook trust:** installation and hook execution are separate decisions. Codex requires explicit review and trust of the current hook exact hash through `/hooks`; a hook change requires re-trust.
- **Host payload boundary:** raw stdin exists only in the dispatcher process. Normalization and source-aware redaction happen before persistence.
- **Local storage boundary:** filesystem permissions and user account isolation protect SQLite and exports. Windows owner-only ACL verification is best effort.
- **Export boundary:** exports are re-sanitized and written atomically to a non-database target. Once copied elsewhere, the operator controls their lifecycle.
- **Application boundary:** the root plugin cannot govern calls that bypass its configured hook, and wrappers cannot govern calls outside the application code that invokes them.

## Independent installation surfaces

The root plugin, the companion Python wheel, compatibility-only skill installers, and retained SQLite data have separate lifecycles. Installing or removing one does not install, remove, or erase the others.
