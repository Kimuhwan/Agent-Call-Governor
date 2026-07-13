# Agent Call Governor

A Codex skill that applies a least-call policy before agent delegation and repeated tool use. It reduces redundant calls with explicit budgets, normalized fingerprints, progress gates, and observable stop conditions while preserving required verification and access.

## Install in Codex

Clone the repository, then copy or link the `agent-call-governor` directory into your Codex skills directory:

```powershell
git clone https://github.com/Kimuhwan/agent-call-governor.git
Copy-Item -Recurse .\agent-call-governor\agent-call-governor "$HOME\.codex\skills\agent-call-governor"
```

Restart Codex so it discovers the skill. Invoke it explicitly with:

```text
Use $agent-call-governor to complete this repository investigation with the minimum sufficient delegation.
```

The skill also allows implicit invocation when Codex is about to delegate, retry calls, or plan a multi-agent workflow.

## What it changes

The governor makes Codex choose in this order:

1. Current context and supplied materials
2. One direct deterministic tool
3. One specialist agent
4. Multiple agents only for independent, reconcilable subproblems

It keeps direct-tool and agent budgets separate, blocks materially identical calls, and stops when results are sufficient or further calls no longer make progress.

## Optional deterministic gate

For complex workflows, the bundled zero-dependency Python script evaluates a proposal ledger:

```powershell
python .\agent-call-governor\scripts\governor.py evaluate .\proposal.json
```

See [`references/proposal-schema.md`](agent-call-governor/references/proposal-schema.md) for the input and output contract. This tool complements the policy; it does not replace Codex's semantic judgment about whether a route is sufficient.

## Validate

```powershell
python -m unittest discover -s tests -v
python "$HOME\.codex\skills\.system\skill-creator\scripts\quick_validate.py" .\agent-call-governor
```

## License

MIT
