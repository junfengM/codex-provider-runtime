# Operations

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
DeepSeek, asks Code Mode to hash a hidden random file through
`tools.exec_command`, and requires the final message to match the locally
calculated hash.

## Routine health check

```bash
./bin/codex-provider status
./bin/codex-provider doctor
./bin/codex-provider logs 100
```

Use `doctor --live` only when one ephemeral paid API request is appropriate.
`logs` includes `gateway.log`; the gateway records health/request status only,
not prompts, responses, arguments, or Authorization headers.

## Desktop upgrade

The updater watches the bundled Codex binary and also runs every 15 minutes.
Manual reconciliation is safe and idempotent:

```bash
./bin/codex-provider update
./bin/codex-provider verify
```

Restart Desktop after a new release is activated. Verify actual rollout
provider metadata; do not rely on the picker label.

The gateway LaunchAgent is version-independent. The updater rebuilds the exact
Codex tag only when the bundled backend changes, then the stable launcher
atomically adopts the matching release.

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
- gateway health (`status = ok`) and LaunchAgent state.

Do not attach full prompts, responses, `auth.json`, `config.toml`, Keychain
output, or entire rollout files to an issue.
