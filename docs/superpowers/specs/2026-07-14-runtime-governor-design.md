# Runtime Governor Design

## Status and goal

This design implements the approved next step after the v0.1.0 policy MVP: automatic call accounting plus enforceable pre-call and post-call wrappers, without claiming universal interception where the host runtime does not expose a veto.

The default remains quality-preserving: start in `observe`, keep `balanced` as the default profile, count real starts from an authoritative ledger, and make enforcement an explicit deployment choice.

## Approaches considered

1. **Wrapper-first core with host adapters — selected.** Keep the policy and ledger SDK-agnostic, enforce calls at application-owned wrapper boundaries, and use Codex/OpenAI adapters for the lifecycle surfaces each host actually exposes. This is portable, testable, and honest about coverage.
2. **Patch host internals.** Monkey-patch Codex or OpenAI runner internals to intercept everything. This offers broader apparent coverage but is version-fragile, difficult to audit, and unsafe as a public default.
3. **Trace-only observer.** Consume logs and traces after execution. This is low-risk and useful for measurement, but cannot stop duplicate or over-budget calls.

The implementation combines approach 1 with trace observation from approach 3. It does not patch host internals.

## Package and compatibility

Place the Python package under `agent-call-governor/scripts/agent_call_governor_runtime/`. The existing skill installer therefore carries the runtime source, while a root `pyproject.toml` also makes the same package installable with `pip install .`.

Keep Python 3.10+ and the standard library as the core dependency floor. Offer OpenAI Agents SDK support through an optional `agents` extra pinned to the verified lifecycle surface: `openai-agents>=0.18.2,<0.19`.

Move the current deterministic `evaluate` and `fingerprint` functions into the package and retain `agent-call-governor/scripts/governor.py` as a backward-compatible CLI wrapper. There is one policy implementation, not a copied runtime variant.

## Components

### Policy core

`policy.py` owns profiles, risk floors, proposal validation, fingerprints, and decisions. It preserves the v0.1.0 JSON contract and CLI exit codes. v0.2 fingerprint values intentionally preserve string case and list order to avoid false duplicate blocking; deployments must begin a new scope or recompute v0.1 history.

### Event model and authoritative ledger

`models.py` defines validated call proposals, decisions, handles, and lifecycle events. Each event contains a schema version, session and call IDs, phase, timestamps, route, fingerprint, budget kind, profile, risk, mode, policy decision, progress, duration, and sanitized metadata.

`ledger.py` uses SQLite as the authoritative cross-process store. A call counts once when its latest lifecycle reaches `started`, `completed`, or `failed`; `proposed`, `blocked`, and pre-execution `cancelled` final states do not consume budget. Queries are scoped to a session and return the existing governor history shape. An optional best-effort JSONL mirror makes records easy to inspect and stream without changing execution semantics if the mirror fails.

Raw objectives/prompts, tool arguments, and results are not stored by default. Objectives are reduced to SHA-256 references; material inputs are used to compute a fingerprint and then discarded. Metadata must be JSON-compatible and explicitly supplied.

### Runtime wrapper

`runtime.py` exposes `GovernedRuntime` with synchronous and asynchronous call helpers:

1. Build a policy document from the proposal and authoritative session history.
2. Record `proposed` and the effective decision.
3. Apply the configured behavior.
4. Record `started` immediately before invoking user code.
5. Record `completed` or `failed` with duration and progress.

Modes are:

- `observe`: always execute and record what policy would have done;
- `warn`: execute, record, and emit a warning for a would-block decision;
- `enforce`: raise `GovernanceBlocked` before application-owned execution.

Profiles remain separate: `strict`, `balanced`, and `quality-first`. Internal policy or ledger failures follow `fail-open` or `fail-closed`; a normal policy rejection in `enforce` is always blocked.

### Codex adapter

`codex_hook.py` reads official Codex command-hook JSON from stdin and writes lifecycle events for `PreToolUse`, `PostToolUse`, `SubagentStart`, and `SubagentStop`. It tolerates additional fields so later Codex versions do not break parsing, treats repeated delivery of one host call ID as idempotent, hashes host session/turn/call IDs before persistence, and scopes policy history to `turn_id` (or the individual call when no turn ID exists) rather than one long-lived Codex session.

Compatibility update (2026-07-15): the host supports denial for a supported `PreToolUse` call through `hookSpecificOutput.permissionDecision: "deny"`; `SubagentStart` still does not provide the same boundary. The adapter defined here deliberately does not emit the denial shape, supports `observe` and `warn` only, and emits a `systemMessage` when policy would reject a call. A generated example hook file is opt-in because Codex requires users to review and trust the current exact hook hash.

### OpenAI Agents SDK adapter

`agents_sdk.py` imports `agents` lazily and verifies the `openai-agents` distribution. It provides:

- workflow-wide `RunHooks` for agent, LLM, local-tool, and handoff accounting;
- a `GovernedRunner` facade that injects the hooks and records run admission/final state;
- an application wrapper for enforceable calls;
- a helper for supported `FunctionTool` input guardrails when per-tool rejection is required.

Lifecycle and tracing callbacks are observation surfaces, not a universal typed veto. Hosted tools and some specialized tool families lack one common pre-execution gate, so the documentation states their coverage explicitly.

## Error handling and concurrency

- Generate IDs with UUID4 and timestamps in UTC.
- Use one SQLite write transaction for history lookup, policy evaluation, and the `started`/`blocked` reservation so concurrent calls cannot share a stale budget snapshot.
- Keep indexes on `(session_id, call_id)` and `(session_id, fingerprint)`.
- Require a call ID to be unique inside its governance session so lifecycle grouping cannot be reused to undercount calls.
- Keep JSONL writes one event per UTF-8 line and guard in-process writes with a lock.
- Move async policy and ledger work to worker threads; if cancellation wins while a reservation is pending, finish the transaction and append a non-counting `cancelled` state before propagating cancellation.
- Re-raise the original application exception after recording `failed`.
- Default failed calls to `low_progress`, allowing only a materially changed retry under the existing policy.
- In `fail-open`, record an internal-error decision and execute. In `fail-closed`, record it and raise before execution.
- Never silently downgrade Codex `enforce` to a firewall; reject that configuration with a clear message or run it as explicitly named `warn`.

## Evaluation and release criteria

Use test-first development for every behavior. The release gate includes:

- existing policy unit and deterministic eval suites;
- ledger lifecycle, session isolation, duplicate, concurrency, privacy, mode, and failure-policy tests;
- Codex hook fixture tests and optional-import Agents SDK tests;
- a concrete replay suite of at least 60 workflow variants that reports necessary-call preservation, redundant-call blocking, duplicate blocking, and under-call rate;
- one fresh-context integration/forward test and one independent final code review;
- English and Korean documentation, migration notes, and explicit coverage limitations;
- green GitHub Actions, merged PR, and a `v0.2.0` release.

No result may be described as a production benchmark unless it comes from production traffic. Replay and forward-test results must retain those labels.
