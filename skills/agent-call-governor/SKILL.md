---
name: agent-call-governor
description: Govern agent delegation and direct-tool calls with minimum-sufficient, quality-preserving routing, risk floors, exact-duplicate evidence, progress gates, and stop conditions; inspect, report, export, or delete local Codex plugin sessions. Use before spawning agents, retrying calls, or planning multi-call work; when cost, latency, runaway delegation, under-calling, uncertainty, or high-risk verification matters; and when users ask about Agent Call Governor installation, hooks, storage, local activity, privacy, exports, or cleanup.
---

# Agent Call Governor

Complete the task with the minimum sufficient calls. Optimize for task success first and call efficiency second. Apply this policy in the current agent; never delegate merely to decide whether to delegate.

## Decide the route

1. State the concrete objective and classify the proposed call as `agent` or `direct-tool`.
2. Identify the capability gap, expected new information, material inputs, and observable stop condition.
3. Choose the first sufficient route:
   - use supplied context and local reasoning;
   - use one batched direct tool;
   - delegate one bounded specialist task;
   - use multiple agents only for independent work that needs distinct capabilities and reconciliation.
4. Select `balanced` by default. Select `strict` for reversible, low-risk work or `quality-first` for uncertain, high-impact, or costly-to-correct work. Treat `quality-first` as a profile, never a mode.
5. Set separate budgets for agent and direct-tool calls. Apply the profile's quality/risk floor before enforcing a requested ceiling.
6. Evaluate mandatory calls, exact duplicates, progress, and remaining budget before execution.

Read [references/profiles.md](references/profiles.md) for numeric limits. Use `python scripts/governor.py evaluate proposal.json` for complex workflows and read [references/proposal-schema.md](references/proposal-schema.md) before preparing the input. Skip the script when the decision is obvious.

## Preserve the quality floor

Increase the route or effective budget when material uncertainty remains, consequences are hard to reverse, independent verification could catch a costly error, evidence spans systems, acceptance criteria are unproven, or a required action has not occurred.

Allow calls required for current information, explicit verification, high-stakes work, private state, missing files, user-requested actions, safety, or system instructions. Let a valid mandatory reason bypass budget and progress stops, but never let it bypass an exact duplicate that could repeat an external side effect.

Allow high-risk work at least one materially changed-strategy retry after low progress. Do not claim a governance improvement when task success falls; track under-calling and false blocks alongside redundant calls.

## Require exact duplicate evidence

Build fingerprint-v2 from the objective, route, material inputs, working directory, tool version, and optional state token. Treat a call as an exact duplicate only when a stored `fingerprint_version: 2` candidate has the same canonical digest.

Do not treat semantic similarity or a near match as an exact duplicate. Change the objective, material inputs, source, parameters, strategy, or state token when a real retry needs new evidence. Legacy fingerprint-v1 rows may continue budget and progress accounting, but never use them as exact fingerprint-v2 duplicate candidates.

## Gate on observed progress

Classify each completed call using evidence:

- `sufficient`: stop calling and complete the task;
- `material_progress`: continue only for a defined remaining gap;
- `low_progress`: allow only a permitted materially changed-strategy retry;
- `no_progress`: stop or report the blocker;
- `unknown`: consume budget but trigger no progress stop or retry rule.

Use `unknown` when the host provides no deterministic signal. Never promote absence of evidence into progress. Recalculate the cheapest sufficient route after every result.

## Inspect local plugin activity

Treat plugin installation, hook trust, the companion wheel, and compatibility-only skill installation as separate surfaces. Installing the skill alone does not create plugin events.

Install the repository-root Codex plugin from Codex Desktop's Plugins directory or **Settings -> Plugins**. The marketplace can be registered separately from a shell:

```powershell
codex plugin marketplace add Kimuhwan/Agent-Call-Governor --ref main
```

After installation or enablement, open `/hooks`, review the hook, and explicitly trust its exact current hash. Do not infer trust from installation. Re-review and re-trust after any hook content change.

Install the companion wheel when global `agent-call-governor-runtime` commands are needed:

```powershell
python -m pip install https://github.com/Kimuhwan/Agent-Call-Governor/releases/download/v0.3.0/agent_call_governor_runtime-0.3.0-py3-none-any.whl
```

The plugin records `SessionStart`, `PreToolUse`, `PostToolUse`, `SubagentStart`, `SubagentStop`, and `Stop`. It defaults to the host-provided `PLUGIN_DATA` directory and stores the authoritative ledger at `PLUGIN_DATA/events.sqlite3`.

If `AGENT_CALL_GOVERNOR_DB` overrides that location, set it before launching Codex and pass the same path as `--db` to every CLI command. Discover the hashed session reference with `sessions`, then inspect, summarize, export, or delete it:

```powershell
agent-call-governor-runtime doctor --plugin-root PATH_TO_CHECKOUT --db PATH_TO_EVENTS
agent-call-governor-runtime sessions --db PATH_TO_EVENTS
agent-call-governor-runtime inspect SESSION_ID --db PATH_TO_EVENTS
agent-call-governor-runtime report --db PATH_TO_EVENTS
agent-call-governor-runtime export --format jsonl --output PATH_TO_EXPORT --db PATH_TO_EVENTS
agent-call-governor-runtime delete-session SESSION_ID --yes --db PATH_TO_EVENTS
```

Treat SQLite as authoritative. Treat JSONL as a sanitized point-in-time export or legacy compatibility format; the bundled plugin dispatcher configures no live JSONL mirror. Read [references/codex-hooks.md](references/codex-hooks.md) before configuring or explaining hook behavior.

## State the enforcement boundary

Keep the bundled Codex hook in `observe` or `warn`. It is fail-open and does not emit a denial, so Agent Call Governor v0.3 is not an agent-call firewall. In `warn`, a call that policy would reject can still execute.

Distinguish that product choice from the host contract: supported Codex `PreToolUse` hooks can deny with `hookSpecificOutput.permissionDecision: "deny"`. Use an application-owned `GovernedRuntime` wrapper when this project must enforce a decision before an owned Python call executes. Never describe wrapped-call enforcement as universal Codex enforcement.

## Protect local data

Persist only hashed host identifiers and objectives, fingerprint and lifecycle facts, decisions, progress, durations, nullable usage/cost fields, and explicitly safe metadata. Do not persist raw prompts, tool inputs, tool results, transcripts, last assistant messages, or exception messages. Treat report counts as directional evidence: separate policy decisions from execution because observe/warn calls may proceed.

Read [references/codex-hooks.md](references/codex-hooks.md) for storage, trust, retention, and compatibility details. Read [references/openai-agents-sdk.md](references/openai-agents-sdk.md) only when integrating with the OpenAI Agents SDK.
