# Native provider continuity router

## Scope

Use the native router when Desktop and phone Remote must start or resume a
supported third-party model through its provider while GPT remains on OpenAI.
Keep the route table evidence-driven and model-exact. The 2026-08-02 verified
policy routes only `deepseek-v4-flash` to `deepseek`.

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

The active install is under `~/.codex/deepseek-native-router/`. Current generic
LaunchAgents are:

- `com.codex.provider-runtime.environment`;
- `com.codex.provider-runtime.updater`.

The old JavaScript shim and loopback DeepSeek gateway are retired. Keep their
labels only for migration cleanup; do not reinstall them.

## Upgrade contract

Require all of the following before activation:

1. Match the bundled Codex version to the exact public source tag.
2. Stop on patch-anchor or source-structure drift.
3. Allow lockfile normalization only for local workspace version changes.
4. Run provider-route and thread-list unit tests, including a negative test for
   every visible but unsupported model family.
5. Build the required Codex binaries with the pinned toolchain and lockfile.
6. Record official binary, patch asset, source commit, and custom binary hashes.
7. Run protocol smoke tests for both DeepSeek Flash/GPT routing, DeepSeek
   resume continuity, and omitted versus empty all-provider history.
8. Run a live App Server tool loop against the currently documented endpoint.
9. Atomically activate only after all checks pass.

For a repository-driven patch update, unload the scheduled updater and install
the new manager/asset before building. Reload it only after the new release is
certified and active; otherwise a still-loaded old updater can race `current`
back to the superseded patch.

The stable launcher must use the official bundled backend on version mismatch,
missing release, disabled state, or failed rebuild. Never use an old custom
backend with a newer client.

## Acceptance checks

Do not infer routing from the picker. Confirm:

- the loaded catalog contains only currently supported DeepSeek models;
- the provider endpoint and wire API match current official documentation;
- Desktop GPT rollout uses `model_provider = openai`;
- Desktop Flash rollout uses `model = deepseek-v4-flash` and
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
