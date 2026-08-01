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
          └── deepseek-* ─► loopback Responses gateway
                               │ protocol translation
                               ▼
                         DeepSeek Chat Completions
                               │ structured stream
                               ▼
                         Codex tool execution loop
```

The stable launcher is the only path stored in the GUI login environment. It
derives the install root from its own location, so the project is not tied to a
user name or one fixed state directory.

## Source and installed state

The repository contains only reusable source, tests, documentation, and an
optional skill. `router_manager.py install-support` copies the minimum runtime
assets into the chosen install root and creates three generic LaunchAgents:

- `com.codex.provider-runtime.deepseek-gateway`
- `com.codex.provider-runtime.environment`
- `com.codex.provider-runtime.updater`

Existing `com.dudu.codex-deepseek-router-*` and
`com.example.codex-provider-router` agents are recognized as legacy support and
moved to recoverable backups during activation.

## Patch policy

The Rust patch is deliberately provider-specific and new-thread-only. It
changes a missing or default OpenAI provider to `deepseek` only when the model
starts with `deepseek-`. Explicit non-default providers and GPT models pass
through unchanged.

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

The gateway is independent of the Rust release and is copied into the install
root whenever support is activated. The updater continues to rebuild only the
version-coupled app-server patch after a Desktop upgrade; the loopback gateway
and its LaunchAgent remain stable across those rebuilds.

## Protocol adapter

Current Codex custom providers emit only the Responses wire protocol, while
DeepSeek's structured tools are exposed through Chat Completions. The loopback
gateway translates both directions and keeps the Codex core tool loop intact:

- developer/user/assistant/tool messages and tool call ids;
- raw `reasoning_content` required by DeepSeek after a thinking-mode tool call;
- function, namespace, tool-search, and freeform tools;
- Code Mode `exec` raw JavaScript and common argument aliases;
- output text, reasoning deltas, tool calls, usage, incomplete responses, and
  retryable resource failures;
- internal `codex-auto-review` to `deepseek-v4-pro` mapping.

The non-OpenAI provider automatically uses Codex local compaction, so it never
depends on OpenAI's private `/responses/compact` response format.

## Trust boundaries

- ChatGPT authentication remains owned by the official Codex flow.
- DeepSeek authentication is command-backed from macOS Keychain.
- The bearer credential passes only from Codex to a loopback listener and then
  to `https://api.deepseek.com`; the gateway never prints headers or bodies.
- The runtime never stores API Key values in manifests or logs.
- Rollout metadata is read for validation but never rewritten.
- The project never modifies or re-signs `ChatGPT.app`.
