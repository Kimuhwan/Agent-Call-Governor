# Proposal schema

Use this reference only for workflows complex enough to benefit from deterministic call gating.

## Input

Pass a JSON object to `scripts/governor.py evaluate`:

```json
{
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
      "progress": "material_progress"
    }
  ],
  "budget": {
    "limit": 2,
    "used": 0
  }
}
```

Required proposal fields are `objective`, `route`, `capability_gap`, `expected_new_information`, and `stop_condition`. `material_inputs` defaults to an empty object. `history` and `budget` are optional.

`route` is typically `direct-tool`, `specialist-agent`, or `multi-agent`. The script does not decide whether the chosen route is semantically appropriate; apply the routing policy in `SKILL.md` first.

History progress values are `sufficient`, `material_progress`, `low_progress`, and `no_progress`.

## Output

The command writes JSON with:

- `allowed`: whether to make the proposed call;
- `reason`: stable machine-readable decision reason;
- `fingerprint`: normalized SHA-256 identity for the proposal;
- `remaining_after_call`: remaining call budget if allowed, otherwise current remaining budget.

Exit status is `0` when allowed, `2` when rejected by policy, and `1` for invalid input.

## Fingerprint command

Generate a fingerprint without evaluating a budget:

```powershell
python scripts/governor.py fingerprint proposal.json
```

The command accepts either a proposal object directly or a wrapper containing `proposal`.
