# Agent Call Governor

[한국어](README.ko.md) · **English**

[![Validate](https://github.com/Kimuhwan/Agent-Call-Governor/actions/workflows/validate.yml/badge.svg)](https://github.com/Kimuhwan/Agent-Call-Governor/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Codex Skill](https://img.shields.io/badge/Codex-Skill-111827)](agent-call-governor/SKILL.md)

Use fewer agent calls without sacrificing correctness.

Agent Call Governor is a lightweight [Codex skill](agent-call-governor/SKILL.md) that applies a least-call policy before delegation, retries, and multi-agent workflows. It combines explicit call budgets, duplicate fingerprints, progress gates, and observable stop conditions.

## Why use it?

Complex models can over-decompose simple work, repeat calls that add no information, or delegate direct lookups to another agent. This skill gives Codex a consistent decision order:

```mermaid
flowchart LR
    A[Current context] -->|insufficient| B[Direct tool]
    B -->|capability gap remains| C[One specialist]
    C -->|independent subproblems| D[Multiple agents]
    A -->|sufficient| E[Complete task]
    B -->|sufficient| E
    C -->|sufficient| E
```

Direct tools and agent calls are budgeted separately. Required calls for freshness, verification, safety, private state, or explicit user actions are never suppressed.

## Install

### Windows PowerShell

```powershell
git clone https://github.com/Kimuhwan/Agent-Call-Governor.git
powershell -ExecutionPolicy Bypass -File .\Agent-Call-Governor\install.ps1
```

### macOS or Linux

```bash
git clone https://github.com/Kimuhwan/Agent-Call-Governor.git
./Agent-Call-Governor/install.sh
```

Restart Codex after installation. The skill is installed at `~/.codex/skills/agent-call-governor`.

## Use

Invoke it explicitly:

```text
Use $agent-call-governor to investigate this repository with the minimum sufficient delegation.
```

It can also trigger implicitly when Codex is about to delegate, retry a call, or plan a multi-agent workflow.

## How decisions are made

Before a call, Codex identifies the capability gap, expected new information, cheapest sufficient route, remaining budget, stop condition, and normalized fingerprint. After every result it classifies progress:

| Result | Behavior |
| --- | --- |
| `sufficient` | Stop and complete the task |
| `material_progress` | Continue only for a defined remaining gap |
| `low_progress` | Allow one changed-strategy attempt |
| `no_progress` | Stop delegation and report the best supported result |

See [SKILL.md](agent-call-governor/SKILL.md) for the complete policy.

## Optional deterministic gate

For long-running workflows, the zero-dependency Python helper can block duplicate or over-budget proposals:

```bash
python agent-call-governor/scripts/governor.py evaluate examples/proposal.json
```

The command returns exit code `0` when allowed, `2` when rejected by policy, and `1` for invalid input. See the [proposal schema](agent-call-governor/references/proposal-schema.md) for details.

## Validate locally

```bash
python -m unittest discover -s tests -v
python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py agent-call-governor
```

The policy itself has no runtime dependencies. The official Codex skill validator requires PyYAML.

## Project layout

```text
agent-call-governor/     Codex-discoverable skill
  agents/openai.yaml     Codex UI metadata
  references/            On-demand proposal schema
  scripts/governor.py    Optional deterministic gate
examples/                Ready-to-run proposal input
tests/                   Standard-library unit tests
```

## Contributing

Issues and pull requests are welcome. Keep the core skill concise, add deterministic behavior to the helper when possible, and include tests for policy changes.

## License

[MIT](LICENSE)
