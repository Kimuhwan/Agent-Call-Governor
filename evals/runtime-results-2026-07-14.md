# Runtime replay evaluation — 2026-07-14

## Result

| Metric | Result |
| --- | ---: |
| Workloads passed | 64 / 64 |
| Call steps | 128 |
| Executed necessary steps | 96 / 96 |
| Blocked redundant steps | 32 / 32 |
| Blocked exact duplicates | 16 / 16 |
| Under-call failures | 0 |
| Unexpected redundant executions | 0 |

All five workload families passed at 100%: mandatory preservation, high-risk
retry preservation, duplicate blocking, satisfied-stop blocking, and
no-progress blocking.

Decision totals were 80 ordinary allows, 16 mandatory exceptions, 16 duplicate
blocks, 8 already-satisfied stops, and 8 no-progress stops.

## Reproduce

```bash
python -m pip install -e .
python evals/run_runtime_evals.py
```

The runner creates a fresh temporary SQLite ledger for every workload and sends
every step through `GovernedRuntime(mode="enforce", failure_policy="fail-closed")`.
It exits non-zero on any expected execution, block, or reason mismatch.

## Coverage

The 64 checked-in workloads are evenly or deliberately varied across:

- all eight mandatory exception reasons after a strict ordinary budget is spent;
- high-risk changed-strategy retries under the strict profile;
- exact direct-tool duplicate fingerprints;
- changed calls after a sufficient result; and
- changed calls after a no-progress result.

## Interpretation limits

This is a deterministic replay regression, not a production benchmark. It does
not measure live model quality, cost, latency, or a universal interception rate.
The 100% figures apply only to the checked-in workload set. Production rollout
must still begin in observe mode and measure task success and under-calling.
