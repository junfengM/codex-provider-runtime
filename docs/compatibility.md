# DeepSeek compatibility matrix

This matrix describes the behavior of DeepSeek V4 when selected in Codex
Desktop or a new phone Remote thread.

| Capability | Status | Adapter behavior |
|---|---|---|
| New-thread provider routing | Supported | `deepseek-*` becomes provider `deepseek` in shared `thread/start`. |
| Text and streaming output | Supported | Chat deltas become Responses SSE message events. |
| Thinking mode | Supported | Codex effort maps to DeepSeek `high`/`max`; raw `reasoning_content` is preserved across tool rounds. |
| Standard function tools | Supported | Responses functions map to Chat function tools and back. |
| Namespace/MCP tools | Supported | Names are safely flattened to DeepSeek's 64-character function format and restored before Codex dispatch. |
| Freeform tools (`apply_patch`) | Supported | Raw input is wrapped in a function argument and unwrapped into `custom_tool_call`. |
| Code Mode `exec` | Supported | `input/code/js/source/script/expression` aliases normalize to raw JavaScript; instructions require nested `tools.exec_command` for shell work. |
| Tool result continuation | Supported | Tool ids and thinking content are reconstructed as assistant/tool messages. |
| Tool search | Supported | Client-side `tool_search` calls and results round-trip. |
| Parallel tool calls | Supported | Multiple returned tool calls are emitted independently; DeepSeek `auto` may select one or more tools. |
| Token/cache usage | Supported | Prompt, completion, cached, and reasoning tokens map into Codex usage fields. |
| JSON output schema | Best effort | Schema is added to system instructions and DeepSeek `json_object` is enabled; strict JSON-Schema validation is not available upstream. |
| Auto-review | Supported with trust change | Internal `codex-auto-review` maps to `deepseek-v4-pro`; DeepSeek performs the review. |
| Long-context compaction | Supported locally | Non-OpenAI providers use Codex local compaction, not `/responses/compact`. |
| OpenAI hosted web search | Not applicable | Disabled in the DeepSeek catalog; use Codex client/browser/MCP tools instead. |
| Image/audio input | Not supported | V4 catalog is text-only; the gateway rejects user media instead of silently dropping it. |
| More than 128 functions | Upstream limit | Gateway returns `too_many_tools`; defer or disable unused MCP/plugin tools. |
| Same-thread provider switching | Out of scope | New chats only; existing-thread provider migration remains a Codex product boundary. |
| Realtime voice/WebRTC | Not supported | DeepSeek Chat Completions is used only for text agent turns. |

## Validation evidence

`codex-provider test-deepseek` proves the local CLI route and a real shell tool
round trip. `codex-provider appserver-smoke` starts an ephemeral public
app-server thread, verifies provider `deepseek`, executes a hidden random-file
SHA-256 challenge through Code Mode, observes `commandExecution`, and compares
the final message with the independently calculated hash.

The adapter follows DeepSeek's documented Chat Completions tool-call and
thinking-mode requirements:

- https://api-docs.deepseek.com/api/create-chat-completion/
- https://api-docs.deepseek.com/guides/tool_calls/
- https://api-docs.deepseek.com/guides/thinking_mode/
