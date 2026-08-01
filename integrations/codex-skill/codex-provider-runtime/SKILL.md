---
name: codex-provider-runtime
description: Operate, diagnose, upgrade, verify, safely disable, or uninstall the local Codex Provider Runtime that routes new DeepSeek chats from Desktop and phone Remote while preserving ChatGPT/OpenAI. Use for provider authentication failures, post-upgrade reconciliation, actual rollout verification, legacy router cleanup, and runtime health checks.
---

# Codex Provider Runtime

Treat the standalone `codex-provider` CLI as the source of truth. Do not
reconstruct LaunchAgents, edit rollout provider metadata, or invoke the legacy
JavaScript router manually.

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

After a Desktop upgrade or activated release, fully restart Desktop and verify
one GPT and one DeepSeek new chat. Add one phone Remote DeepSeek new chat when
remote use is in scope.

## Fail safely

Use `codex-provider disable` for emergency fallback. Use
`codex-provider uninstall` only when the user requests removal of runtime
support. Both preserve credentials, configuration, releases, and conversations.

Never run legacy `coexist.sh router-uninstall` while the native runtime owns
`CODEX_CLI_PATH`.
