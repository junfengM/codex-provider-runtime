---
name: codex-provider-runtime
description: Operate, diagnose, evolve, upgrade, verify, safely disable, or uninstall the local Codex Provider Runtime that routes validated DeepSeek models from Desktop and phone Remote while preserving ChatGPT/OpenAI. Use for provider authentication failures, new upstream model or protocol capabilities, post-upgrade reconciliation, actual rollout verification, legacy router cleanup, and runtime health checks.
---

# Codex Provider Runtime

Treat the standalone `codex-provider` CLI as the source of truth. Do not
reconstruct LaunchAgents, edit rollout provider metadata, or invoke the legacy
JavaScript router manually.

Separate safety invariants from revisable implementation choices. Preserve
ChatGPT authentication, thread provider integrity, secret handling,
exact-version builds, fail-closed fallback, and end-to-end verification. Recheck
official documentation and live wire behavior before treating model names,
catalog fields, native-versus-adapter transport, auto-review routing, or patch
anchors as fixed. When better verified support appears, update runtime code,
tests, compatibility docs, and this skill together; do not let an older skill
rule block a safer native mechanism.

Current verified baseline (2026-08-02): only `deepseek-v4-flash` is integrated,
using DeepSeek's native Responses API directly. V4 Pro remains absent until
official support and the same structured-tool acceptance checks pass. This is a
dated baseline, not a permanent prohibition.

## Diagnose

Run read-only checks first:

```bash
codex-provider status
codex-provider doctor
```

Use `codex-provider doctor --live` only when one ephemeral DeepSeek request is
appropriate. Do not claim success from the model picker; verify rollout
`session_meta.model_provider`, turn model, completion/token events, and absence
of authentication or fallback errors.

Read [references/operations.md](references/operations.md) before installing,
updating, disabling, or uninstalling the runtime.

## Maintain

Use:

```bash
codex-provider update
codex-provider verify
```

After a Desktop upgrade, upstream model change, or activated release, fully
restart Desktop and verify one GPT and one currently supported DeepSeek new
chat. Add one phone Remote DeepSeek new chat and resume it before sending a
second turn when remote use is in scope.

## Fail safely

Use `codex-provider disable` for emergency fallback. Use
`codex-provider uninstall` only when the user requests removal of runtime
support. Both preserve credentials, configuration, releases, and conversations.

Never run legacy `coexist.sh router-uninstall` while the native runtime owns
`CODEX_CLI_PATH`.
