# Auto-review with a custom provider

## Current mechanism

The internal `codex-auto-review` model name is not a public DeepSeek model.
Without an override, a reviewer sub-session can inherit provider `deepseek` and
fail with an unsupported model error.

The current model catalog sets:

```text
auto_review_model_override = deepseek-v4-flash
```

Codex therefore creates the DeepSeek reviewer with Flash and prefers `low`
reasoning effort under the current cost policy. Codex still owns reviewer
identity, approval protocol, and result handling. No local model-name proxy is
required.

This changes the trust boundary: DeepSeek Flash performs the approval judgment,
not the official GPT reviewer. If the user does not accept that boundary, use
manual approval instead.

## Validation

Trigger one low-risk action that requires approval and confirm the reviewer
rollout uses `deepseek-v4-flash`, completes normally, and has no unsupported
model or authentication error. Do not infer reviewer success from the main
agent's normal responses.

## Evolution rule

The review model and effort are policy choices, not permanent constants. When
DeepSeek publishes a cheaper or more suitable natively supported model, compare
official capabilities, cost, latency, and a real approval flow. Update the
catalog override, tests, compatibility documentation, and this reference only
after the new path passes.
