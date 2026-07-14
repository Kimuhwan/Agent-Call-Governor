# Codex lifecycle hooks

Agent Call Governor can record these current Codex lifecycle events:

- `PreToolUse` and `PostToolUse`
- `SubagentStart` and `SubagentStop`

The adapter stores policy and lifecycle metadata in SQLite and can mirror it to
JSONL. It hashes `tool_input` for duplicate detection and does not store raw tool
arguments, tool results, transcripts, or the last assistant message.

Policy budgets and duplicate history use hashed session and turn references when
Codex supplies a turn ID. Original `session_id`, `turn_id`, `tool_use_id`, and
`agent_id` values are not persisted; SHA-256 references retain correlation.
Payloads without a turn ID fall back to a call-local scope, favoring quality over
carrying a stale budget across unrelated tasks. Re-delivery of the same start or
terminal host ID is idempotent; a real second call must have a new host ID and is
then evaluated by fingerprint.

## Capability boundary

Use `--mode observe` first, then `--mode warn` after reviewing the ledger.
Current Codex hook contracts do not provide a dependable pre-call veto:

- `PreToolUse` accepts `systemMessage`, but its common stop fields are unsupported.
- `SubagentStart` can surface context or warnings, but `continue: false` does not
  stop the subagent from starting.
- matching command hooks may launch concurrently.

Therefore this adapter intentionally rejects `enforce`. Use the application-owned
`GovernedRuntime` wrapper when a call must be blocked before execution.

## Configure

Install the Python package, copy `examples/codex-hooks.json` to a supported
`hooks.json` location, then replace every absolute script and database path.
Codex asks you to review and trust a new or changed command hook before it runs.

The same command handles all four events:

```powershell
agent-call-governor-runtime codex-hook `
  --mode observe `
  --db C:\absolute\governor-data\events.sqlite3 `
  --jsonl C:\absolute\governor-data\events.jsonl
```

When the runtime package is not installed as a command, use the script directly:

```powershell
python C:\absolute\Agent-Call-Governor\skills\agent-call-governor\scripts\codex_hook.py `
  --mode observe `
  --db C:\absolute\governor-data\events.sqlite3 `
  --jsonl C:\absolute\governor-data\events.jsonl
```

Switch to `warn` to surface a `systemMessage` when the policy would block a call.
The call still proceeds. Omit `--jsonl` when only the authoritative SQLite ledger
is needed.

## Failure policy

The default is `--failure-policy fail-open`, which avoids breaking Codex if the
policy or ledger fails. `fail-closed` makes the hook command fail, but that still
does not turn unsupported Codex hook events into an agent-call firewall.
