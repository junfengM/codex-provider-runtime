---
name: codex-model-coexist
description: Evolve, configure, repair, upgrade, and validate Codex multi-provider coexistence without losing ChatGPT login or thread provider integrity. Use when adding or removing DeepSeek models, enabling provider-aware new chats from Desktop or phone Remote, reconciling new official Codex/DeepSeek capabilities, maintaining the native app-server router across upgrades, diagnosing authentication or hidden history, or performing a safe fallback.
---

# Codex Model Coexist

Treat this skill as a living operational guide, not a frozen architecture.
Re-evaluate changeable assumptions whenever Codex, DeepSeek, the Desktop app,
or observed runtime behavior changes.

## Separate invariants from current implementation

Preserve these safety invariants unless the user explicitly changes the goal:

- keep ChatGPT authentication and the OpenAI provider usable;
- never rewrite stored thread provider metadata to simulate compatibility;
- never ask the user to paste API keys into chat or commit secrets;
- activate only a Codex backend built and tested for the exact bundled version;
- fail closed to the official backend on patch drift, build failure, or version
  mismatch;
- verify actual provider metadata, completed output, and a structured tool loop;
  never infer success from the model picker alone.

Treat these as revisable implementation choices:

- supported DeepSeek model names;
- native Responses versus an adapter;
- model-catalog capability fields and reasoning levels;
- auto-review model and effort;
- router patch anchors, service layout, and validation probes.

When new evidence invalidates an implementation choice, update the runtime,
tests, documentation, and this skill together. Do not use an older skill rule to
block a safer or more native implementation.

## Establish the current upstream contract first

Before changing model support or protocol behavior:

1. Read the latest official Codex and DeepSeek documentation and release notes.
2. Inspect the installed Codex version, model catalog, provider configuration,
   and active runtime manifest.
3. Probe the official endpoint directly with the smallest representative
   request, including a structured tool call when tool compatibility matters.
4. Compare documentation with observed wire behavior. Record discrepancies as
   evidence; do not force reality to match this skill.
5. Make the narrowest implementation supported by both evidence sources.
6. Add regression tests that distinguish supported models from unsupported
   ones and exercise upgrade reconciliation.

Read [references/mechanism.md](references/mechanism.md) for catalog,
authentication, history, and decision boundaries. Read
[references/native-new-thread-router.md](references/native-new-thread-router.md)
before changing Desktop or phone Remote routing. Read
[references/native-provider-switch.md](references/native-provider-switch.md)
only when same-thread switching is in scope.

## Current verified baseline

Last verified: 2026-08-02.

- Integrate only `deepseek-v4-flash`.
- Send it directly to `https://api.deepseek.com/responses` through the Codex
  native Responses client.
- Do not expose or route V4 Pro until official native Codex/Responses support
  exists and the same acceptance tests pass.
- Route new and resumed Flash threads only; leave GPT, unknown DeepSeek models,
  and explicit third-party providers unchanged.
- Map `codex-auto-review` to Flash and prefer `low` effort while this remains the
  selected cost/performance policy.
- Keep the retired loopback gateway unloaded. It is not part of the current
  request path.
- Preserve official `thread/list` semantics: omitted, null, or empty
  `modelProviders` includes all interactive providers; explicit filters remain
  authoritative.

This baseline is dated evidence, not a permanent prohibition. When official
support changes, re-run the upstream-contract workflow and replace this section
after validation.

## Use the reusable runtime as source of truth

Prefer the public `codex-provider-runtime` project and its `codex-provider`
CLI. Do not reconstruct LaunchAgents, adapters, or patches from prose in this
skill. Locate the project, then run read-only checks first:

```bash
./bin/codex-provider status
./bin/codex-provider doctor
```

For an authorized configuration or upgrade:

```bash
./bin/codex-provider configure
./bin/codex-provider update
./bin/codex-provider verify
```

Use `doctor --live` or `appserver-smoke` only when one paid ephemeral DeepSeek
request is appropriate. The app-server smoke must observe provider `deepseek`,
a structured `commandExecution`, and an independently checked hidden result.

Fully quit and reopen Codex Desktop after catalog or backend activation. Verify
separate new GPT and Flash chats. When phone access is in scope, verify one new
phone Remote Flash chat and then resume that same thread before sending another
turn; both lifecycle paths must retain `model_provider = deepseek`.

## Authentication and catalog rules

Store the DeepSeek key in macOS Keychain with command-backed authentication
when available. Elsewhere use an environment key. Preserve the global ChatGPT
login and do not set a global DeepSeek provider merely to make the picker work.

Merge the official GPT catalog with the currently validated DeepSeek entries.
Refresh after Codex changes its official catalog. Model entries describe
capabilities; the native start/resume router supplies provider identity.

## History and provider boundaries

Do not switch providers inside an existing conversation unless a separately
validated product mechanism supports it. For missing history, inspect the
Chronological view, local thread database, and rollout files; do not change the
stored provider just to make a thread visible.

## Safe fallback

Use the runtime's reversible disable operation and restart Desktop:

```bash
./bin/codex-provider disable
```

This must preserve credentials, catalogs, releases, and conversations. Re-enable
only after exact-version build and live acceptance checks pass.

## Evolve the skill after discoveries

After a materially better verified mechanism is adopted:

1. update the reusable project first;
2. encode the discovery in tests and compatibility documentation;
3. update this skill's dated baseline and affected references;
4. validate the skill folder with `quick_validate.py`;
5. compare the installed skill with the project integration guidance so they do
   not drift;
6. preserve a recoverable backup of the previous installed skill.

Keep superseded mechanisms only when needed for migration cleanup. Label them
as retired and remove instructions that could accidentally reinstall them.
