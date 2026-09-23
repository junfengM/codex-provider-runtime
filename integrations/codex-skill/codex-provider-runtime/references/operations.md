# Runtime operations

The standalone CLI owns the lifecycle:

```bash
codex-provider prerequisites
codex-provider keychain-set
codex-provider configure
codex-provider sync-models
codex-provider install
codex-provider status
codex-provider doctor [--live]
codex-provider update
codex-provider cleanup
codex-provider verify
codex-provider disable
codex-provider enable
codex-provider uninstall
```

`install` configures coexistence, builds the exact matching official Codex tag,
tests the native patch, installs generic LaunchAgents, and activates the stable
launcher. It may download source and compile Rust, so use it only for an
explicit install request.

An unattended failure writes a fingerprinted backoff marker. Scheduled runs
skip the unchanged Codex-binary/provider-patch combination; a changed binary or
patch retries automatically, and manual `update` always forces a retry. After a
successful activation, bounded retention keeps the current and one rollback
release and removes source worktrees and Cargo products. `cleanup` applies the
same policy immediately without touching configuration, credentials, or
conversations.

Source builds intentionally use one Cargo job with release LTO disabled, one
codegen unit, and debug/symbol data removed to bound compile and link memory on
Desktop Macs. Do not remove these limits merely to shorten a build; the cached
binary reuse path is the preferred speed optimization.

The build fetches the exact upstream lock-file dependencies before its offline
workspace-version normalization. It then rejects any lock diff beyond expected
workspace package version changes, so a newly introduced Git dependency can be
cached without weakening the fail-closed dependency contract.

`doctor` is local/read-only. `doctor --live` performs one ephemeral DeepSeek API
request. `disable` creates a fail-safe marker and takes effect after Desktop is
restarted. `uninstall` unloads support jobs and moves their plists to a backup;
it does not purge releases, credentials, or conversations.

Because the merged model catalog is a pinned startup snapshot, a newly
released official model stays invisible until it is rebuilt.
`codex-provider sync-models` unpins the override briefly, fetches the live
official list, rebuilds and validates the merged catalog, re-pins it, and
restores the previous `config.toml` on any failure or interrupt; the default
model is preserved. `sync-models --check` reports offline drift without
writing. `update` attempts one sync after activation and only warns if it
cannot run.

On upstream mismatch or patch drift, retain the failure log and use the bundled
official backend. Never force an old custom release against a newer client.

Before adding a model or changing transport, compare current official Codex and
provider documentation with a minimal direct API probe and an App Server
structured-tool smoke. Treat the compatibility matrix as dated evidence. Once
the new path passes, update its positive/negative route tests, catalog contract,
release patch identifier, documentation, and skill baseline in the same change.
