# Contributing

Thank you for improving Agent Call Governor. Changes should preserve task quality, local privacy, deterministic behavior, and honest public claims.

## Setup

Requirements are Python 3.10+ and Git. Clone the repository, create an isolated environment if desired, and install development dependencies:

```bash
git clone https://github.com/Kimuhwan/Agent-Call-Governor.git
cd Agent-Call-Governor
python -m pip install -e ".[dev]"
```

The core runtime has no required third-party runtime dependency. Development validation uses PyYAML, jsonschema, and build through the `dev` extra.

## Test-driven workflow

1. Write the smallest focused test that describes the intended contract.
2. Run it and confirm it fails for the expected missing behavior, not a broken fixture.
3. Implement the smallest coherent change.
4. Re-run the focused test, related suites, and the complete verification gate.
5. Refactor only while tests remain green.

Tests must cover both failure directions: wasteful over-calling and harmful under-calling. Concurrency, migration, privacy, and cleanup changes need an adversarial case in addition to the happy path.

## Privacy testing

Use unique canary strings to prove raw objectives, host IDs, tool arguments, results, exception messages, and unexpected metadata do not survive in SQLite, WAL/SHM sidecars, exports, stdout, or stderr. Never use real credentials, user data, or production databases in a test.

Privacy behavior must fail closed at the persistence boundary even though the Codex dispatcher intentionally fails open for host availability. Error output may expose only a safe exception type, not an exception message or raw input.

## Full verification

Run these before opening a pull request:

```bash
python -m unittest tests.test_plugin_package -v
python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/agent-call-governor
python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
python -m unittest discover -s tests -v
python evals/run_evals.py
python evals/run_runtime_evals.py
python -m build
git diff --check
```

Platform-specific validator paths are acceptable. Do not make automated CI depend on an interactive Codex installation or bypass hook trust for a smoke test.

## Documentation and evidence

Keep English and Korean onboarding semantically matched and command blocks identical. Link host-contract claims to official sources and date compatibility checks. Distinguish deterministic instrumentation, policy accuracy, directional A/B evidence, and production task quality. Do not present observe/warn telemetry as call blocking.

## Pull request checklist

- [ ] The change has a focused RED test and the test now passes.
- [ ] Over-call and under-call risks were considered.
- [ ] Raw-input privacy canaries do not survive persistence, export, or process output.
- [ ] Schema, migration, concurrency, and retention compatibility were assessed where relevant.
- [ ] Public docs match behavior in both languages.
- [ ] No secret, local database, JSONL export, environment file, or generated private state is included.
- [ ] Full unit tests, deterministic evals, validators, build, and `git diff --check` pass.
- [ ] Claims are limited to reproducible evidence.

## Security reports

Do not disclose a vulnerability or secret in a public issue or pull request. Follow [SECURITY.md](SECURITY.md), which explains the current reporting limitation.
