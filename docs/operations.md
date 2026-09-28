# Operations

## Safe API key handoff

Run the credential entry step from a visible macOS Terminal, not from a chat
window or an agent session whose stdin is not visible:

```bash
cd /path/to/codex-provider-runtime
./bin/codex-provider keychain-set
```

The prompt does not echo the key. The command stores it in the macOS Keychain
under the runtime service identity and does not write the value to the
repository, `config.toml`, logs, or chat history. Never paste the key into a
Codex/ChatGPT conversation. An agent can safely check the handoff without
reading the secret:

```bash
./bin/codex-provider keychain-status
```

## Install

```bash
./bin/codex-provider keychain-set
./bin/codex-provider install
```

Restart Desktop, then run `./bin/codex-provider doctor --live` and create one
new GPT chat plus one new DeepSeek chat. When phone access matters, create one
new phone Remote DeepSeek chat as the final acceptance check. The automated
equivalent is:

```bash
./bin/codex-provider test-deepseek
./bin/codex-provider appserver-smoke
```

The second command creates an ephemeral app-server thread, routes it to
DeepSeek, asks the official `shell_command` tool to hash a hidden random file,
and requires the final message to match the locally calculated hash.

## Update an existing install from the repository

The repository is source only; the active runtime lives in the install root and
the model catalogs live in `$CODEX_HOME` (`models.json` for the validated
DeepSeek entries, `models-coexist.json` for the merged catalog that
`model_catalog_json` points at). On a machine that already has the runtime,
pulling new commits is not enough:

```bash
git pull
./bin/codex-provider update        # runtime binaries + LaunchAgent support
./bin/codex-provider sync-models   # newly released official models (also tried by update)
./bin/codex-provider skill-install # both operator skills, including shared copies
# fully quit and reopen ChatGPT/Codex Desktop
./bin/codex-provider doctor
```

`install` on a fresh machine already performs `configure`, the build, and
activation. Everything else that is machine-local stays manual: the bundled
ChatGPT.app, the DeepSeek key in the login Keychain, and the optional
`~/.local/bin/codex` shim used by Open Design.

## Model catalog refresh

`model_catalog_json` is a startup snapshot. Once it is pinned, the client never
replaces it with the account's live model list, so a newly released GPT model
stays invisible in the picker until the merged catalog is rebuilt. `update`
attempts that refresh after activation; when the machine is offline, or the
run needs a network/login combination that is unavailable, it only prints a
warning.

```bash
./bin/codex-provider sync-models            # refresh the live list, rebuild, re-pin
./bin/codex-provider sync-models --check    # offline drift report, exit 1 when stale
./bin/codex-provider status                 # prints the drift count when the catalog lags
```

`sync-models` backs up `config.toml` and both catalogs first, briefly removes
`model_catalog_json`, runs `codex debug models` to fetch the account's live
list, rebuilds the merged catalog, re-pins it, and then re-reads the catalog
through the client. Any failure, including Ctrl-C, restores the backed-up
`config.toml`, so the machine never stays unpinned. The default model and
reasoning effort are left untouched; use `configure` only when you also want
the default model reset to the official first entry with `medium` effort.

Sync the catalog after any Desktop upgrade or upstream model announcement. A
catalog that lags is not a routing failure: routing still works, the new model
is simply not offered yet.

## Routine health check

```bash
./bin/codex-provider status
./bin/codex-provider doctor
./bin/codex-provider logs 100
```

Use `doctor --live` only when one ephemeral paid API request is appropriate.

## Desktop upgrade

The updater watches the bundled Codex binary and also runs every 15 minutes.
Manual reconciliation is safe and idempotent:

```bash
./bin/codex-provider update
./bin/codex-provider verify
```

Restart Desktop after a new release is activated. Verify actual rollout
provider metadata; do not rely on the picker label.

`auto` is the default distribution policy. It first reuses a locally certified
binary with the same recipe, then downloads an attested GitHub Release, and
finally falls back to a local source build. Choose a persistent machine-level
policy with `codex-provider install --distribution auto|prebuilt-only|source`
or `codex-provider update --distribution auto|prebuilt-only|source`. The value
is stored in `~/.codex/provider-runtime/build-policy.json` and passed to both
LaunchAgents and launcher-triggered background updates. `prebuilt-only` never
runs Cargo and can install on a Mac without rustup; `source` skips prebuilt
lookups and uses the local Rust toolchain.

