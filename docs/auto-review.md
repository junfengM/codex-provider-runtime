# Auto-review 与自定义 provider 的兼容问题

状态：已知兼容性边界；运行时必须依据当前 Codex 版本重新验证。

## 现象

`approvals_reviewer = "auto_review"` 时，需要升级审批的操作（沙箱外命令、被拦截的网络请求、受限写入等）会报：

```text
Automatic approval review failed:
The supported API model names are deepseek-v4-pro or deepseek-v4-flash, but you passed codex-auto-review.
```

## 根因

自动审批的 reviewer 是一个独立的 Codex 子会话，它继承当前会话的 `model_provider_id`。当会话运行在 `deepseek` provider 上时，reviewer 会话被创建为：

- `model = codex-auto-review`（官方内置评审模型，模型目录中有对应条目）
- `model_provider_id = deepseek`

`codex-auto-review` 不是 DeepSeek 接口支持的模型名，请求被 DeepSeek API 以 `invalid_request_error` 拒绝，评审 agent 启动即失败，升级审批以“拒绝”形式返回主 agent。

证据：`~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl` 中 reviewer 会话的 `thread_settings` 含 `"model":"codex-auto-review","model_provider_id":"deepseek"`。

## 影响范围

- 只影响需要审批的动作；沙箱内已允许的操作不受影响。
- 只在 provider 为 `deepseek` 的会话触发；OpenAI/GPT 会话的 reviewer 走 OpenAI，正常。
- 这不是审批策略的拒绝，而是评审模型路由不兼容导致的启动失败。

## 方案 A：改回手动审批

```toml
approvals_reviewer = "user"
```

写入 `~/.codex/config.toml` 后完全退出并重开 Codex。审批请求直接弹给用户手动确认。
只有在用户明确接受手动审批时才修改此设置。

## 方案 B：本地模型名映射代理（设计，未实现）

让评审模型能在 DeepSeek provider 上运行：

1. 本地起一个 OpenAI 兼容代理，把 `[model_providers.deepseek].base_url` 指向它；
2. 代理把请求体中的 `model = "codex-auto-review"` 改写为 `deepseek-v4-pro`，其余原样转发到 `https://api.deepseek.com/`；
3. 保持 `approvals_reviewer = "auto_review"`。

代价与风险：

- 评审判断由 DeepSeek 模型完成，不再是官方 GPT 评审；
- 需要常驻本地进程（如 LaunchAgent），多一个故障点；
- 属于非官方集成，App/协议更新后需要重新验证；
- 代理需要隐藏转发用的 API Key，不能写入开源仓库。

验证方式：改回 `auto_review` 后，在 DeepSeek 会话中发起一次需要审批的命令，确认 reviewer 日志不再出现 `invalid_request_error`。
