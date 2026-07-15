# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.3.x | Yes |
| 0.2.x and earlier | No; upgrade before requesting a fix |

## Reporting a vulnerability

Do **not** put vulnerability details, exploit steps, credentials, personal data, logs, databases, or other secrets in a public issue, discussion, or pull request.

This repository does not currently have GitHub private vulnerability reporting enabled, and no alternative private security mailbox is published. To request a private reporting channel, open a public issue containing only a request for private maintainer contact and the affected version—include no vulnerability details. Wait for the maintainer to provide a private channel before sharing technical information.

If you cannot request a channel without exposing sensitive information, retain the report until a private route is available. Do not send secrets through an unverified account or address.

There is no fixed response or remediation SLA. Maintainers will handle a received private report on a best-effort basis, validate impact, coordinate a fix and release where appropriate, and credit the reporter if requested and safe.

## Scope

Reports may cover the root Codex plugin, hooks, companion Python runtime, SQLite persistence and migration, CLI/export behavior, installers, release packaging, or supported SDK adapters. General policy suggestions, feature requests, and non-sensitive bugs belong in ordinary public issues.

## Safe research

Use only systems and data you own or are authorized to test. Avoid production data, privacy violations, service disruption, destructive actions, and persistence beyond what is needed to demonstrate the issue. A concise reproduction using synthetic canary data is preferred.
