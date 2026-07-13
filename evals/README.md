# Evaluation methodology

The evaluation suite protects both sides of the policy:

- `quality_preservation` cases verify that risk floors and mandatory exceptions prevent under-calling.
- `waste_control` cases verify that duplicates, exhausted retries, satisfied stop conditions, and spent budgets are blocked.

Run:

```bash
python evals/run_evals.py
```

The runner merges each case with a common proposal, evaluates it through the same `governor.py` used by the skill, and checks the expected decision and reason. Any mismatch exits non-zero and fails CI.

## What the score means

`policy_accuracy`, `quality_preservation_rate`, and `waste_control_rate` measure deterministic rule consistency on the checked-in regression cases. They do not prove an end-to-end task success rate, a production call-reduction percentage, or model-independent generalization.

Use fresh-agent scenario evaluations and real workload telemetry to measure those outcomes. Compare at least:

- task success and acceptance-criteria completion;
- agent and direct-tool calls per task;
- duplicate or no-new-information calls;
- latency and cost;
- under-call failures where a necessary lookup, verification, or specialist was suppressed.

Treat call reduction as a win only when task success remains within the chosen quality tolerance.

## Matched Codex A/B trials

For a behavioral trial, give identical tasks to independent Codex runs with and without the Governor. Record completed tasks, direct-tool calls, subagent calls, identical retries, and uncertainties. Give anonymized outputs to a third evaluator that cannot see the execution condition or call counts.

Report the sample size and do not generalize a single run into a production savings claim. See [the latest results](results-2026-07-13.md) for the first checked-in trial.
