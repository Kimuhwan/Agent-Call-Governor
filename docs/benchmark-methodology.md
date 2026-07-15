# Benchmark Methodology

Agent Call Governor is evaluated against both over-calling and under-calling. A reduction in call count is not a success when it lowers task quality.

## Metric priority

Results must be interpreted in this order:

1. **Task success** — did the run satisfy the task's acceptance criteria?
2. **Under-call rate** — how often was a necessary lookup, specialist, retry, or verification suppressed?
3. **False-block rate** — how often did policy mark a necessary call as blocked or would-block?
4. **Call efficiency** — after the first three quality gates hold, how many duplicate, exhausted, or no-new-information calls were avoided?

Latency, token usage, and cost are useful secondary measures when the source provides trustworthy values. They may be null and must never be silently estimated.

## Deterministic policy regression

`evals/run_evals.py` evaluates checked-in proposals against expected policy outcomes. Cases cover mandatory work, budgets, exact duplicates, sufficient results, no progress, and risk-aware retries. It protects both necessary-call preservation and waste control, but it is not a model or production benchmark.

## Runtime replay

`evals/run_runtime_evals.py` sends checked-in workloads through a real `GovernedRuntime` and temporary SQLite ledger. It measures whether enforcement preserves necessary calls and blocks expected redundant steps under deterministic inputs. Replay results prove regression behavior for those cases only.

## Instrumentation pilot

The planned v0.3 ten-case instrumentation pilot exercises fingerprinting, six-event normalization, redaction, SQLite persistence, migration, duplicate delivery, concurrency, CLI reporting, retention, and failure behavior. Publish a result only with its deterministic runner, exact ten-case input set, environment, date, and empty-or-explicit failure list. It is designed to measure instrumentation and deterministic governance accuracy; it does not measure model response quality.

## Matched behavioral studies

A behavioral study should assign the same task set to independent control and Governor runs, pin relevant model/tool configuration, and blind an output judge to condition and call count. Record acceptance-criteria completion before looking at efficiency. Then record necessary calls, false blocks or would-blocks, exact duplicate fingerprints, direct-tool and agent calls, latency, and trustworthy usage/cost.

Small matched studies are directional. They require more tasks, independent repetitions, confidence intervals, and representative production workloads before supporting general claims.

## Reporting rules

- Publish the exact runner, inputs, environment, date, and failures.
- Separate observe/warn would-block results from actual application-wrapper enforcement.
- Report missing values as null or unknown.
- Keep policy accuracy separate from task success.
- Do not extrapolate deterministic percentages into production savings.
- Treat migrated legacy-v1 history and fingerprint-v2 history as different exact-duplicate epochs.
- Investigate every under-call or false block before optimizing call efficiency.

## Production rollout

Start with observe data, establish a task-success and call baseline, then review warn-mode would-blocks. Move an application-owned surface to enforcement only after the under-call and false-block guardrails meet a predeclared threshold and a rollback path exists. Continue auditing by task family and risk level; aggregate averages can hide high-impact regressions.
