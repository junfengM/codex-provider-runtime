# Codex Provider Runtime

Codex Provider Runtime 是一个 macOS 本地运行时扩展。它让 Codex Desktop 和手机 Remote
在新建或恢复 `deepseek-flash` 对话（含已退役名称的历史线程）时保持使用 DeepSeek
provider，同时保留 ChatGPT 登录、GPT 模型和 OpenAI provider。

当前版本只接入 DeepSeek V4.1 Flash（`deepseek-flash`，2026-09-10 发布，支持图片输入）。
`deepseek-v4-flash` 与 `deepseek-v4-pro` 已被 DeepSeek 退役并转发到 V4.1 Flash：目录里
只保留 V4.1 Flash，但路由仍然接受这两个旧名，历史线程可以继续恢复与发送。它不修改或
重新签名 `ChatGPT.app`，不重写历史会话 provider，也不支持在同一旧对话中跨 provider
切换。该模型通过 DeepSeek 官方原生 Responses API 直连。

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
新线程或恢复线程请求仍可能省略 provider 或携带默认的 `openai`。手机 Remote 直接进入公共
app-server 路径，因此仅在 Desktop stdin 前增加 JavaScript shim 无法覆盖手机请求。

本项目在 app-server 的公共协议层维护两项互不干扰的兼容修复。新线程和恢复线程都执行窄路由：

```text
deepseek-flash / deepseek-v4-flash（旧名）/ deepseek-v4-pro
                        + provider 缺失/openai → deepseek
其他模型（包括尚未接入的 DeepSeek）          → 不改路由
GPT 模型                                     → 保持 OpenAI
显式第三方 provider                          → 保持调用方选择
DeepSeek 线程恢复/后续 turn                   → 继承 deepseek provider
```

`thread/resume` 会在客户端只带模型、不带 provider 时重新绑定已验证的 DeepSeek provider，
但不会改写 SQLite 或 rollout 中已经保存的线程元数据。这样手机 Remote 从后台恢复、重连
或重新进入线程后，后续消息不会落回 ChatGPT 认证链路。跨 provider 的主动线程迁移仍不属于
本工程范围。

`thread/list` 恢复官方协议语义：调用方未传 `modelProviders`、传 `null` 或传空数组时均
返回全部交互式 Provider；显式传入 `openai` 或 `deepseek` 时仍严格过滤。这样 Desktop 和
手机 Remote 从任意当前对话返回历史列表时，都不会把另一个 Provider 的对话隐藏。

路由完成后，DeepSeek provider 的 Responses 请求直达官方接口：

```text
Codex Responses request → https://api.deepseek.com/responses

codex-auto-review → deepseek-flash（low effort）
```

## 快速开始

前置条件：macOS、`/Applications/ChatGPT.app`、Git、rustup/Cargo、`jq`、`sqlite3`、
`ripgrep`，以及可访问官方 `openai/codex` 仓库。

```bash
git clone https://github.com/junfengM/codex-provider-runtime.git
cd codex-provider-runtime

./bin/codex-provider prerequisites
./bin/codex-provider keychain-set
./bin/codex-provider install
```

`keychain-set` 必须在你能看到的 macOS Terminal 中执行：它会隐藏输入，并把 API Key
保存到 macOS Keychain，不会写进仓库、配置文件、日志或聊天记录。不要把 API Key
输入到 Codex/ChatGPT 聊天窗口，也不要通过 Agent 的不可见 stdin 录入。需要让 Agent
确认凭据是否已经保存时，使用只检查存在性的命令：

```bash
./bin/codex-provider keychain-status
```

安装完成后完全退出并重新打开 ChatGPT/Codex Desktop，再执行：

```bash
./bin/codex-provider doctor --live
```

如果手机 Remote 已显示 DeepSeek 的“最高”推理强度，但 Desktop 只显示“轻度”和“高”，
请在 Desktop 的“设置 → 配置/组态 → 模型功能 → 可用推理强度”中勾选 `Max`。Desktop
会把模型声明的 `low`/`high`/`max` 与本机启用的推理强度取交集；这个显示偏好目前不与
手机端同步，也不属于 Provider 路由故障。修改后重新打开模型菜单即可，通常无需重启。

可选安装全局命令和 Codex skills：

```bash
./bin/codex-provider link-cli
./bin/codex-provider skill-install
```

`skill-install` 同步 `codex-model-coexist` 与 `codex-provider-runtime`，并把旧版本移动到安装
目录下的可恢复备份，确保新发现进入后续诊断和升级流程。如果本机存在跨 agent 共享技能目录
（默认 `~/ai/shared/skills/`，可用 `CODEX_SHARED_SKILLS_ROOT` 覆盖）且其中已有同名技能，
它会一并刷新到同一版本，避免两份 skill 漂移。

## 更新已有安装 / 多机复用

仓库只是源码；真正生效的是安装态（`~/.codex/provider-runtime`）和模型目录
（`~/.codex/models.json`）。因此**已经装过的机器**拉取新版本后，还要显式部署一次：

```bash
cd <仓库目录>
git pull
./bin/codex-provider update        # 部署管理器/补丁并激活；同源码 tag 时秒级复用，不重新编译
./bin/codex-provider configure     # 刷新模型目录（update 不会动目录）
./bin/codex-provider skill-install # 同步两个 skill（含共享目录副本）
# 完全退出并重新打开 ChatGPT/Codex Desktop
./bin/codex-provider doctor        # 可选：结构 + 路由检查；加 --live 会发一次真实请求
```

