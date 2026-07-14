# Agent Call Governor v0.3 Local Observability Foundation Design

**Status:** Detailed design for user review  
**Date:** 2026-07-14  
**Target:** `v0.3.0`

## Goal

Make Agent Call Governor a real, local-first Codex plugin with trustworthy event recording, versioned fingerprints, privacy-safe operational inspection, and enough durable policy facts for a later counterfactual replay release.

The product sequence remains:

```text
Observe -> Explain -> Replay -> Enforce -> Prove
```

This release completes the observability foundation. It does not claim Codex-wide enforcement and does not ship policy replay before the new schema has produced trustworthy logs.

## Product position

Use this public description:

> Agent Call Governor is a local-first, quality-preserving call governance and observability plugin for Codex.

The primary product is the Codex plugin. The Python runtime wrapper and OpenAI Agents SDK adapter remain supported compatibility integrations, but they are secondary surfaces and receive no new feature expansion in this release.

Codex hooks remain `observe` or `warn` only. The current Codex hook contract supports warnings through `systemMessage`, but does not expose a dependable pre-tool or pre-subagent veto. Application-owned wrappers and supported SDK guardrails may still enforce their explicitly wrapped boundaries.

Do not use these claims:

- all-call firewall;
- complete Codex runtime enforcement;
- every subagent call is blocked; or
- universal agent gateway.

## Release and compatibility decisions

- Preserve the published `v0.2.0` tag and artifacts.
- Release this work as `v0.3.0`; never rewrite an existing tag.
- Preserve the public Python import package and console command names.
- Preserve `agent-call-governor evaluate` and the current runtime wrapper APIs.
- Keep the existing skill-only installers as a compatibility route, updated for the new skill path.
- Make plugin installation the primary documented route.
- Accept the legacy JSONL mirror argument with a deprecation warning, but plugin hooks never enable it. JSONL is export-only in the primary product path.

## Non-goals

The following are explicitly out of scope for `v0.3.0`:

- Codex hook-backed call denial;
- `explain`, `policy-replay`, or `compare` commands;
- semantic or embedding-based duplicate enforcement;
- automatic LLM progress classification;
- cloud telemetry, a daemon, a web dashboard, or SaaS features;
- MCP proxying or support for additional agent frameworks;
- organization policy inheritance, RBAC, or a remote policy server;
- raw prompt, tool input, or tool output storage, even as an opt-in;
- a broad rewrite of the policy engine or Agents SDK adapter.

## Repository and plugin packaging

Package the repository root as the plugin to avoid maintaining a second copy of the skill and runtime code:

```text
.codex-plugin/
  plugin.json
.agents/plugins/
  marketplace.json
hooks/
  hooks.json
  dispatch.py
skills/
  agent-call-governor/
    SKILL.md
    agents/openai.yaml
    references/
    scripts/
schemas/
  event-v2.schema.json
  policy-facts-v1.schema.json
docs/
  architecture.md
  limitations.md
  security.md
  benchmark-methodology.md
pyproject.toml
CHANGELOG.md
CONTRIBUTING.md
SECURITY.md
```

Move the current `agent-call-governor/` directory to `skills/agent-call-governor/`. Update `pyproject.toml`, tests, examples, installers, CI, and documentation to the new path without changing the installed Python package name.

The plugin manifest declares `skills: "./skills/"`. Keep hooks at the default discoverable path `hooks/hooks.json`; omit the explicit manifest `hooks` field until the bundled validator and product ingestion schema agree on that field. The current Codex runtime discovers the default path without the manifest field.

The repo marketplace points at this Git repository as a root plugin, marks installation as `AVAILABLE`, authentication as `ON_INSTALL`, and uses the `Productivity` category. The plugin contains no connector, MCP server, authentication secret, or remote telemetry endpoint.

