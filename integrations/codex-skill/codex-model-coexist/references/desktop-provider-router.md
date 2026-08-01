# Retired Desktop JSON-RPC router

This document records only a migration boundary. The old JavaScript stdin shim
does not cover phone Remote and must not be installed. The native exact-version
App Server runtime supersedes it.

During repair, unload and back up these legacy assets if present:

- `com.example.codex-provider-router` LaunchAgent;
- `scripts/codex-provider-router` launcher;
- `scripts/provider-router.mjs` implementation.

Do not run an old uninstall helper when the current native launcher owns
`CODEX_CLI_PATH`; it can unset the active runtime environment. Use the reusable
runtime's migration cleanup or reversible disable operation instead.

Retain backups only for audit or recovery. Never use this historical mechanism
as evidence that current Desktop or phone routing works.
