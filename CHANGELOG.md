# Changelog

All notable changes to Agent Call Governor are documented here.

## [0.3.0] - 2026-07-15

### Added

- Root Codex plugin packaging with `SessionStart`, `PreToolUse`, `PostToolUse`, `SubagentStart`, `SubagentStop`, and `Stop` hooks.
- Local schema-v2 SQLite observability, session inspection, reporting, sanitized export, secure deletion, retention, and seven-check doctor commands.
- Fingerprint v2, source-aware redaction, safe v0.2 migration, transactional policy decisions, and multiprocess delivery handling.
- English and Korean plugin onboarding plus architecture, limitations, security, benchmark, contribution, and vulnerability-reporting documentation.
- A mechanically rendered 10/10 instrumentation pilot and tracked-file-only release packaging with deterministic checksums.

### Changed

- Moved the skill under `skills/agent-call-governor` so the repository root is the installable plugin.
- Made SQLite the sole authoritative plugin record. JSONL is export and legacy compatibility only.
- Defined the installed Codex hook as observe/warn-only while retaining enforcement in application-owned wrappers and supported SDK guardrails.
- Began a new exact-duplicate epoch for fingerprint v2; migrated legacy-v1 rows still contribute budget and progress history.

### Security

- Raw hook payloads are excluded from persistence, host identifiers are hashed, export targets are hardened, and hook failures report only exception types.
- Hook installation and exact-hash trust are documented as separate user decisions.

## [0.2.0] - 2026-07-14

### Added

- Installable Python runtime with synchronous and asynchronous governed-call wrappers.
- Authoritative SQLite ledger, optional compatibility JSONL output, reports, and OpenAI Agents SDK adapters.
- Observe, warn, and application-owned enforce modes with explicit failure policy.

### Changed

- Preserved string case and list order in call fingerprints to avoid false duplicate matches.
- Documented the boundary between prompt policy, wrapper enforcement, SDK guardrails, and Codex lifecycle observation.

## [0.1.0] - 2026-07-14

### Added

- Initial Agent Call Governor Codex skill and deterministic proposal CLI.
- Strict, balanced, and quality-first profiles with separate direct-tool and agent budgets.
- Quality floors for mandatory calls, explicit verification, private or fresh state, and high-risk changed-strategy retries.
- Deterministic policy cases and an initial small matched A/B evaluation.
