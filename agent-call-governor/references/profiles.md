# Profiles and quality floors

Use one profile per workflow. Budgets are upper bounds for ordinary calls, while the risk floor prevents an unrealistically low configured limit from causing under-calling.

## Profile selection

| Profile | Use for | Agent limits by risk | Direct-tool limits by risk | Changed-strategy retries after low progress |
| --- | --- | --- | --- | ---: |
| `strict` | Low-risk, reversible, latency-sensitive work | low 0 · medium 1 · high 2 | low 1 · medium 3 · high 5 | 0 |
| `balanced` | Default product and engineering work | low 0 · medium 2 · high 3 | low 1 · medium 6 · high 8 | 1 |
| `quality-first` | High-impact, uncertain, or costly-to-correct work | low 1 · medium 3 · high 4 | low 2 · medium 8 · high 12 | 2 |

These limits guide complex workflows. They do not justify an agent call when the task is already solvable from context or one direct tool.

High-risk work always receives at least one materially changed-strategy retry, including under `strict`. This risk floor prevents an inconclusive first attempt from terminating medical, legal, security, financial, or production work while safe budget remains.

## Risk selection

- `low`: reversible output, supplied evidence, and cheap error correction.
- `medium`: ordinary implementation or investigation with meaningful acceptance criteria.
- `high`: medical, legal, financial, security, production, or other costly and hard-to-reverse consequences; also use when independent verification can catch a material error.

## Precedence

Apply rules in this order:

1. Block the exact duplicate fingerprint.
2. Allow a valid mandatory exception, even beyond the numeric budget.
3. Stop after a sufficient or no-progress result.
4. Enforce the profile's changed-strategy retry allowance.
5. Enforce the effective budget after applying the quality-risk floor.

This order prevents repeated side effects while ensuring that cost controls do not suppress required verification or actions.
