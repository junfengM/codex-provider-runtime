# Auto-review 与自定义 provider 的兼容问题

状态：通过模型目录原生配置；运行时必须依据当前 Codex 版本重新验证。

## 现象

旧实现使用自动 reviewer 时，需要升级审批的操作（沙箱外命令、被拦截的网络请求、受限写入等）曾报告：

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

## 当前实现

DeepSeek 模型目录将 `auto_review_model_override` 设置为 `deepseek-v4-flash`，Codex 会直接
用 Flash 创建 reviewer，并优先使用 Flash 支持的 `low` effort。Codex 侧仍保留 reviewer
会话身份、审批协议和结果处理，不需要本机模型名代理。

这意味着审批判断由 DeepSeek V4 Flash 完成，不等同于官方 GPT reviewer。若不接受这一
信任边界，使用下面的手动审批方案。

## 回退：改回手动审批

```toml
approvals_reviewer = "user"
```

写入 `~/.codex/config.toml` 后完全退出并重开 Codex。审批请求直接弹给用户手动确认。
只有在用户明确接受手动审批时才修改此设置。

验证方式：保持 `auto_review`，在 DeepSeek 会话中发起一次需要审批的低风险命令，确认
reviewer 日志不再出现 unsupported model 或 `invalid_request_error`。请求通过 Codex 原生
Responses 客户端直连 DeepSeek 官方 API；本工程不代理审批内容或 bearer header。
