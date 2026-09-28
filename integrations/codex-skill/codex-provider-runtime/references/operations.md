# Runtime operations

The standalone CLI owns the lifecycle:

```bash
codex-provider prerequisites
codex-provider prerequisites --distribution prebuilt-only
codex-provider keychain-set
codex-provider configure
codex-provider sync-models
codex-provider install --distribution prebuilt-only
codex-provider status
codex-provider doctor [--live]
codex-provider update
codex-provider cleanup
codex-provider verify
codex-provider disable
codex-provider enable
codex-provider uninstall
```

`install` configures coexistence, checks the exact matching Codex release,
installs generic LaunchAgents, and activates the stable launcher. Its default
`auto` policy reuses a local same-recipe release, then verifies an attested
prebuild, then falls back to a source build. On a Mac without rustup/Cargo,
choose `--distribution prebuilt-only`; it never compiles and the policy persists
for scheduled updates. `--distribution source` skips prebuilt downloads.

An unattended source-build failure writes a fingerprinted memo tied to the
binary and build recipe, preventing repeated cold compiles. Missing prebuilds,
network failures, and local attestation/smoke failures use a separate short
retry window, so the next scheduled run can see a newly published release.
Manual `update` retries immediately. If an update fails, support LaunchAgents
are restored and the old release remains; a version mismatch makes the stable
launcher use the bundled official backend.

Prebuilt archives contain only patched `codex` and its manifest. Every machine
uses its own same-version official `codex-code-mode-host`. `gh attestation verify --bundle`
binds the archive to this repository, its signer workflow, and
`refs/heads/main` before extraction. The manifest checks source tag commit,
patch/recipe digests, checksum, architecture, and the macOS 13.0 app floor.
Scheduled release discovery runs every six hours and only selects the newest
upstream CLI release that has both arm64 CLI and host assets; Desktop alpha
releases may need an exact `workflow_dispatch` request.

After a successful activation, bounded retention keeps the current and one
rollback release and removes source worktrees. The Cargo target cache stays
available up to a configurable 24 GiB default; `cleanup` clears it explicitly
without touching configuration, credentials, or conversations.

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
