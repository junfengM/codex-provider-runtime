# Security

This repository contains no API keys, Codex login state, conversation history,
compiled Codex binaries, or local model catalogs.

- Store the DeepSeek API key in macOS Keychain through
  `codex-provider keychain-set`.
- Keep ChatGPT authentication under the official Codex login flow.
- Do not commit files copied from `~/.codex`, runtime logs, rollout JSONL,
  LaunchAgent plists, release binaries, or build caches.
- Run `make check` before every push. The check rejects common credential
  patterns and known local-state filenames.

The native patch changes only new/resumed-thread provider normalization.
DeepSeek V4 Flash and Pro use Codex's normal HTTPS Responses client to connect directly to
`https://api.deepseek.com`; this project does not proxy prompts, responses,
tools, or credentials. The API key remains command-backed from macOS Keychain.
