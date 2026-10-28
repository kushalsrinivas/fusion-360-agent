# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Report privately
using GitHub's [security advisory
form](https://github.com/kushalsrinivas/fusion-360-agent/security/advisories/new)
or email **kushal.s.2005@gmail.com**.

Include the affected version, a description of the issue, and reproduction
steps if possible. You can expect an acknowledgement within a few days and a
coordinated disclosure once a fix is available.

## Scope

Examples of in-scope issues:

- Leaked API keys, tokens, or credentials in the repository or its history.
- Command or argument injection through MCP command configuration, plans, or
  tool calls.
- Path traversal or unsafe file writes in export/storage code.
- Bypassing the approval gate or the destructive-operation guard.
- Unintended destructive CAD operations that the safety layer should have
  blocked.

## Handling secrets

The agent reads API keys from environment variables or a local `.env` file.
`.env` is git-ignored and must never be committed. If a key is exposed,
revoke it with the provider immediately and rotate it; removing the commit is
not sufficient once a secret has been pushed.

## Safety note

Live mode can mutate or delete an open Fusion design. Use `mock` or
`faust-mock` when evaluating the project, keep Fusion backups enabled, and
review destructive steps before approving them.