Hook commands resolve bundled code from `PLUGIN_ROOT` and writable state from `PLUGIN_DATA`. `commandWindows` supplies the Windows command while `command` covers macOS and Linux. Hook execution must not depend on the user's current working directory.

## Runtime components

Keep responsibilities narrow:

```text
hooks/dispatch.py
  -> decode one hook payload and enforce process-level fail-open

codex_hook.py
  -> normalize Codex events and generate supported warning output

fingerprint.py
  -> tool-aware, versioned canonicalization and digests

models.py
  -> validated proposal, decision, and event records

ledger.py
  -> migrations, append-only persistence, atomic reservation, queries, deletion

redaction.py
  -> metadata allowlisting, secret/path/email redaction, export sanitization

cli.py
  -> doctor, sessions, inspect, delete-session, report, export, hook entry point

policy.py
  -> pure deterministic decision logic; no storage or hook knowledge
```

Do not split `policy.py` further unless implementation makes a responsibility boundary unavoidable.

## Fingerprint v2

`v0.2.0` already preserves string case and list order while sorting JSON object keys. `v0.3.0` makes that behavior explicit, versioned, and tool-aware.

Every stored decision records:

```text
fingerprint
fingerprint_version = 2
duplicate_scope = turn
```

The canonical identity always includes:

- tool or route name;
- canonical material input digest;
- working directory when it can affect meaning;
- tool canonicalizer version; and
- an optional caller-supplied state token.

Canonicalization rules:

- sort JSON object keys only;
- preserve list order;
- preserve string case and whitespace unless a tool contract explicitly says otherwise;
- remove only explicitly named volatile host identifiers;
- never persist the canonical raw payload;
- hash the canonical representation before storage.

Initial tool rules:

- `Bash`: hash the exact command representation plus `cwd`; do not attempt shell parsing or command reordering.
- `apply_patch`, `Edit`, and `Write`: hash the exact patch or edit representation plus `cwd`; do not include mutable workspace state that would accidentally permit an identical side effect after the first write.
- MCP: include the full MCP tool name and canonical JSON arguments; include `cwd` only when supplied and meaningful.
- Unknown tools: use the conservative generic JSON canonicalizer.

Duplicate levels stay separate:

- Same host call ID: return the previously persisted decision.
- Same exact fingerprint in the active turn: policy duplicate candidate.
- Near duplicate: no enforcement in this release.

`v0.3.0` accepts an explicit state token for integrations that can prove a meaningful state change. It does not guess whether arbitrary Bash or MCP calls are read-only and does not relax potentially side-effectful duplicates using a time-to-live.

## Event schema v2

SQLite remains authoritative and append-only. JSONL is a sanitized export format.

Canonical event types are:

```text
session.started
call.proposed
policy.decided
call.started
call.blocked
call.completed
call.failed
call.cancelled
progress.observed
session.stopped
```

Retain `phase`, `session_id`, and `call_id` as compatibility projections through the pre-1.0 period. Add first-class observability and policy fields:

```text
schema_version
event_id
event_type
observed_at
trace_id
turn_id
span_id
parent_span_id nullable
source
source_event
agent_id nullable
tool_name nullable
input_digest nullable
fingerprint nullable
fingerprint_version nullable
mode
profile
risk
failure_policy
decision nullable
reason_code nullable
policy_version nullable
budget_before nullable
budget_after nullable
decision_latency_ms nullable
execution_latency_ms nullable
status nullable
progress
usage fields nullable
raw_input_stored = false
safe_metadata_json
policy_facts_json nullable
```

Host identifiers are stored as stable SHA-256 references, never as raw Codex IDs. `parent_span_id` remains nullable; the adapter must not invent ancestry that the host did not provide.

Token, cost, and usage fields remain nullable. An estimate is stored only with an explicit pricing version and is never presented as actual cost.

## Privacy-safe policy facts

Future counterfactual replay needs enough facts to rerun policy decisions, but it does not need raw prompts or tool arguments. `call.proposed` stores a validated, privacy-safe policy snapshot:

