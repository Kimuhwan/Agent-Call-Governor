# Agent Call Governor Skill Comprehension Evaluation

## v0.2 baseline

- Tag: `v0.2.0`
- Commit: `f820c4e916116c4441a4926a953edd6b9d2cae1a`
- Run date: `2026-07-15`
- Evaluation subject: `/root/task10_baseline`
- Model identifier: not exposed by the evaluation runtime
- Context: only the exact released `agent-call-governor/SKILL.md` snapshot was
  supplied; repository, plan, conversation, tools, and internet access were
  excluded by instruction.

### Verbatim prompt

```text
You have Agent Call Governor v0.2 instructions only. Explain how to install the root Codex plugin, where its six hook events store data, how to inspect and delete one session, whether Agent Call Governor v0.3's installed hook currently blocks a tool call, and separately whether the host Codex PreToolUse contract supports denial. Cite the repository paths or commands you relied on.
```

### Verbatim answer

The v0.2 snapshot is insufficient to answer the requested operational details reliably:

- Root plugin installation: no installation path or command is provided.
- Six hook events and storage: the events are not named, and no storage location is given. `SKILL.md` only says SQLite history is authoritative and JSONL may be an optional audit mirror.
- Inspecting/deleting a session: no session-management commands are documented. The only supplied command, `python scripts/governor.py evaluate proposal.json`, evaluates a proposal; it does not inspect or delete sessions.
- Agent Call Governor v0.3: its installed code, configuration, and runtime state are absent, so I cannot determine whether its hook currently blocks tool calls. The v0.2 `SKILL.md` says Codex lifecycle hooks should be used only for `observe` or `warn` and must not be described as providing a pre-tool veto.
- Host Codex `PreToolUse` denial contract: the current host contract is not included. Based strictly on `SKILL.md`, denial support cannot be claimed, but that is not a current-contract verification.

Relevant supplied references are `SKILL.md`, its unavailable link `references/codex-hooks.md`, and the command `python scripts/governor.py evaluate proposal.json`. Any more specific installation, hook-storage, session, v0.3, or current Codex contract answer would require repository/runtime evidence not present in the snapshot.

### Failure matrix

| Criterion | Result | Evidence |
| --- | --- | --- |
| Root plugin installation path | FAIL | The answer says no installation path or command is provided. |
| All six events and `PLUGIN_DATA` storage | FAIL | The answer says the events and storage location are absent. |
| Exact inspect and delete commands | FAIL | The answer says no session-management commands are documented. |
| Host denial support versus ACG v0.3 observe/warn-only behavior | FAIL | It identifies the v0.2 observe/warn statement but cannot establish current host denial support or the installed v0.3 implementation from the supplied snapshot. |

Baseline result: **0/4**. This is the expected RED result for the released v0.2
skill snapshot.

## v0.3 forward test

- Run date: `2026-07-15`
- Evaluation subject: `/root/task10_forward`
- Model identifier: not exposed by the evaluation runtime
- Context: only the revised `skills/agent-call-governor/SKILL.md` and its
  directly referenced `references/codex-hooks.md` were supplied. The v0.2
  answer, plan, failure matrix, repository, conversation, tools, and internet
  were excluded by instruction.

### Verbatim prompt

```text
You have Agent Call Governor v0.2 instructions only. Explain how to install the root Codex plugin, where its six hook events store data, how to inspect and delete one session, whether Agent Call Governor v0.3's installed hook currently blocks a tool call, and separately whether the host Codex PreToolUse contract supports denial. Cite the repository paths or commands you relied on.
```

### Verbatim answer

Install or enable the repository-root **Agent Call Governor** plugin from Codex Desktop’s Plugins directory or **Settings → Plugins**. Registering the marketplace is a separate optional step:

```powershell
codex plugin marketplace add Kimuhwan/Agent-Call-Governor --ref main
```

Then open `/hooks`, inspect the hook, and trust its exact current hash. Re-trust it whenever the hook changes. The compatibility `install.ps1` and `install.sh` scripts install only the skill, not the root plugin or its hooks.

The plugin sends these six events from `hooks/hooks.json` to `hooks/dispatch.py`:

- `SessionStart`
- `PreToolUse`
- `PostToolUse`
- `SubagentStart`
- `SubagentStop`
- `Stop`

They are stored in the authoritative SQLite ledger at:

```text
PLUGIN_DATA/events.sqlite3
```

To override it, set `AGENT_CALL_GOVERNOR_DB` before launching Codex, fully restart Codex, and use the same database path with every CLI command.

Install the companion CLI:

```powershell
python -m pip install https://github.com/Kimuhwan/Agent-Call-Governor/releases/download/v0.3.0/agent_call_governor_runtime-0.3.0-py3-none-any.whl
```

Then discover the hashed session ID, inspect it, and delete it:

```powershell
agent-call-governor-runtime sessions --db PATH_TO_EVENTS
agent-call-governor-runtime inspect SESSION_ID --db PATH_TO_EVENTS
agent-call-governor-runtime delete-session SESSION_ID --yes --db PATH_TO_EVENTS
```

Deletion removes that trace and attempts secure SQLite cleanup; it does not uninstall the plugin, skill, or companion wheel.

Agent Call Governor v0.3’s installed hook does **not** block tool calls. It is deliberately fail-open and supports only `observe` and `warn`; even a policy-rejected call can execute, and `warn` only surfaces a message.

Separately, the host Codex `PreToolUse` contract **does support denial** through:

```json
{
  "hookSpecificOutput": {
    "permissionDecision": "deny"
  }
}
```

The v0.3 plugin simply chooses not to emit that response.

Sources: `skills/agent-call-governor/SKILL.md` and `skills/agent-call-governor/references/codex-hooks.md`.

### Pass matrix

| Criterion | Result | Evidence |
| --- | --- | --- |
| Root plugin installation path | PASS | Names Codex Desktop Plugins/Settings and the separate marketplace command, then `/hooks` exact-hash trust. |
| All six events and `PLUGIN_DATA` storage | PASS | Names all six events and `PLUGIN_DATA/events.sqlite3`. |
| Exact inspect and delete commands | PASS | Gives `sessions`, `inspect SESSION_ID`, and `delete-session SESSION_ID --yes` with the shared `--db`. |
| Host denial support versus ACG v0.3 observe/warn-only behavior | PASS | Separately states host denial support and that the installed v0.3 hook does not emit it or block calls. |

Forward-test result: **4/4**.
