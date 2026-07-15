# Limitations

Agent Call Governor v0.3.0 is a policy and local-observability release. These boundaries are part of the product contract.

## Codex hook enforcement

As checked on **2026-07-15**, the host Codex contract supports denying a supported `PreToolUse` call by returning `hookSpecificOutput.permissionDecision: "deny"`. See the official [hook contract](https://learn.chatgpt.com/docs/hooks.md).

The Agent Call Governor v0.3 plugin intentionally does not return that denial shape. Its bundled hook is **observe/warn-only**, rejects `enforce` configuration, and fails open if recording is unavailable. It may surface a supported warning, but it is not a firewall. Application-owned wrappers and supported SDK guardrails are separate enforcement surfaces.

## Duplicate detection

Fingerprint v2 represents objective, canonical route, working directory, tool version, optional state token, and material-input identity. The policy partitions matching history by budget kind separately. It detects exact duplicates under its current schema; it does not infer semantic equivalence, fuzzy similarity, intent overlap, or near-duplicates. Different canonical inputs are treated as different calls even when a person might consider them redundant.

Upgrading from v0.2 creates an exact-duplicate epoch boundary. Migrated legacy-v1 rows still count toward budget and progress history, but they are not exact fingerprint-v2 duplicate candidates. The first matching v2 call can therefore proceed before later identical v2 calls are detected.

## No policy replay yet

Schema-v2 records make decisions inspectable, but v0.3 does not replay a historical decision against a newer policy or reconstruct a model task. Policy replay is deferred until enough schema-v2 evidence exists and compatibility rules can be specified safely.

## Incomplete host measurements

Usage, tokens, and cost are nullable. The plugin records them only when a source supplies a trustworthy value; it does not invent estimates. Host progress evidence can also be absent. Unknown progress is neutral—it is not automatically success or failure.

The currently available deterministic and small matched A/B results are directional evidence. Any instrumentation result is also directional and should be cited only after its runner and dated output are checked in. These results do not establish production task success, cost savings, latency improvement, or generalization across models and workloads.

## Retention and deletion

Automatic retention defaults to seven days and runs during `SessionStart` and `Stop`. It removes only closed traces whose last event is older than the cutoff. A crashed or interrupted session without `session.stopped` is conservatively treated as active and skipped indefinitely by automatic retention until an operator uses `delete-session`.

SQLite secure deletion and space reclamation reduce residual local content, but the project cannot guarantee erasure from SSD remapping, filesystem journals, backups, snapshots, forensic copies, or previously exported JSONL files.

## Local security boundaries

Raw hook payloads are not persisted, and source-aware redaction restricts metadata. SHA-256 references are pseudonymous identifiers, not encryption; low-entropy inputs can be guessable. Operators must protect the database and sanitized exports.

POSIX owner-only permissions are applied where supported. Windows ACL checks and hardening are best effort because inherited and enterprise ACL policies vary. A passing doctor check is not a substitute for an operating-system security review.

## Operational boundaries

- The hook depends on a working local Python 3.10+ interpreter and writable plugin-data directory.
- Hook failures deliberately fail open and report only the exception type; recording completeness is therefore not guaranteed during local failures.
- Repeated tool/subagent call delivery with a stable host call ID is collapsed. `SessionStart` and `Stop` without a turn ID use a bounded five-second delivery window, and the plugin does not reconstruct events the host never delivered.
- Warnings do not prove that the host call was prevented.
- Global CLI commands require the separate companion wheel.
- Installing only the compatibility skill does not configure hooks or create event data.
- Root plugin removal does not remove the wheel, compatibility skill, SQLite data, or prior exports.
