# Codex Provider Runtime

Codex Provider Runtime 是一个 macOS 本地运行时扩展。它让 Codex Desktop 和手机 Remote
在新建 `deepseek-v4-flash` 对话时使用 DeepSeek provider，同时保留 ChatGPT 登录、GPT 模型和
OpenAI provider。

当前版本只接入 DeepSeek V4 Flash-0731。它不修改或重新签名 `ChatGPT.app`，不重写历史
会话 provider，也不支持在同一旧对话中跨 provider 切换。Flash 通过 DeepSeek 官方原生
Responses API 直连；V4 Pro 在原生 Codex 支持正式发布前不加入模型目录。

## 工程形态

仓库是唯一源码来源，`~/.codex` 只保存安装态与本机状态：

```text
codex-provider-runtime/
├── bin/codex-provider       # 统一生命周期 CLI
├── config/coexist.sh        # ChatGPT/DeepSeek 配置与模型目录管理
├── runtime/                 # 版本构建器、原生补丁和稳定启动器
├── tests/                   # CLI、补丁、模型契约和升级不变量测试
├── docs/                    # 架构、运维和协议边界
└── integrations/codex-skill # 可选的 Codex 操作入口
```

新安装默认使用 `~/.codex/provider-runtime`。如果检测到现有
`~/.codex/deepseek-native-router/current`，CLI 会继续使用旧目录，避免破坏已经验证通过的
安装。也可以通过 `CODEX_PROVIDER_RUNTIME_ROOT` 明确指定路径。

## 为什么需要原生 app-server 补丁

Codex 把模型名和 provider 分开保存。Desktop 模型下拉框可以显示 DeepSeek，但部分
新线程请求仍会省略 provider 或携带默认的 `openai`。手机 Remote 直接进入公共
app-server 路径，因此仅在 Desktop stdin 前增加 JavaScript shim 无法覆盖手机请求。

本项目在 app-server 的公共协议层维护两项互不干扰的兼容修复。新线程仍执行窄路由：

```text
deepseek-v4-flash + provider 缺失/openai  → deepseek
其他模型（包括尚未接入的 DeepSeek）      → 不改路由
GPT 模型                                 → 保持 OpenAI
显式第三方 provider                      → 保持调用方选择
旧线程/turn/start                 → 不修改
```

`thread/list` 恢复官方协议语义：调用方未传 `modelProviders`、传 `null` 或传空数组时均
返回全部交互式 Provider；显式传入 `openai` 或 `deepseek` 时仍严格过滤。这样 Desktop 和
手机 Remote 从任意当前对话返回历史列表时，都不会把另一个 Provider 的对话隐藏。

路由完成后，DeepSeek provider 的 Responses 请求直达官方接口：

```text
Codex Responses request → https://api.deepseek.com/responses

codex-auto-review → deepseek-v4-flash（low effort）
```

## 快速开始

前置条件：macOS、`/Applications/ChatGPT.app`、Git、rustup/Cargo、`jq`、`sqlite3`、
`ripgrep`，以及可访问官方 `openai/codex` 仓库。

```bash
git clone <private-repository-url>
cd codex-provider-runtime

./bin/codex-provider prerequisites
./bin/codex-provider keychain-set
./bin/codex-provider install
```

`keychain-set` 会在终端中隐藏输入，不会把 API Key 写进仓库或聊天记录。安装完成后完全
退出并重新打开 ChatGPT/Codex Desktop，再执行：

```bash
./bin/codex-provider doctor --live
```

可选安装全局命令和 Codex skills：

```bash
./bin/codex-provider link-cli
./bin/codex-provider skill-install
```

`skill-install` 同步 `codex-model-coexist` 与 `codex-provider-runtime`，并把旧版本移动到安装
目录下的可恢复备份，确保新发现进入后续诊断和升级流程。

## 常用命令

```bash
codex-provider status
codex-provider doctor
codex-provider doctor --live
codex-provider update
codex-provider verify
codex-provider test-deepseek
codex-provider appserver-smoke
codex-provider history deepseek
codex-provider logs 200
codex-provider disable
codex-provider enable
codex-provider uninstall
```

- `disable`：保留安装与凭据，下一次启动回退官方后端；
- `enable`：解除禁用标记，但仍要求版本完全匹配；
- `uninstall`：卸载 LaunchAgent 和环境入口，保留 releases、配置与 Keychain；
- `test-deepseek`：本地 CLI 真实结构化工具调用闭环；
- `appserver-smoke`：使用手机 Remote 相同的 app-server 公共协议，执行隐藏 SHA-256
  挑战并验证本机 `commandExecution`；
- `doctor --live`：组合结构检查与一次临时 DeepSeek 请求。

## 安全升级模型

更新器读取客户端内置 Codex 版本，只获取完全匹配的 `rust-v<version>` 官方标签。补丁
锚点、Cargo.lock、路由/历史单元测试、两个 release 二进制、协议 smoke、版本号、签名和摘要全部
通过后，才会原子切换 `current`。

从仓库执行 `codex-provider update` 时会先暂停已加载的定时更新器并同步新版补丁资产，
构建和验证结束后再重新加载。这样旧更新器无法在新 release 激活与支持文件同步之间把
`current` 竞态切回旧补丁；后台定时更新仍直接使用已安装、已同步的管理器。

若客户端版本与自定义发布不一致、精确标签尚未发布、源码结构改变或构建失败，稳定启动器
会使用 ChatGPT.app 内置官方后端。它不会让旧自定义二进制冒充新版本。此时 GPT 继续可用，
而 DeepSeek 新线程路由可能暂时不可用，直到补丁适配新版本。

## 验收标准

模型出现在下拉框不代表路由成功。完整验收要求：

1. Desktop GPT 新对话记录 `model_provider = openai`；
2. Desktop DeepSeek 新对话记录 `model = deepseek-v4-*` 和
   `model_provider = deepseek`；
3. `appserver-smoke` 记录 `model_provider = deepseek`，产生真实 `commandExecution`，隐藏
   SHA-256 挑战与最终消息匹配；
4. `thread/list` 省略 `modelProviders` 与传空数组返回相同线程集合，且显式 Provider 过滤
   仍然有效；
5. 没有认证、unsupported model、fallback 或旧 JavaScript router 错误；
6. 实际 app-server 来自当前版本匹配的 `current/codex`。

## 开发

```bash
make check
```

测试与 secret scan 必须在提交前通过。仓库不应包含 `~/.codex` 配置、API Key、会话、
模型缓存、LaunchAgent plist、构建缓存或编译后的 Codex 二进制。

详细设计见 [架构说明](docs/architecture.md)、[兼容矩阵](docs/compatibility.md) 和
[运维手册](docs/operations.md)。