```json
{
  "fingerprint": "sha256:...",
  "fingerprint_version": 2,
  "budget_kind": "direct-tool",
  "requested_budget_limit": 6,
  "profile": "balanced",
  "risk": "medium",
  "mandatory_reason": null,
  "changed_strategy_present": false,
  "required_fields_present": true,
  "duplicate_scope": "turn",
  "state_token_digest": null
}
```

The snapshot excludes objective text, capability-gap text, expected-information text, stop-condition text, raw material inputs, and tool results. Future replay consumes the stored fingerprint and enumerated policy facts rather than reconstructing raw proposals.

Every policy decision records a stable machine-readable reason code and `policy_version`. The initial policy version is a source constant, not the package version.

## Database migration and concurrency

Use `PRAGMA user_version` and explicit ordered migrations. Opening a current `v0.2.0` database with `user_version = 0` and the known `call_events` shape performs one idempotent migration to schema v2.

Migration rules:

- run inside an exclusive migration transaction;
- add and backfill new columns without deleting old lifecycle rows;
- map legacy phases to the nearest canonical event type;
- mark existing SQLite fingerprints as version 2 because SQLite runtime ledgers first shipped with the v0.2 semantics;
- mark legacy policy decisions with a distinct legacy policy version;
- never fabricate missing `call.proposed` records for old sessions;
- reject an unknown future schema version with an actionable error; and
- make `doctor` report migration state before hooks begin writing.

Use WAL and a configurable busy timeout. Plugin hooks default to 1,000 ms to limit hook latency. Application-owned runtimes may opt into a longer timeout.

Policy evaluation, duplicate lookup, remaining-budget calculation, and reservation insertion remain inside one `BEGIN IMMEDIATE` transaction. The lifecycle-derived reservation model remains valid:

- `started` is reserved;
- `completed` or `failed` is consumed;
- pre-execution `cancelled` is released.

Add recovery for stale `started` reservations using an explicit age threshold and a `call.cancelled` recovery event. Never mutate or delete the original start event.

The stale threshold defaults to 24 hours and is configurable. Automatic recovery runs only after a later `SessionStart` for the same trace and only for reservations older than that threshold. A recovery event records `reason_code = stale_reservation_recovered`; setting the threshold to `0` disables automatic recovery.

## Progress semantics

Add `unknown` to the progress enum and make it the default for successful Codex tool and subagent completion.

Only record a stronger progress value when one of these is explicitly supplied or deterministically measured:

- exit code;
- file-change signal;
- test-result change;
- new unique source count;
- result digest change; or
- acceptance-criterion status.

`unknown` consumes an executed-call budget and participates in exact-duplicate detection, but does not trigger sufficient, no-progress, or low-progress stopping rules.

No runtime path invokes an LLM merely to classify progress.

## Codex hook lifecycle

The plugin bundles these events:

```text
SessionStart
PreToolUse
PostToolUse
SubagentStart
SubagentStop
Stop
```

Behavior:

- `SessionStart`: open or migrate the local ledger, apply retention, and append `session.started`.
- `PreToolUse`: normalize the proposal, calculate the v2 fingerprint, atomically record the decision and reservation, and optionally return a warning.
- `PostToolUse`: append one idempotent terminal event with `progress = unknown` unless deterministic evidence is present.
- `SubagentStart`: record lifecycle data and optional warning context; never claim it can stop the subagent.
- `SubagentStop`: append one idempotent terminal event.
- `Stop`: append `session.stopped` and run best-effort maintenance.

The bundled dispatcher is process-level fail-open:

- malformed input, missing runtime files, a locked database, migration failure, or an internal exception produces sanitized diagnostics;
- raw stdin and exception messages are never written to crash output;
- the dispatcher exits `0` without hook output on internal failure; and
- valid warn decisions return only documented `systemMessage` output.

