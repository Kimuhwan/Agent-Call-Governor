---
name: agent-call-governor
description: Govern agent delegation and repeated tool use with least-call routing, explicit budgets, duplicate fingerprints, progress gates, and stop conditions. Apply before delegating to subagents, spawning parallel agents, retrying a tool or agent call, or planning a multi-call workflow; use especially when call cost, latency, or runaway delegation matters.
---

# Agent Call Governor

Use the minimum sufficient delegation that still completes the task correctly. Apply this policy in the current agent; never delegate to another agent merely to decide whether delegation is necessary.

## Route work from cheapest to strongest

Consider these routes in order and choose the first sufficient one:

1. Use the conversation, supplied materials, and local reasoning.
2. Use one direct deterministic tool, batching independent reads when supported.
3. Delegate one bounded task to one specialist agent.
4. Use multiple agents only for independent subproblems that benefit from meaningfully different capabilities and require reconciliation.

Do not move to a stronger route until the weaker route has a concrete capability gap.

Treat direct tools and agents differently. A file read, search, database query, or API lookup is a direct tool call when it returns the needed state itself. Do not wrap it in an agent unless interpretation or multi-step judgment is the actual gap.

## Test necessity before each call

Permit a call only when all of the following are recorded compactly:

- `capability_gap`: what cannot be completed from current context
- `expected_new_information`: the specific new state or result expected
- `cheapest_sufficient_route`: why this call is the least expensive sufficient route
- `remaining_budget`: calls available after this call
- `stop_condition`: the observable result that ends the search
- `normalized_call_fingerprint`: the stable identity of objective, route, and material inputs

Do not expose private reasoning. Share only brief, user-relevant progress updates.

Reject the call if the capability gap, expected result, or stop condition is vague. Default to no call when necessity is unclear.

## Use default budgets

Count agent calls separately from direct tool calls.

| Work type | Agent budget | Direct-tool guidance |
| --- | ---: | --- |
| Writing, rewriting, translation, formatting, or summarizing supplied content | 0 | Usually 0 |
| One current fact or state read | 0 | One batched lookup |
| One bounded specialist task | 1 | As needed for its evidence |
| Repository or multi-document investigation and implementation | 2 | Batch related reads |
| Clearly decomposable multi-domain work | 3 | Require explicit reconciliation |

Allow at most one handoff. Allow no identical retry. Raise a budget only for a system or developer requirement, an explicit user request, high-stakes verification, or genuinely independent decomposition. State the reason in a user-relevant progress update when the increase is material.

## Block duplicates

Normalize a proposed call from its objective, route or capability, and material inputs. Exclude timestamps, generated IDs, ordering that does not change meaning, and wording-only differences.

Reject a proposed call when:

- its normalized fingerprint matches a completed or pending call;
- it pursues the same objective with materially identical inputs;
- an earlier result already satisfies the stop condition;
- it is cosmetic validation without material uncertainty or risk; or
- it is unlikely to produce new information.

Permit one retry only when the query, parameters, source, or strategy changes materially. Record a new fingerprint and the changed assumption.

For complex or long-running workflows, run `python scripts/governor.py evaluate proposal.json` to apply deterministic budget and duplicate checks. Read [references/proposal-schema.md](references/proposal-schema.md) when preparing the JSON input. Do not run the script for a simple decision that is already obvious.

## Gate on progress

After every result, classify it as:

- `sufficient`: stop calling and complete the task;
- `material_progress`: continue only if a defined gap remains within budget;
- `low_progress`: allow at most one changed-strategy call;
- `no_progress`: stop delegation and provide the best supported result or report the blocker.

Recalculate the cheapest sufficient route after every result. Never continue merely to increase confidence cosmetically.

## Preserve mandatory calls

Do not suppress calls required for current or rapidly changing information, explicit verification, high-stakes factual checking, private account or connector access, missing file retrieval, explicit user-requested actions, safety requirements, or system and developer instructions.

Prefer a complete answer with clearly stated uncertainty over speculative extra calls.