`update` 只更新运行时二进制，`configure` 才更新模型目录，两者都要跑。新机器直接用
`install` 即可，它内部已经包含 `configure`、构建与激活。

三样东西不在仓库里，必须在每台机器本机处理：`/Applications/ChatGPT.app` 本体、
Keychain 里的 DeepSeek API Key（`keychain-set`，不会跨机同步），以及可选的
`~/.local/bin/codex` 转发 shim（用于 Open Design + Local DeepSeek，源文件在共享 skills
目录）。

## 常用命令

```bash
codex-provider status
codex-provider doctor
codex-provider doctor --live
codex-provider update
codex-provider cleanup
codex-provider verify
codex-provider test-deepseek deepseek-flash
codex-provider keychain-status
codex-provider appserver-smoke
codex-provider history deepseek
codex-provider logs 200
codex-provider disable
codex-provider enable
codex-provider uninstall
```

- `disable`：保留安装与凭据，下一次启动回退官方后端；
- `cleanup`：保留当前 release 和一个回退 release，移除源码 worktree 与 Cargo 构建产物；
- `enable`：解除禁用标记，但仍要求版本完全匹配；
- `update`：Desktop 更新后重新认证并激活；当公开源码 tag 与补丁资产未变时，直接复用已
  验证的自编译二进制（仍会重跑 code-mode-host 检查和协议 smoke），需要强制源码重建时用
  `update --no-reuse`；
- `uninstall`：卸载 LaunchAgent 和环境入口，保留 releases、配置与 Keychain；
- `test-deepseek [model]`：对 `deepseek-flash`（或退役名的兼容路由）跑一次本地 CLI 真实
  结构化工具调用闭环；
- `keychain-status`：只检查 DeepSeek Keychain 项是否存在，不读取或打印 API Key；
- `appserver-smoke [model]`：使用手机 Remote 相同的 app-server
  公共协议，执行隐藏 SHA-256 挑战并验证本机 `commandExecution`；
- `doctor --live`：组合结构检查与一次临时 DeepSeek 请求。

## 安全升级模型

更新器读取客户端内置 Codex 版本，只获取完全匹配的 `rust-v<version>` 官方标签。补丁
锚点、Cargo.lock、路由/历史单元测试、补丁版 Codex 二进制、Desktop 同版本内置且已签名的
`codex-code-mode-host`、协议 smoke、版本号和摘要全部通过后，才会原子切换 `current`。
独立 host 不包含 Provider 路由补丁，因此直接复用客户端随附版本，避免上游尚未发布
对应 V8 预编译资产时阻塞路由升级。

从仓库执行 `codex-provider update` 时会先暂停已加载的定时更新器并同步新版补丁资产，
构建和验证结束后再重新加载。这样旧更新器无法在新 release 激活与支持文件同步之间把
`current` 竞态切回旧补丁；后台定时更新仍直接使用已安装、已同步的管理器。

后台更新失败后会按“Codex 二进制 + Provider 补丁”记录退避标记；相同输入不再每 15 分钟
重复下载、打补丁或编译，只有 Codex 或补丁发生变化时才自动重试。手动执行 `update` 仍会
强制重试。每次成功激活后，运行时只保留当前 release 和一个回退 release，并清除源码
worktree 与 Cargo 构建产物，避免长期累积多 GB 缓存。

必须从源码构建时，运行时固定使用单 Cargo job，并关闭 release LTO、单 codegen unit、
移除调试信息和符号，以控制 Desktop Mac 上的编译与链接内存峰值。最终二进制仍须通过
版本校验、路由单元测试和完整 app-server 协议 smoke 才能激活。

若客户端版本与自定义发布不一致、精确标签尚未发布、源码结构改变或构建失败，稳定启动器
会使用 ChatGPT.app 内置官方后端。它不会让旧自定义二进制冒充新版本。此时 GPT 继续可用，
而 DeepSeek 新线程/恢复线程路由可能暂时不可用，直到补丁适配新版本。

## 验收标准

模型出现在下拉框不代表路由成功。完整验收要求：

1. Desktop GPT 新对话记录 `model_provider = openai`；
2. Desktop DeepSeek 新对话记录 `model = deepseek-v4-*` 和
   `model_provider = deepseek`；
3. Desktop/phone Remote 恢复 DeepSeek 线程后，`model_provider` 仍为 `deepseek`，后续 turn
   不进入 ChatGPT WebSocket 或 ChatGPT 认证错误；
4. `appserver-smoke` 记录 `model_provider = deepseek`，产生真实 `commandExecution`，隐藏
   SHA-256 挑战与最终消息匹配；
5. `thread/list` 省略 `modelProviders` 与传空数组返回相同线程集合，且显式 Provider 过滤
   仍然有效；
6. 没有认证、unsupported model、fallback 或旧 JavaScript router 错误；
7. 实际 app-server 来自当前版本匹配的 `current/codex`。

## 开发

```bash
make check
```

测试与 secret scan 必须在提交前通过。仓库不应包含 `~/.codex` 配置、API Key、会话、
模型缓存、LaunchAgent plist、构建缓存或编译后的 Codex 二进制。

详细设计见 [架构说明](docs/architecture.md)、[兼容矩阵](docs/compatibility.md) 和
[运维手册](docs/operations.md)。