This fail-open rule applies to the Codex plugin surface because the current hook contract is a guardrail and warning surface. It does not change the explicit fail-open/fail-closed behavior of application-owned wrappers.

## Configuration axes

Keep the four axes independent:

```text
mode: observe | warn
profile: strict | balanced | quality-first
risk: low | medium | high
failure_policy: fail-open
```

The bundled Codex plugin defaults to `observe`, `balanced`, `medium`, and `fail-open`. `quality-first` is a policy profile, never an execution mode. Application-owned runtimes retain `mode = enforce` and opt-in `failure_policy = fail-closed`; those values are not exposed as Codex plugin hook guarantees.

Plugin hook arguments provide the checked-in defaults. Environment variables may override profile, risk, retention, database path, and stale-reservation threshold. Invalid configuration fails open, emits a sanitized diagnostic, and does not silently fall back to a more restrictive policy.

## Privacy and security

Defaults:

```yaml
privacy:
  store_raw_prompt: false
  store_raw_tool_input: false
  store_raw_tool_output: false
  local_only: true
  retention_days: 7

redaction:
  api_keys: true
  authorization_headers: true
  home_directory: true
  emails: true
```

Implementation requirements:

- add a source-specific metadata allowlist;
- recursively redact strings before persistence;
- hash unknown custom string metadata instead of storing it verbatim;
- run the same sanitizer again during export;
- store error types, never exception messages;
- create the data directory with owner-only permissions where the platform supports POSIX modes;
- create the database with owner-only permissions where supported;
- keep all state under `PLUGIN_DATA` for an installed plugin, otherwise under `~/.codex/agent-call-governor`;
- never configure remote telemetry; and
- document that Windows inherited ACLs are checked best-effort rather than claiming POSIX-style guarantees.

Session deletion uses `PRAGMA secure_delete = ON`, a transaction-scoped delete, WAL checkpoint truncation, and database compaction outside the transaction. The CLI requires an explicit confirmation flag. The security guide explains that filesystem, backup, and SSD behavior can still retain historical blocks outside SQLite's guarantees.

Retention deletes only sessions whose latest event is older than the configured period. It never deletes an active session. Automatic retention runs best-effort at `SessionStart`; manual deletion remains available.

## CLI contract

Keep existing commands and add:

```bash
agent-call-governor-runtime doctor [--db PATH] [--plugin-root PATH] [--json]
agent-call-governor-runtime sessions [--db PATH] [--json]
agent-call-governor-runtime inspect SESSION_ID [--db PATH] [--json]
agent-call-governor-runtime delete-session SESSION_ID --yes [--db PATH]
agent-call-governor-runtime report [--db PATH] [--session SESSION_ID] [--json]
agent-call-governor-runtime export --format jsonl --output PATH [--session SESSION_ID]
```

Keep `export-jsonl` as a deprecated alias for one release.

`doctor` checks:

- supported Python version;
- plugin manifest, skill, hook config, and dispatcher presence;
- hook configuration JSON shape;
- data directory and database permissions;
- SQLite open, schema version, WAL, busy timeout, and a rolled-back write probe;
- privacy defaults, retention value, and remote telemetry disabled state; and
- whether a legacy live JSONL mirror is configured.

`sessions` lists hashed session references, first/last timestamps, call counts, event counts, and final lifecycle status. `inspect` emits sanitized event details in sequence order. Neither command reads transcript files.

## Version sources

Use the Python package `__version__` as the canonical product version and derive the wheel version from it through setuptools dynamic metadata. The plugin manifest necessarily repeats the version, so CI verifies exact equality rather than relying on manual review.

Keep `POLICY_VERSION`, `FINGERPRINT_VERSION`, and `SCHEMA_VERSION` separate from the package version.

## Documentation and open-source product work

Update English and Korean entry points together. Add:

