# Evaluation methodology

Agent Call Governor evaluates both failure directions:

- **under-calling:** a necessary lookup, verification, retry, or specialist is suppressed;
- **over-calling:** a duplicate, exhausted, already-satisfied, or no-progress call executes.

Call reduction counts as a win only when the chosen quality floor is preserved.

## 1. Deterministic policy regression

```bash
python evals/run_evals.py
```

This suite merges each case in `cases.json` with a common proposal and calls the same deterministic policy used by the CLI and runtime. It reports:

- `policy_accuracy`;
- `quality_preservation_rate`;
- `waste_control_rate`.

Latest checked-in result: **19/19**. See [results-2026-07-13.md](results-2026-07-13.md).

## 2. Enforce-mode runtime replay

```bash
python evals/run_runtime_evals.py
```

This suite replays every step in `runtime_workloads.json` through a real `GovernedRuntime`, temporary SQLite ledger, and `enforce` mode. It covers five workload families:

- mandatory calls after the ordinary budget is spent;
- high-risk changed-strategy retries under `strict`;
- exact duplicate blocking;
- stopping after a sufficient result;
- stopping after no progress.

The checked-in set contains **64 workloads and 128 call steps**. The runner reports necessary-call preservation, redundant-call blocking, duplicate blocking, under-call failures, decision reasons, and per-family pass rates.

Latest checked-in result: **64/64**, with **96/96 necessary calls preserved**, **32/32 redundant calls blocked**, and **0 under-call failures**. See [runtime-results-2026-07-14.md](runtime-results-2026-07-14.md).

## 3. Instrumentation pilot

```bash
python evals/run_instrumentation_evals.py
```

This local, deterministic pilot runs exactly ten checked-in unit and integration tests selected by `instrumentation_tasks.json`. It covers exact and changed fingerprints, a changed-strategy high-risk retry after low progress, multi-process budget contention, bounded locked-database handling, stale-reservation recovery, and SQLite reopen persistence.

Latest checked-in result: **10/10**. See [instrumentation-results-2026-07-15.md](instrumentation-results-2026-07-15.md). The pilot measures these selected instrumentation and deterministic-governance contracts only; it does not measure model response quality, production performance, cost savings, semantic near-duplicate detection, policy replay, or host-level firewall enforcement.

## What these scores mean

These are deterministic regression scores for the checked-in cases. They demonstrate that the current implementation behaves consistently on those inputs. They do not prove:

- a production task-success rate;
- a universal call-reduction percentage;
- model-independent generalization;
- latency or cost savings on a live workload; or
- complete host-level interception.

## Fresh-agent and production evaluation

For behavioral evaluation, give matched tasks to independent runs with and without the Governor. Record acceptance-criteria completion, direct-tool and agent calls, repeated fingerprints, latency, cost, and uncertainties. Blind the output judge to condition and call count.

For a production rollout, start in `observe`, establish a baseline, then move to `warn` and `enforce` only after reviewing would-block calls. Track at least:

- task success and acceptance-criteria completion;
- necessary-call preservation;
- duplicate/no-new-information calls;
- agent and direct-tool calls per task;
- latency and cost;
- policy or ledger failures;
- under-call incidents and manual overrides.

The first small matched Codex A/B trial is documented in [results-2026-07-13.md](results-2026-07-13.md). Treat it as directional evidence, not a production benchmark.
