# Mechanism and evidence boundaries

## Stable facts

- The model catalog describes model capabilities but does not reliably select
  a transport provider.
- `thread/start` carries model and provider; the Desktop picker can otherwise
  submit a third-party model with the OpenAI default provider.
- `model_catalog_json` is a startup snapshot and must be merged with official
  GPT entries and refreshed after updates.
- Stored thread provider metadata is part of conversation identity. Rewriting
  it can resume a thread through the wrong endpoint.
- Desktop and phone Remote new chats converge on the shared App Server
  `thread/start` path in the currently verified client.

Re-check protocol fields after Codex upgrades; the conclusions are stable only
while the observed protocol remains unchanged.

## Evidence hierarchy

For changeable integration details, use this order:

1. latest official provider and Codex documentation;
2. direct minimal endpoint probes;
3. end-to-end App Server events and rollout metadata;
4. repository regression tests and release manifests;
5. this skill's dated description.

When layers disagree, investigate rather than blindly preferring an old local
rule. Clearly label inferences and update the skill after resolving the
discrepancy.

## Provider and authentication

Keep the global provider unset so ChatGPT remains the normal default. Register
DeepSeek as a custom provider and route only validated model names at new-thread
creation. On macOS, prefer command-backed Keychain authentication; elsewhere
use an environment key. Never copy the secret into a catalog or repository.

Current verified DeepSeek provider contract (2026-08-01):

- base URL `https://api.deepseek.com`;
- wire API `responses`;
- only `deepseek-v4-flash` integrated;
- native structured tools, no local protocol gateway.

This contract must be rechecked after upstream announcements.

## Catalog generation

Clone a current local official model entry to retain required Codex fields,
then overwrite every provider-documented capability field. Validate the final
entry as a contract, not just by slug presence. Remove stale DeepSeek entries
that are no longer supported by the runtime.

For the current Flash baseline, validate context window, reasoning efforts,
tool mode, shell type, apply-patch type, search type, parallel tool support,
Responses-lite setting, text modality, and auto-review override. Treat all
values as dated upstream metadata.

## Validation contract

A complete validation requires:

1. `codex debug models` contains GPT models and exactly the intended DeepSeek
   models;
2. ChatGPT login is available and forced API-only authentication is absent;
3. provider base URL and wire API match the current upstream contract;
4. an ephemeral direct/provider-explicit request completes;
5. an App Server new thread records the expected provider and emits structured
   tool execution;
6. a hidden challenge result matches an independently calculated value;
7. retired services are unloaded and upgrade/fallback tests pass.

Network reachability failures are distinct from catalog correctness, but a live
success claim requires both.

## Thread visibility

Desktop clients may filter `thread/list` by provider. A thread can remain in
`state_*.sqlite` and `sessions/**/*.jsonl` while missing from the current
sidebar. Recover by using Chronological view, auditing all providers, and
opening by thread ID. Never change `model_provider` merely for visibility.

## Product boundary

The current local runtime solves provider-aware new-thread creation. It does
not guarantee safe same-thread provider switching because `turn/start`, active
client state, history item formats, and persistence must all change
atomically. Read `native-provider-switch.md` when that separate goal returns.

## Update discipline

After a new model or native protocol capability appears:

1. verify official support and pricing/performance implications;
2. probe the raw API and Codex App Server path;
3. decide whether the old adapter, catalog field, or exclusion is obsolete;
4. implement in the reusable project with positive and negative tests;
5. update compatibility docs and this skill's dated baseline;
6. retain safe migration cleanup, but remove obsolete installation guidance.
