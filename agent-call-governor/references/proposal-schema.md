# Proposal schema

Use this reference only for workflows complex enough to benefit from deterministic call gating.

## Input

Pass a JSON object to `scripts/governor.py evaluate`:

```json
{
  "profile": "balanced",
  "quality_risk": "high",
  "proposal": {
    "objective": "Inspect authentication failures",
    "route": "specialist-agent",
    "material_inputs": {
      "scope": "server/auth",
      "question": "Find the root cause"
    },
    "capability_gap": "The repository spans several authentication modules",
    "expected_new_information": "A source-backed root cause",
    "stop_condition": "A failing path is identified with file and line evidence"
  },
  "history": [
    {
      "fingerprint": "sha256-from-an-earlier-result",
      "budget_kind": "agent",
      "progress": "material_progress"
    }
  ],
  "budget": {
    "kind": "agent",
    "limit": 2,
    "used": 0
  }
}
```

Required proposal fields are `objective`, `route`, `capability_gap`, `expected_new_information`, and `stop_condition`. `material_inputs` defaults to an empty object.

Top-level fields:

- `profile`: `strict`, `balanced` (default), or `quality-first`;
- `quality_risk`: `low`, `medium` (default), or `high`;
- `history`: prior fingerprints and progress results;
- `budget.kind`: `agent` (default) or `direct-tool` so their ledgers stay separate;
- `budget.limit`: requested ceiling; the profile's risk floor raises it when it is unsafe;
- `budget.used`: calls already consumed in this ledger. When omitted, it is derived from matching history entries. When supplied, it cannot be lower than that derived count.

History progress values are `sufficient`, `material_progress`, `low_progress`, and `no_progress`. Set each entry's optional `budget_kind` to `agent` or `direct-tool`; an omitted value belongs to the currently evaluated ledger for backward compatibility. After `low_progress`, add a non-empty `proposal.changed_strategy` describing the material change.

For a required call, set `proposal.mandatory_reason` to one of:

- `current_information`
- `explicit_verification`
- `high_stakes`
- `private_state`
- `missing_file`
- `user_requested_action`
- `safety`
- `system_instruction`

A mandatory reason bypasses budget and progress stops, but never bypasses an identical fingerprint.

## Output

The command writes JSON with the decision, normalized fingerprint, remaining budget, selected profile and risk, budget kind, effective limit, whether a quality floor was applied, and any mandatory reason.

Exit status is `0` when allowed, `2` when rejected by policy, and `1` for invalid input.

## Fingerprint command

Generate a fingerprint without evaluating a budget:

```powershell
python scripts/governor.py fingerprint proposal.json
```

The command accepts either a proposal object directly or a wrapper containing `proposal`.
