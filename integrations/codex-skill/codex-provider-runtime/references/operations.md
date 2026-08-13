# Runtime operations

The standalone CLI owns the lifecycle:

```bash
codex-provider prerequisites
codex-provider keychain-set
codex-provider configure
codex-provider install
codex-provider status
codex-provider doctor [--live]
codex-provider update
codex-provider verify
codex-provider disable
codex-provider enable
codex-provider uninstall
```

`install` configures coexistence, builds the exact matching official Codex tag,
tests the native patch, installs generic LaunchAgents, and activates the stable
launcher. It may download source and compile Rust, so use it only for an
explicit install request.

The build fetches the exact upstream lock-file dependencies before its offline
workspace-version normalization. It then rejects any lock diff beyond expected
workspace package version changes, so a newly introduced Git dependency can be
cached without weakening the fail-closed dependency contract.

`doctor` is local/read-only. `doctor --live` performs one ephemeral DeepSeek API
request. `disable` creates a fail-safe marker and takes effect after Desktop is
restarted. `uninstall` unloads support jobs and moves their plists to a backup;
it does not purge releases, credentials, or conversations.

On upstream mismatch or patch drift, retain the failure log and use the bundled
official backend. Never force an old custom release against a newer client.

Before adding a model or changing transport, compare current official Codex and
provider documentation with a minimal direct API probe and an App Server
structured-tool smoke. Treat the compatibility matrix as dated evidence. Once
the new path passes, update its positive/negative route tests, catalog contract,
release patch identifier, documentation, and skill baseline in the same change.
