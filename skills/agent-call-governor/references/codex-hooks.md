# Codex plugin hooks

Use this reference when installing, trusting, operating, or explaining the repository-root Codex plugin. This contract was checked against the official Codex documentation on 2026-07-15:

- [Codex hooks](https://learn.chatgpt.com/docs/hooks.md)
- [Build Codex plugins](https://learn.chatgpt.com/docs/build-plugins.md)

## Install and trust

Install or enable **Agent Call Governor** from the Codex Desktop Plugins directory or **Settings -> Plugins**. To register its GitHub marketplace from a shell, run:

```powershell
codex plugin marketplace add Kimuhwan/Agent-Call-Governor --ref main
```

Treat registration, installation, and hook trust as separate steps. Installing or enabling a plugin does not trust its command hooks. Open `/hooks`, inspect the command and content, and approve the exact current hook hash. New or changed hook content is skipped until the user reviews and trusts it again. Never bypass or automate that decision.

Compatibility-only `install.ps1` and `install.sh` copy the skill but do not install the repository-root plugin or create hook data.

## Observe six events

The plugin's `hooks/hooks.json` sends these six lifecycle events to `hooks/dispatch.py`:

- `SessionStart`
- `PreToolUse`
- `PostToolUse`
- `SubagentStart`
- `SubagentStop`
- `Stop`

Follow this data path:

`Codex event -> dispatcher -> normalizer -> policy -> SQLite -> CLI/export`

The host supplies `PLUGIN_ROOT` and `PLUGIN_DATA`. The dispatcher loads its bundled Python runtime from `PLUGIN_ROOT` and defaults to `PLUGIN_DATA/events.sqlite3`. To use another database, set `AGENT_CALL_GOVERNOR_DB` before launching Codex, fully restart Codex, and pass the same path with `--db` to the companion CLI.

The dispatcher supports these pre-launch settings:

- `AGENT_CALL_GOVERNOR_MODE`: `observe` or `warn`; default `observe`.
- `AGENT_CALL_GOVERNOR_PROFILE`: `strict`, `balanced`, or `quality-first`; default `balanced`.
- `AGENT_CALL_GOVERNOR_RISK`: `low`, `medium`, or `high`; default `medium`.
- `AGENT_CALL_GOVERNOR_RETENTION_DAYS`: 1 through 3650; default 7.
- `AGENT_CALL_GOVERNOR_STALE_SECONDS`: stale reservation recovery window; default 86400.

`quality-first` is a profile, not a mode.

## Inspect and clean up

Install the v0.3.0 companion wheel to expose the global CLI:

```powershell
python -m pip install https://github.com/Kimuhwan/Agent-Call-Governor/releases/download/v0.3.0/agent_call_governor_runtime-0.3.0-py3-none-any.whl
```

Use the same `PATH_TO_EVENTS` that the plugin uses:

```powershell
agent-call-governor-runtime doctor --plugin-root PATH_TO_CHECKOUT --db PATH_TO_EVENTS
agent-call-governor-runtime sessions --db PATH_TO_EVENTS
agent-call-governor-runtime inspect SESSION_ID --db PATH_TO_EVENTS
agent-call-governor-runtime report --db PATH_TO_EVENTS
agent-call-governor-runtime export --format jsonl --output PATH_TO_EXPORT --db PATH_TO_EVENTS
agent-call-governor-runtime delete-session SESSION_ID --yes --db PATH_TO_EVENTS
```

Use the hashed session reference printed by `sessions` as `SESSION_ID`. Add `--session SESSION_ID` to `report` or `export` to limit output to one trace. Deletion removes that trace and attempts SQLite secure cleanup; it does not uninstall the plugin, wheel, or skill.

Treat SQLite as the authoritative local ledger. The bundled dispatcher never passes a JSONL path. Use `export --format jsonl` for a sanitized point-in-time export; treat the deprecated `export-jsonl` command and manual `codex-hook --jsonl` surface as legacy compatibility, not live plugin mirroring.

## Keep the boundary honest

The current host contract supports denying a supported `PreToolUse` call with `hookSpecificOutput.permissionDecision: "deny"`. Agent Call Governor v0.3 deliberately does not emit that response. Its bundled dispatcher is observe/warn-only and fail-open:

- `observe` records the policy decision without surfacing a warning.
- `warn` may return a `systemMessage`, but the call still proceeds.
- dispatcher errors return success after a type-only diagnostic so Codex availability is preserved.

Do not call the installed plugin an agent-call firewall. Use application-owned `GovernedRuntime` enforcement only for Python calls that the application itself controls.

## Interpret the ledger safely

The normalizer hashes host session, turn, tool-use, and agent identifiers. It does not persist raw tool inputs, tool results, prompts, transcripts, last assistant messages, or exception messages. It records safe lifecycle, policy, progress, timing, and nullable usage/cost facts. Local file permissions are best effort on Windows; `doctor` reports that limitation.

Fingerprint-v2 provides exact-duplicate evidence only within its current epoch. Legacy fingerprint-v1 rows still contribute to budget and progress history but are not exact v2 duplicate candidates. Near-duplicate or semantic replay detection is not implemented.

Unknown progress is neutral: it consumes budget but does not trigger sufficient, low-progress, or no-progress rules. Policy decisions and execution outcomes are separate; a `would_block` decision in observe/warn mode may still have a `call.started` event. Treat reports as directional evidence rather than proof of response quality.

Retention is opportunistic at `SessionStart` and `Stop`. A crashed session may lack `session.stopped`; automatic retention treats it as active and skips it until an operator runs `delete-session`. Exports are not automatically deleted with their source session.