The prebuilt archive contains only the patched Codex CLI and its manifest. Each
Mac supplies the same-version `codex-code-mode-host` from its own ChatGPT.app.
Clients download public Release assets without GitHub login, verify the bundle
with `gh attestation verify --bundle`, and require this repository, signer
workflow, and `refs/heads/main` before validating or extracting the archive.
The manifest binds the exact upstream `rust-v<version>` peeled commit, patch and
recipe digests, architecture, binary checksum, and macOS 13.0 minimum. A source
or manifest mismatch fails closed.

The main-branch workflow publishes after distribution-code changes, scans once
every six hours, and accepts manual exact-version requests. Scheduled discovery
selects at most one newest upstream Codex Release with both arm64 CLI and host
assets. This follows published CLI Releases; Desktop alpha releases may differ
and can be requested through `workflow_dispatch`. CI caches its own Cargo target
and dependencies. Local Cargo target state is retained after successful updates
until it exceeds 24 GiB by default; set `CODEX_PROVIDER_CARGO_CACHE_MAX_GB` to
adjust the 1–128 GiB bound. `cleanup` clears it explicitly. The bounded cache
reduces recompilation but does not guarantee a hit for every version.

When only the bundled client digest moves but the public source tag, patch, and
recipe are unchanged, the updater can re-certify the local binary instead of
compiling an identical one. Reuse still re-runs the code-mode-host check and
protocol smoke, records the new official checksum, and activates atomically.
`--no-reuse` skips carrying a local binary across a changed official digest;
use `--distribution source` to skip prebuilt downloads.

A background source-build failure is memoized by the bundled Codex binary and
build recipe so an unchanged failure is not recompiled every 15 minutes. The
prebuilt lookup has a separate 10-minute retry window: missing assets, network
errors, and local attestation/smoke failures do not block later scheduled
checks. Manual `update` retries immediately. If an update fails, the CLI
restores its support agents and keeps the current release; when that release
does not match Desktop, the launcher uses the official Codex backend until a
matching runtime is available.

Source builds use one Cargo job and a memory-bounded release profile: LTO is
disabled, code generation uses one unit, and debug/symbol data is removed. The
route module's dependency-free Rust unit tests, exact version, arm64 Mach-O
metadata, and full app-server protocol smoke gate the prebuild. Local source
tests share the release Cargo graph with the subsequent binary build.

Before the offline workspace lock normalization and `--locked` build, the
updater fetches the exact dependencies selected by the upstream lock file with
Git CLI support enabled for nested Git dependencies. The subsequent lock diff
still fails closed if anything other than the expected workspace package
version normalization changed.

Only the patched Codex CLI/app-server is rebuilt. The release copies the
executable `codex-code-mode-host` bundled with the same Desktop update and
records its checksum. The host contains no provider-routing patch, and reusing
the signed bundled binary prevents a missing upstream Rusty V8 archive from
blocking an otherwise compatible local update.

After a DeepSeek API/model announcement, compare the official Codex integration
page and setup script with `docs/compatibility.md`, then run
`./bin/codex-provider configure` and the two live smoke tests. Catalog
validation fails closed if the known V4 Flash/Pro compatibility fields drift.

`./bin/codex-provider skill-install` refreshes the two operator skills under
`$CODEX_HOME/skills` and, when a cross-agent shared skills directory already
contains them (default `~/ai/shared/skills`, override with
`CODEX_SHARED_SKILLS_ROOT`), refreshes that copy from the same source so the two
never describe different routing mechanisms.

## Emergency fallback

```bash
./bin/codex-provider disable
```

Restart Desktop. The stable launcher will use the bundled official backend.
This keeps ChatGPT available and preserves releases and credentials.

Recover with:

```bash
./bin/codex-provider enable
./bin/codex-provider update
./bin/codex-provider doctor
```

## Uninstall support

```bash
./bin/codex-provider uninstall
```

This unloads and backs up the runtime LaunchAgents and clears
`CODEX_CLI_PATH` only when it points to this runtime. It does not delete
configuration, Keychain entries, releases, caches, or conversation data.

## Incident evidence

Collect without secrets:

- `codex-provider status` output;
- `codex-provider logs 200` output;
- bundled and current manifest versions/checksums;
- affected rollout `session_meta.model_provider` and turn model;
- structured error event types.
- configured DeepSeek base URL and loaded Flash model metadata.

Do not attach full prompts, responses, `auth.json`, `config.toml`, Keychain
output, or entire rollout files to an issue.
