# DeepSeek compatibility matrix

This matrix describes DeepSeek V4.1 Flash (`deepseek-flash`, released
2026-09-10; retired alias `deepseek-v4-flash`) and V4 Pro-0813 in Codex Desktop
or a new phone Remote thread.

| Capability | Status | Adapter behavior |
|---|---|---|
| New-thread provider routing | Supported | The exact supported slugs `deepseek-flash`, its retired `deepseek-v4-flash` alias, and `deepseek-v4-pro` become provider `deepseek` in shared `thread/start`. |
| Remote resume provider continuity | Supported | `thread/resume` rebinds a missing/default provider to `deepseek` for Flash or Pro without rewriting stored thread metadata. |
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
| Image/audio input | Not enabled | V4.1 Flash supports vision upstream (DeepSeek docs, 2026-09-10); the catalog still declares `input_modalities = ["text"]` until Codex image handling is verified end-to-end. |
| More than 128 functions | Upstream limit | Defer or disable unused MCP/plugin tools. |
| Same-thread provider switching | Out of scope | A DeepSeek thread remains DeepSeek across resume and later turns; deliberate OpenAI ↔ DeepSeek migration is not implemented. |
| V4 Pro | Supported until 2026-09-14 | Official Pro-0813 exposes Responses and tools; DeepSeek routes every `deepseek-v4-pro` request to V4.1 Flash from 04:00 UTC on 2026-09-14 until V4.1 Pro ships. |

## Current client/runtime validation

On 2026-09-10 DeepSeek released V4.1 Flash (`deepseek-flash`) and retired
V4-Flash; `deepseek-v4-flash` is still accepted and served by V4.1 Flash. The
runtime routes the new slug, the retired alias, and `deepseek-v4-pro`, and the
catalog publishes `deepseek-flash` as `DeepSeek-V4.1-Flash` with
`auto_review_model_override = deepseek-flash`.

On 2026-09-09, the native runtime was rebuilt and activated for Codex Desktop
`26.901.51231` with bundled Codex CLI `0.153.4` (`rust-v0.153.4`). The source
patch manager now accepts both the older request-processor module layout and
the `0.153.4` layout that inserts `mod projects;`, while refusing ambiguous or
missing anchors.

Validation completed after the Desktop restart:

- `codex-provider verify` passed the ChatGPT/DeepSeek routing contract;
- the desktop app-server process loaded
  `/Users/mjf/.codex/provider-runtime/current/codex`;
- `codex-provider appserver-smoke deepseek-v4-flash` completed a structured
  `commandExecution` and matched the hidden SHA-256 challenge;
- the routing smoke kept GPT on `openai` and both supported DeepSeek models on
  `deepseek`.

## Validation evidence

`codex-provider test-deepseek` proves the local CLI route and a real shell tool
round trip. The normal runtime protocol smoke compares omitted and empty
`modelProviders` pages and checks the explicit DeepSeek filter.
`codex-provider appserver-smoke` starts an ephemeral public
app-server thread, verifies provider `deepseek`, executes a hidden random-file
SHA-256 challenge through `shell_command`, observes `commandExecution`, and compares
the final message with the independently calculated hash.

## Upstream contract

Checked against DeepSeek's official API documentation and live protocol behavior on
2026-08-13. The catalog follows the shared V4 Flash/Pro contract: native
Responses, 1,048,576-token context, `low`/`high`/`max` effort, normal
`shell_command` tools, parallel calls, freeform apply-patch, and non-lite
Responses. Pro remains model-exact rather than routing arbitrary future
`deepseek-*` identifiers.

Codex Desktop independently filters the reasoning levels shown in its model picker.
Its default enabled-level set can omit `max`, so a provider that advertises only
`low`/`high`/`max` may appear as `low`/`high` on Desktop while phone Remote still
shows `max`. Enable `Max` under Settings → Configuration → Model features →
Available reasoning efforts; no runtime patch or app rebuild is required.

- https://api-docs.deepseek.com/quick_start/agent_integrations/codex/
- https://api-docs.deepseek.com/guides/responses_api/
- https://api-docs.deepseek.com/updates/
- https://developers.openai.com/codex/app-server/
