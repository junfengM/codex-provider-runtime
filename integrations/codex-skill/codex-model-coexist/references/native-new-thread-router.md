# Native new-thread provider router

## Scope

Use the native router when Desktop and phone Remote must start a supported
third-party model through its provider while GPT remains on OpenAI. Keep the
route table evidence-driven and model-exact. The 2026-08-01 verified policy
routes only `deepseek-v4-flash` to `deepseek`.

It changes only new-thread provider normalization. It does not implement
same-thread provider switching.

## Current architecture

The reusable `codex-provider-runtime` project owns:

- exact-version source acquisition, patching, build, signing, activation, and
  fail-safe controls;
- the narrow model-to-provider Rust policy;
- a stable `CODEX_CLI_PATH` launcher;
- catalog/provider reconciliation and live acceptance tests.

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
4. Run provider-route unit tests, including a negative test for every visible
   but unsupported model family.
5. Build the required Codex binaries with the pinned toolchain and lockfile.
6. Record official binary, patch asset, source commit, and custom binary hashes.
7. Run protocol smoke tests for both DeepSeek Flash and GPT routing.
8. Run a live App Server tool loop against the currently documented endpoint.
9. Atomically activate only after all checks pass.

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
- phone Remote has the same pairing when remote access is in scope;
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
