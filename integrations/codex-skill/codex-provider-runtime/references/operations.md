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

`doctor` is local/read-only. `doctor --live` performs one ephemeral DeepSeek API
request. `disable` creates a fail-safe marker and takes effect after Desktop is
restarted. `uninstall` unloads support jobs and moves their plists to a backup;
it does not purge releases, credentials, or conversations.

On upstream mismatch or patch drift, retain the failure log and use the bundled
official backend. Never force an old custom release against a newer client.
