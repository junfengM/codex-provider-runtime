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

Current verified baseline (2026-09-24): `deepseek-flash` (DeepSeek-V4.1-Flash,
released 2026-09-10) is integrated using DeepSeek's native Responses API
directly, with image input enabled (`input_modalities = ["text", "image"]`)
after a Codex `exec -i` vision probe passed. The retired `deepseek-v4-flash`
and `deepseek-v4-pro` names stay routed because DeepSeek serves them from V4.1
Flash, so existing threads keep working, but neither is offered in the picker.
The runtime is validated against Codex `0.155.0-alpha.16.4`, keeps GPT on
OpenAI, and passes new-thread, cold-resume, and app-server structured-tool
smokes for the current Flash slug. This is a dated baseline, not a permanent
prohibition.

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
codex-provider update --distribution prebuilt-only
codex-provider sync-models
codex-provider verify
codex-provider cleanup
```

`update` defaults to `auto`: it reuses a same-recipe local release, downloads a
GitHub prebuild after verifying its bundle against the main workflow, then
falls back to a source build. Use `--distribution prebuilt-only` on Macs
without rustup/Cargo; that choice is saved locally and the LaunchAgent keeps it
for unattended runs. `--distribution source` skips prebuilt downloads.

An update refreshes the runtime binaries and then attempts one best-effort
`sync-models` for newly released official models; when that sync cannot reach
the account it only warns, so re-run `codex-provider sync-models` yourself.
`sync-models` keeps the default model, `configure` resets it.

Scheduled source-build failures are memoized by the bundled binary and build
recipe so an unchanged cold compile does not repeat every 15 minutes. Missing
prebuilts, network errors, and local attestation/smoke failures have a separate
short retry window; scheduled checks can discover a later release. Manual
`update` retries immediately. Failed updates restore support agents and leave
the current release in place; a mismatched release makes the launcher use the
official bundled backend. Successful updates keep one rollback release and
retain the local Cargo target up to its configurable 24 GiB default cap;
`cleanup` clears it on demand.

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
