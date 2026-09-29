# Native provider continuity router

## Scope

Use the native router when Desktop and phone Remote must start or resume a
supported third-party model through its provider while GPT remains on OpenAI.
Keep the route table evidence-driven and model-exact. The 2026-08-13 verified
policy routes `deepseek-flash` (V4.1 Flash, 2026-09-10), its retired
`deepseek-v4-flash` alias, and `deepseek-v4-pro` to `deepseek`.

It normalizes provider identity at new-thread creation and Remote resume. It
does not implement deliberate same-thread provider switching.

## Current architecture

The reusable `codex-provider-runtime` project owns:

- exact-version source acquisition, patching, build, signing, activation, and
  fail-safe controls;
- the narrow model-to-provider Rust policy for `thread/start` and `thread/resume`;
- a stable `CODEX_CLI_PATH` launcher;
- catalog/provider reconciliation and live acceptance tests.
- the App Server `thread/list` all-provider default required for shared Desktop
  and phone Remote history.

The active install is under `~/.codex/provider-runtime/` (the stable launcher
derives its root from its own location). Current generic LaunchAgents are:

- `com.codex.provider-runtime.environment`;
- `com.codex.provider-runtime.updater`.

The old JavaScript shim and loopback DeepSeek gateway are retired. Keep their
labels only for migration cleanup; do not reinstall them.

### Bundled backend layout

The bundled CLI path is not stable across Desktop updates. Older Desktop keeps
it at `Contents/Resources/codex`; Desktop `26.924.22138` (bundled Codex
`0.158.0-alpha.2.1`) moved it into `Contents/Resources/codex-cli`, described by
`codex-package.json` (`layoutVersion` 1, entrypoint `bin/codex`, backend
`codex-cli/CodexCLI.app/Contents/MacOS/codex`).

Resolve the layout on every run instead of baking one path into the launcher or
manager: explicit `CODEX_OFFICIAL_CLI_PATH`, then legacy `Resources/codex`,
then the `codex-cli` entrypoint with the app-bundle executable as fallback.
Keep the launcher, the manager, and the `codex-provider` CLI on that same
resolution, and let the code-mode host and updater watch paths follow the
detected layout.

Failure signature to recognize: Desktop stops on `Organization settings could
not be loaded` / `The app is paused until your organization settings can be
loaded safely` (`desktop.workspacePolicyRecovery`) when the app-server startup
policy read never completes. A launcher that points at a removed or
non-executable backend exits `127` and produces exactly that symptom, because
Desktop resolves the app-server from `CODEX_CLI_PATH` before its own bundled
path. `codex-provider logs` shows `official Codex backend is missing`, and
`codex-provider status` prints the resolved `official_binary`. This is not a
network or organization-policy problem, and it must not be worked around by
signing out or rewriting account state.

## Upgrade contract

Require all of the following before activation:

1. Match the bundled Codex version to the exact public source tag.
2. Stop on patch-anchor or source-structure drift.
3. Allow lockfile normalization only for local workspace version changes.
4. Run provider-route and thread-list unit tests, including a negative test for
   every visible but unsupported model family.
5. Build the required Codex binaries with the pinned toolchain and lockfile.
6. Record official binary, patch asset, source commit, and custom binary hashes.
7. Run protocol smoke tests for DeepSeek Flash/Pro/GPT routing, DeepSeek
   resume continuity, and omitted versus empty all-provider history.
8. Run a live App Server tool loop against the currently documented endpoint.
9. Atomically activate only after all checks pass.

An unattended source-build failure is fingerprinted by the official Codex
binary and build recipe, so the scheduler does not repeat an unchanged cold
compile every 15 minutes. Prebuilt lookup failures have a separate short retry
window, allowing a later schedule to find a published artifact. After
activation, keep the active and one rollback release, remove source worktrees,
and retain the Cargo target up to its configurable 24 GiB default cap; explicit
cleanup clears it.

When reuse is impossible, build with one Cargo job and a memory-bounded release
profile (LTO disabled, one codegen unit, debug and symbols removed). Keep the
exact-version, unit-test, and protocol-smoke gates unchanged.

For a repository-driven patch update, unload the scheduled updater and install
the new manager/asset before building. Restore support agents after either a
successful or failed attempt; otherwise an unavailable prebuild could leave
the scheduler unloaded and prevent a later retry. Preserve the previous
release, and let the stable launcher use the official backend whenever that
release does not match the bundled Codex version.

`auto` prefers a same-recipe local release, then a GitHub archive whose
attestation is verified against this repository's signer workflow on
`refs/heads/main`, and finally a source build. `prebuilt-only` is a persistent
machine policy that never compiles. The archive contains only patched Codex and
its manifest; each Mac uses its own same-version bundled host. The manifest's
macOS 13.0 floor follows the Desktop application requirement.

The stable launcher must use the official bundled backend on version mismatch,
missing release, disabled state, or failed rebuild. Never use an old custom
backend with a newer client.

## Acceptance checks

Do not infer routing from the picker. Confirm:

- the loaded catalog contains only currently supported DeepSeek models;
- the provider endpoint and wire API match current official documentation;
- Desktop GPT rollout uses `model_provider = openai`;
- Desktop DeepSeek rollout uses a supported model and
  `model_provider = deepseek`;
- phone Remote has the same pairing after both new-thread creation and resume
  when remote access is in scope;
- `thread/list` without `modelProviders` includes the same interactive threads
  as an empty all-provider filter, while explicit filters remain exact;
- a structured shell command executes and its hidden result is independently
  verified;
- no authentication, unsupported-model, fallback, or retired-service error is
  present.

## Evolving the route table

Do not generalize the Rust policy from an exact model to `deepseek-*` merely
because a new name appears. First verify official Responses/Codex support and a
real tool loop. Conversely, do not keep a model excluded solely because this
reference predates its support. Once evidence and tests pass, update the route
table, catalog contract, compatibility matrix, manifest patch identifier, and
this reference together.

## Safe fallback and secrets

Use `codex-provider disable`, restart Desktop, and retain releases and evidence.
Keep API keys in Keychain or an environment-backed secret. Never place them in
the project, logs, manifests, prompts, or rollout diagnostics. Never rewrite
stored thread provider metadata.
