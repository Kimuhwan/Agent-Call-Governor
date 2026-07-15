# v0.3 Instrumentation Pilot — 2026-07-15

## Scope

This deterministic pilot selects ten checked-in unit and integration tests for fingerprinting, policy retries, multi-process budget contention, bounded dispatcher failure, recovery, and SQLite reopen persistence. It makes no network, browser, external API, or model call.

## Environment

- Base commit (working-tree evaluation): `26271c409c3a5d5d0a803492958f281f59ec1274`
- Operating system: `Windows`
- Python: `3.14.3`
- Run date: `2026-07-15`
- Selection: exactly ten unique manifest records, each resolving to one non-skipped unittest

## Results

- Total: **10**
- Passed: **10**
- Failed: **0**

| # | Case | Expected | State | Test ID | Measured safe detail |
| ---: | --- | --- | --- | --- | --- |
| 1 | same-file-read-twice | same_fingerprint | PASS | tests.test_fingerprint.FingerprintTests.test_exact_file_read_repeats_same_fingerprint | expected_contract=same_fingerprint; test_result=pass; test=tests.test_fingerprint.FingerprintTests.test_exact_file_read_repeats_same_fingerprint; elapsed_ms=45.319 |
| 2 | changed-state-token | new_fingerprint | PASS | tests.test_fingerprint.FingerprintTests.test_exact_patch_and_state_token_are_material | expected_contract=new_fingerprint; test_result=pass; test=tests.test_fingerprint.FingerprintTests.test_exact_patch_and_state_token_are_material; elapsed_ms=0.358 |
| 3 | same-bash-command-twice | same_fingerprint | PASS | tests.test_fingerprint.FingerprintTests.test_exact_bash_repeat_same_fingerprint | expected_contract=same_fingerprint; test_result=pass; test=tests.test_fingerprint.FingerprintTests.test_exact_bash_repeat_same_fingerprint; elapsed_ms=0.274 |
| 4 | bash-direction-change | new_fingerprint | PASS | tests.test_fingerprint.FingerprintTests.test_bash_direction_and_cwd_are_material | expected_contract=new_fingerprint; test_result=pass; test=tests.test_fingerprint.FingerprintTests.test_bash_direction_and_cwd_are_material; elapsed_ms=0.265 |
| 5 | mcp-object-key-order | same_fingerprint | PASS | tests.test_fingerprint.FingerprintTests.test_object_key_order_is_equivalent | expected_contract=same_fingerprint; test_result=pass; test=tests.test_fingerprint.FingerprintTests.test_object_key_order_is_equivalent; elapsed_ms=0.268 |
| 6 | high-risk-low-progress-changed-strategy-retry | retry_allowed | PASS | tests.test_governor.GovernorTests.test_strict_high_risk_allows_one_changed_strategy_retry | expected_contract=retry_allowed; test_result=pass; test=tests.test_governor.GovernorTests.test_strict_high_risk_allows_one_changed_strategy_retry; elapsed_ms=0.738 |
| 7 | parallel-final-budget-slot | one_allow_one_would_block | PASS | tests.test_multiprocess_hooks.MultiprocessHookTests.test_two_processes_compete_for_one_policy_budget_slot | expected_contract=one_allow_one_would_block; test_result=pass; test=tests.test_multiprocess_hooks.MultiprocessHookTests.test_two_processes_compete_for_one_policy_budget_slot; elapsed_ms=1378.284 |
| 8 | locked-database-dispatch | zero_exit_under_three_seconds | PASS | tests.test_multiprocess_hooks.MultiprocessHookTests.test_exclusive_database_lock_returns_zero_within_three_seconds | expected_contract=zero_exit_under_three_seconds; test_result=pass; test=tests.test_multiprocess_hooks.MultiprocessHookTests.test_exclusive_database_lock_returns_zero_within_three_seconds; elapsed_ms=1276.900 |
| 9 | stale-reservation-recovery | append_cancelled | PASS | tests.test_ledger_operations.LedgerOperationTests.test_stale_recovery_appends_cancellation_without_mutating_start | expected_contract=append_cancelled; test_result=pass; test=tests.test_ledger_operations.LedgerOperationTests.test_stale_recovery_appends_cancellation_without_mutating_start; elapsed_ms=20.895 |
| 10 | sqlite-reopen-persistence | history_preserved_after_reopen | PASS | tests.test_runtime_ledger.RuntimeLedgerTests.test_reopening_sqlite_preserves_events | expected_contract=history_preserved_after_reopen; test_result=pass; test=tests.test_runtime_ledger.RuntimeLedgerTests.test_reopening_sqlite_preserves_events; elapsed_ms=19.067 |

## Failures

None. The measured runner returned `failures: []`.

## Interpretation

This pilot tests instrumentation and deterministic governance accuracy; it does not measure model response quality.

The result is directional evidence for these ten selected contracts only. It is not a production benchmark, proof of firewall enforcement, semantic or near-duplicate detection, policy replay, cost savings, latency improvement, or model-answer quality. Agent Call Governor v0.3's bundled Codex hook remains observe/warn-only even though the host supports denial for a supported `PreToolUse` call.
