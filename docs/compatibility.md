# DeepSeek compatibility matrix

This matrix describes the behavior of DeepSeek V4 Flash-0731 when selected in Codex
Desktop or a new phone Remote thread.

| Capability | Status | Adapter behavior |
|---|---|---|
| New-thread provider routing | Supported | Only `deepseek-v4-flash` becomes provider `deepseek` in shared `thread/start`. |
| Cross-provider history visibility | Supported | Omitted/null/empty `modelProviders` lists all interactive providers; explicit filters remain exact. |
| Text and streaming output | Supported | Direct DeepSeek native Responses SSE. |
| Thinking mode | Supported | Official catalog exposes `low`/`high`/`max`; Desktop requires `Max` in Settings → Configuration → Model features → Available reasoning efforts. |
| Standard function/shell tools | Supported | Native Responses items are dispatched by Codex without translation. |
| Namespace/MCP tools | Supported by Codex | Client-side tools remain owned by Codex and its MCP runtime. |
| Freeform tools (`apply_patch`) | Supported | Official catalog uses `apply_patch_tool_type = freeform`. |
| Parallel tool calls | Supported | Official catalog enables parallel tool calls. |
| Token/cache usage | Supported | Native Responses usage fields reach Codex directly. |
| JSON output schema | Native | No Chat-format approximation layer is present. |
| Auto-review | Supported with trust change | `auto_review_model_override` routes the reviewer to Flash with low effort. |
| Long-context compaction | Supported locally | Non-OpenAI providers use Codex local compaction, not `/responses/compact`. |
| Search tool | Official catalog enabled | Uses DeepSeek's current `web_search_tool_type = text` contract. |
| Image/audio input | Not supported | The official Flash catalog is text-only. |
| More than 128 functions | Upstream limit | Defer or disable unused MCP/plugin tools. |
| Same-thread provider switching | Out of scope | New chats only; existing-thread provider migration remains a Codex product boundary. |
| V4 Pro | Not integrated | Wait for official native Responses/Codex support, then revalidate before adding it. |

## Validation evidence

`codex-provider test-deepseek` proves the local CLI route and a real shell tool
round trip. The normal runtime protocol smoke compares omitted and empty
`modelProviders` pages and checks the explicit DeepSeek filter.
`codex-provider appserver-smoke` starts an ephemeral public
app-server thread, verifies provider `deepseek`, executes a hidden random-file
SHA-256 challenge through `shell_command`, observes `commandExecution`, and compares
the final message with the independently calculated hash.

## Upstream contract

Checked against DeepSeek's official Codex setup and API documentation on
2026-08-01. The catalog follows the V4-Flash-0731 Codex contract: native
Responses, 1,048,576-token context, `low`/`high`/`max` effort, normal
`shell_command` tools, parallel calls, freeform apply-patch, and non-lite
Responses. V4 Pro is intentionally excluded until DeepSeek documents native
Responses support for it and live acceptance passes.

Codex Desktop independently filters the reasoning levels shown in its model picker.
Its default enabled-level set can omit `max`, so a provider that advertises only
`low`/`high`/`max` may appear as `low`/`high` on Desktop while phone Remote still
shows `max`. Enable `Max` under Settings → Configuration → Model features →
Available reasoning efforts; no runtime patch or app rebuild is required.

- https://api-docs.deepseek.com/quick_start/agent_integrations/codex/
- https://api-docs.deepseek.com/guides/responses_api/
- https://api-docs.deepseek.com/updates/
- https://developers.openai.com/codex/app-server/
