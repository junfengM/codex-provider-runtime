# Architecture

## Components

```text
Desktop / phone Remote
          │ thread/start
          ▼
stable CODEX_CLI_PATH launcher
          │ exact version gate
          ├──────── mismatch/disabled ───────► bundled official Codex
          ▼
versioned patched Codex app-server
          │ model prefix normalization
          ├── GPT ───────────────────────────► OpenAI provider
          └── deepseek-v4-flash ─────────────► DeepSeek native Responses
```

The stable launcher is the only path stored in the GUI login environment. It
derives the install root from its own location, so the project is not tied to a
user name or one fixed state directory.

## Source and installed state

The repository contains only reusable source, tests, documentation, and an
optional skill. `router_manager.py install-support` copies the minimum runtime
assets into the chosen install root and creates two generic LaunchAgents:

- `com.codex.provider-runtime.environment`
- `com.codex.provider-runtime.updater`

Existing `com.dudu.codex-deepseek-router-*` and
`com.example.codex-provider-router` agents are recognized as legacy support and
moved to recoverable backups during activation.

## Patch policy

The Rust patch is deliberately provider-specific and new-thread-only. It
changes a missing or default OpenAI provider to `deepseek` only when the model
is exactly `deepseek-v4-flash`. Explicit non-default providers, unintegrated
DeepSeek models, and GPT models pass through unchanged.

If more providers are added later, prefer a reviewed data-driven route table
over accumulating model-name conditions in the handler. Keep the current narrow
policy until a second provider creates a real generalization requirement.

## Upgrade contract

Activation requires an exact bundled-version/public-tag match, fixed patch
anchors, locked dependency proof, provider-route tests, release builds of both
required binaries, protocol smoke, checksums, and an atomic symlink switch.

Structural upstream changes are a supported failure state. The updater must
stop and retain evidence; the launcher must use the official backend until a
human-reviewed patch update passes the same contract.

The updater rebuilds only the version-coupled app-server patch after a Desktop
upgrade. Model-catalog refresh is separate and validates the current official
DeepSeek Flash Codex contract before activation.

## Native protocol

Codex custom providers emit the Responses wire protocol. DeepSeek
V4-Flash-0731 supports that protocol natively and is specifically documented
for Codex, so requests go directly from Codex to `https://api.deepseek.com`.
There is no local protocol translation or prompt/response proxy.

The model catalog sets `auto_review_model_override` to
`deepseek-v4-flash`. This lets Codex choose Flash directly and use its supported
`low` reasoning effort for the reviewer.

V4 Pro is deliberately absent from the catalog. It can be reconsidered after
DeepSeek officially releases native Responses/Codex support and the same live
tool-loop acceptance checks pass.

The non-OpenAI provider automatically uses Codex local compaction, so it never
depends on OpenAI's private `/responses/compact` response format.

## Trust boundaries

- ChatGPT authentication remains owned by the official Codex flow.
- DeepSeek authentication is command-backed from macOS Keychain.
- Codex sends the bearer credential directly to `https://api.deepseek.com`.
- The runtime never stores API Key values in manifests or logs.
- Rollout metadata is read for validation but never rewritten.
- The project never modifies or re-signs `ChatGPT.app`.
