# Security Architecture

Agent Call Governor v0.3.0 is local-first and minimizes persisted content. This document describes technical controls and remaining operator responsibilities. For vulnerability reporting, see the root [SECURITY.md](../SECURITY.md).

Compatibility note: hook trust behavior was checked against the official [Codex hook documentation](https://learn.chatgpt.com/docs/hooks.md) and [plugin documentation](https://learn.chatgpt.com/docs/build-plugins.md) on **2026-07-15**.

## Threat model

The design addresses accidental persistence of prompts or tool data, repeated host delivery, conflicting call identities, local database races, unsafe export targets, malformed input, and stale records. It assumes the local user, Codex host, Python interpreter, plugin checkout, and operating system are not already fully compromised.

It does not defend against an attacker with the user's account or arbitrary local code execution, malicious Python/runtime replacement, offline attacks on copied databases, compromised backups, or side channels outside the plugin process.

## Hook trust

Plugin installation does not authorize hook execution. Before use, open `/hooks`, review the hook content and current **exact hash**, and explicitly trust that exact version. Codex skips new or changed hook content until the user trusts its new hash. Do not bypass this review or infer trust from plugin installation.

The host can deny a supported `PreToolUse` call using `hookSpecificOutput.permissionDecision: "deny"`; the v0.3 Agent Call Governor hook deliberately never emits it. The plugin is observe/warn-only and process-level fail-open. For enforcement, applications must use an explicitly integrated wrapper or supported SDK guardrail.

## Data minimization and redaction

The dispatcher reads the host payload in memory and passes it through a source-specific normalizer. Persistence excludes raw objectives, prompts, tool arguments, tool results, transcripts, and exception messages. It stores canonical lifecycle facts, policy decisions, timing, bounded allowlisted metadata, and SHA-256 references for relevant identifiers and material-input identity.

Unknown diagnostic scalar fields are re-sanitized into hash references during inspection and export. Export is atomic, refuses database aliases and sidecars, and re-sanitizes every event. The plugin configures no live JSONL mirror.

Hashing is data minimization, not encryption. Low-entropy values may be guessed, and linkage across repeated references is intentional. Treat SQLite and exported JSONL as sensitive operational metadata.

## Database integrity and concurrency

SQLite is authoritative. Schema creation and v0.2 migration validate the exact expected schema before mutation. Policy evaluation and reservation commit transactionally, and uniqueness rules make repeated tool/subagent call deliveries idempotent. Conflicting reuse of one call ID does not overwrite the original record. Session-boundary events use the documented five-second delivery window when no stable turn identity is available, so a legitimate later resume is not collapsed forever.

The dispatcher uses a bounded busy timeout and fails open when the database is unavailable. This preserves Codex availability but can create an observability gap. `doctor` checks Python, plugin structure, hook configuration, permissions, SQLite schema, privacy defaults, and telemetry configuration.

## Permissions and paths

Database parent directories, temporary exports, and export files are created with owner-oriented permissions where the platform supports them. Export rejects symlinks, database sidecars, database hardlinks, path changes detected during creation, and protected-state aliases.

On Windows, inherited ACLs and enterprise policies make owner-only hardening and verification best effort. Operators should inspect ACLs for `PLUGIN_DATA`, an explicit `AGENT_CALL_GOVERNOR_DB`, and export destinations. Never place the database or exports in a public sync/share directory without an explicit risk decision.

## Retention and deletion

The default retention period is seven days. Best-effort pruning runs on `SessionStart` and `Stop`, deleting only closed traces older than the cutoff. Sessions without `session.stopped` remain active and require explicit `delete-session` cleanup.

Deletion enables SQLite secure deletion, checkpoints sidecars, and reclaims database space. If cleanup cannot complete, the CLI reports that a retry is required. Filesystem journals, SSD wear leveling, backups, snapshots, and already-created exports are outside this guarantee.

## Network and telemetry

The plugin does not configure remote telemetry and the runtime has no required third-party dependency. Marketplace installation, wheel installation, GitHub access, or an application integrating other SDKs can use the network independently; those are not plugin telemetry.

## Operator checklist

1. Install the root plugin from the intended repository and ref.
2. Review and trust the current hook exact hash through `/hooks`.
3. Keep the plugin checkout, Python interpreter, and `PLUGIN_DATA` writable only by intended users.
4. Set `AGENT_CALL_GOVERNOR_DB` before Codex launch if overriding the database, and pass the same `--db` to CLI commands.
5. Run `doctor`, inspect sessions, and verify retention behavior.
6. Protect or delete exports independently.
7. Re-review and re-trust after every hook change.