- architecture and data-flow documentation;
- exact supported and unsupported hook paths;
- security, privacy, retention, and deletion behavior;
- benchmark methodology ordered by success, under-call, false blocks, then efficiency;
- plugin install, update, disable, uninstall, and legacy skill-only instructions;
- `CHANGELOG.md`, `CONTRIBUTING.md`, and `SECURITY.md`;
- a public roadmap that places replay after new-schema log collection; and
- a statement that current deterministic and small A/B results are directional evidence, not production benchmarks.

## Testing strategy

All behavior changes follow test-first development.

Required test groups:

1. Fingerprint v2
   - object-key order equivalence;
   - list-order and string-case distinction;
   - Bash direction and `cwd` distinction;
   - apply-patch exact side-effect duplication;
   - MCP argument canonicalization;
   - explicit volatile-ID removal only; and
   - persisted version checks.

2. Schema and migration
   - fresh schema v2 creation;
   - real v0.2 fixture migration from `user_version = 0`;
   - idempotent reopen;
   - unknown future-version rejection;
   - legacy event preservation; and
   - policy-facts privacy checks.

3. Multiprocess safety
   - concurrent hook subprocess appends;
   - concurrent duplicate delivery;
   - last-budget-slot competition;
   - 1-second lock timeout and fail-open dispatcher behavior;
   - stale reservation recovery; and
   - no malformed JSONL because plugin hooks do not write live JSONL.

4. Hook lifecycle
   - all six bundled events;
   - SessionStart and Stop idempotency;
   - raw host IDs absent;
   - successful completion defaults to unknown;
   - malformed payload exits zero through the bundled dispatcher; and
   - warning output uses only supported fields.

5. Privacy and security
   - API key, authorization header, home path, email, prompt, input, output, and exception-message canaries;
   - metadata allowlist behavior;
   - export re-redaction;
   - POSIX permission checks where applicable;
   - retention excludes active sessions; and
   - delete-session checkpoint/compaction behavior.

6. CLI and packaging
   - doctor healthy and failing diagnostics;
   - sessions/inspect/delete/report/export contracts;
   - plugin validator and skill validator;
   - marketplace and hooks JSON validation;
   - legacy installer smoke tests;
   - wheel and source build;
   - fresh plugin-path dispatch on Windows and Linux; and
   - package/plugin version equality.

Existing policy regression, runtime replay, Agents SDK adapter, concurrency, cancellation, and privacy-canary suites remain green.

## Acceptance criteria

`v0.3.0` is releasable only when all of the following are true:

- Codex discovers the repository as a plugin with its bundled skill and hooks.
- A clean plugin install records all six supported lifecycle events without a separate manual hooks file.
- Parallel hook processes cannot corrupt the database or overspend the final budget slot.
- Same-call-ID redelivery returns the previous decision and does not create a second logical decision.
- Fingerprint version 2 is stored and tool-aware false-positive fixtures pass.
- Existing v0.2 ledgers migrate without event loss.
- Hook internal failures do not stop the Codex call path.
- Raw prompts, tool inputs, outputs, host IDs, secrets, home paths, emails, and exception messages are absent from the DB and exports in canary tests.
- `doctor`, `sessions`, `inspect`, and `delete-session` work on Windows and Linux CI.
- Skill, plugin, marketplace, package, unit-test, and evaluation validation all pass.
- English and Korean documentation describe the same enforcement boundary.
- The release notes state that counterfactual replay is deferred until v0.4 and that current evaluations are not production benchmarks.

## Follow-on release

After v0.3 logs have been exercised in the deterministic 10-task instrumentation pilot, `v0.4.0` may add:

```text
explain <call-id>
policy-replay <session-id> --profile ...
compare <session-id> --profiles ...
```

Replay must consume only the stored privacy-safe policy facts, reproduce reason codes deterministically, and never re-execute a model or tool. Codex hook enforcement remains deferred until the official hook contract exposes and documents a reliable veto.
