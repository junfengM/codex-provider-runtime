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

The native patch changes only new-thread provider normalization. The DeepSeek
protocol gateway binds only to `127.0.0.1:17892`; it necessarily relays prompts,
tool schemas/results, streamed responses, and the command-backed bearer header
to `https://api.deepseek.com`. It does not persist or log headers, bodies,
prompts, responses, tool arguments, or credentials. Operational logs contain
only startup, health, and HTTP method/path/status metadata.

Any local process running as the same macOS user is already inside the user's
trust boundary and can connect to the loopback port. Do not bind the gateway to
LAN/WAN addresses; the executable refuses non-loopback hosts.
