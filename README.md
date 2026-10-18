# Codex 模型扩展补丁

在 Codex 桌面版的原生模型菜单中使用现有云端接口和本地模型，保留 GPT。**不再强制绑定 DeepSeek，也不需要另装一个路由管理软件。**

| 版本 | 适合谁 | 更新通道 |
| --- | --- | --- |
| [DS 版](https://github.com/limyum-creator/codex-deepseek-patch/tree/main) | GPT + DeepSeek 官方直连 | `main`，独立更新 |
| **本分支：扩展版 `0.4.0-proxy.1`** | GPT + 按需选择的 DeepSeek、已有代理、本地模型 | `codex/existing-proxy-models`，独立更新 |

[English](README.en.md) · [验证记录](docs/VALIDATION.md) · [实现说明](docs/ARCHITECTURE.md) · [MIT](LICENSE)

- 在**同一个已保存的本机聊天**中切换已接入模型与 GPT，保留聊天 ID 和历史；等当前回复结束后切换。
- 思考程度按模型实际能力选择，逐回合生效。
- 上下文默认使用模型最大值；保留用户主动调小的窗口，修复旧聊天保存的 128K 默认值与压缩后的窗口回落。
- Codex 更新后检测兼容性；兼容时自动补装。补丁自身、模型列表与规格也可自动更新。
- Python / Node.js 标准库，无第三方依赖、数据库或额外管理界面。原生兼容的模型直连已有 Responses 接口；只聊天模型与严格消息模板使用按需启动、空闲 120 秒后退出的小兼容进程。

非官方补丁。仓库只含补丁源码，不含官方应用、提取后的程序、模型提示词或凭据。支持 macOS Apple Silicon 与 Windows 官方桌面应用，需要 **Python 3.11+、Node.js 18+**。

## 1. 选择接口

```sh
git clone --branch codex/existing-proxy-models https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
```

### 本地模型：Ollama、LM Studio、其他兼容服务

先运行已有模型服务。以下命令发现模型及规格并保存连接，不必手改 Codex 设置：

```sh
python3 patch.py connect --name local --kind ollama --url http://127.0.0.1:11434/v1
# LM Studio：将 kind 改为 lmstudio，通常端口为 1234。
# 其他支持 /v1/responses 的服务：--kind openai。
```

命令输出 `saved` 配置路径；下一步把它传给 `--connections`。默认接入该服务全部可用生成模型，排除 embedding；`--models ID1 ID2` 可限定型号。工具模型可执行任务，只聊天模型会标记“聊天”。

- 客户端目录不会修改本地服务的加载参数；需要扩展服务端窗口时，在原服务中设置。[Ollama 窗口说明](https://docs.ollama.com/context-length)
- `--respect-server-context`：将本地服务已有的较小窗口作为自定义设置保留；模型最大值仍单独记录。Ollama 优先读取已加载窗口，其次读取模型 `num_ctx`。
- `--key-file /absolute/path/key.txt`：需要认证时读取已有密钥文件，密钥不写入连接配置。
- 通用接口不返回最大上下文时，用 `--models MODEL --context-window 最大值` 补充真实规格；不会猜一个 128K 上限。通用接口需要支持 Responses，纯 Chat Completions 接口暂不适用。
- 已启用监测后再次 `connect` 即更新连接；下次监测自动验证并等待退出安装。更换地址也会触发更新。

### 现有 Gemini / Grok / Opus 代理

复用已配置好的 [EasyCLIProxyAPI](https://github.com/router-for-me/EasyCLIProxyAPI)，在下一步构建参数中加入：

```sh
--proxy-config "/path/to/existing/config.yaml" \
--proxy-base-url "http://127.0.0.1:8317/v1" \
--proxy-models grok-4.5 grok-4.7 gemini-3.8-flash-high claude-opus-4-6-thinking
```

只读取该配置中的客户端 API key，保留现有订阅登录与刷新机制。远程代理使用 HTTPS；回环地址可用 HTTP。可只选其中部分模型。

### 可选 DeepSeek

```sh
python3 patch.py setup-key
```

然后构建时加 `--with-deepseek`。密钥进入 macOS 钥匙串或 Windows 凭据管理器。**扩展版首次安装默认不添加 DS；更新旧安装则继承原来的选择。** 已有 DS 可用 `--without-deepseek` 移除；若它是全局默认模型，先在 Codex 选择保留的默认模型。

## 2. 构建、验证、安装

下面是 macOS 本地模型示例。将连接路径替换为上一步输出的 `saved`；可以同时加入代理参数与 `--with-deepseek`。

```sh
PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/build-040"
python3 patch.py build --output "$PATCH_BUILD" \
  --connections "/path/to/connections.json" --verify
# 正常退出 Codex / ChatGPT 后：
python3 patch.py install "$PATCH_BUILD"
```

Windows 在 PowerShell 中运行同样的命令，将 `python3` 改为 `python`，路径例如：

```powershell
$PatchBuild = Join-Path $env:LOCALAPPDATA "CodexDeepSeekPatch\build-040"
python patch.py build --output "$PatchBuild" --connections "C:\path\connections.json" --verify
# 正常退出应用后：
python patch.py install "$PatchBuild"
```

`--verify` 一次执行目录、思考档位、上下文、同聊天切换和隔离启动检查；全部使用临时配置与本机模拟接口，不消耗模型额度。可用 `python3 patch.py verify BUILD` 重跑检查。测试窗口可能短暂出现。

macOS 默认来源 `/Applications/ChatGPT.app` 或 `/Applications/Codex.app`；其他位置用 `--app`。Windows 自动发现 Store 来源，原包保持只读；副本安装到 `%LOCALAPPDATA%\CodexDeepSeekPatch\app`，通过开始菜单 **Codex DeepSeek** 旧兼容入口启动。

**保留构建目录**：其中含模型目录、恢复数据及必要 helper。Mac 构建与应用需在同一 APFS 卷；Windows 需在同一磁盘卷。应用副本需要磁盘空间，源码本身没有模型权重。

### 更新已有安装

每次使用新构建目录，加 `--baseline "旧构建目录"`。已有连接与用户设置会继承。macOS 同版本重建可用官方签名备份：

```sh
python3 patch.py build --app "/path/to/old-build/original.app" \
  --baseline "/path/to/old-build" --install-target "/Applications/ChatGPT.app" \
  --output "/path/to/new-build" --verify
```

将本地连接加入旧扩展版时，再加 `--connections`。初次安装遇到用户自定义模型目录或同名服务商时停止核对；不会覆盖来源不明的设置。

## 3. 开启自动更新与同步

```sh
python3 monitor.py enable --app "/Applications/ChatGPT.app" --baseline "$PATCH_BUILD"
python3 monitor.py status
python3 monitor.py run-once --sync-now
```

Windows 用 `python`，`--app` 指向官方 Store 来源，`--baseline` 指向已安装构建。

每 **5 分钟**检查应用变化，每 **1 小时**检查补丁和模型规格。两个版本分别验证各自发布清单、提交和摘要，拒绝互相覆盖、降级或摘要不符的包。更新先测试，再等待正常退出安装。结构不兼容、规格不明或检查失败时去重通知，保留可用版本；没有变化时保持安静。

- GPT 刷新原生目录；DS 查询官方接口；代理自动加入现有 **Grok、Gemini Flash、Opus** 系列的新版本，再核对厂商规格。
- 本地服务默认跟随其可用生成模型；明确指定 `--models` 时保留该选择。
- 默认上下文跟随最大值，单模型调小与手动隐藏持续保留。自动压缩默认为当前窗口的 90%；已有更低阈值继续保留。

```sh
python3 monitor.py model-options --model MODEL --context-window 32768
python3 monitor.py model-options --model MODEL --reset-context
python3 monitor.py model-options --model MODEL --hide
python3 monitor.py model-options --model MODEL --show
python3 monitor.py run-once --retry
python3 monitor.py disable
```

连接、偏好与状态保存在本机监测目录；更新安装后重新打开应用即可，旧本机聊天在下一回合应用对应模型窗口。启用监测时可用 `--no-code-updates` 或 `--no-model-sync` 分别关闭对应功能。

## 模型规格与思考档位

2026-10-18 核对的初始预设；后续由同步更新：

| 模型 | 最大上下文 | 思考档位 | 来源 |
| --- | ---: | --- | --- |
| DeepSeek Flash | 1,048,576 | low / high / max | [DeepSeek](https://api-docs.deepseek.com/quick_start/agent_integrations/) |
| Grok 4.5 | 500,000 | low / medium / high | [xAI](https://docs.x.ai/developers/models/grok-4.5) |
| Grok 4.7 | 500,000 | low / medium / high / xhigh | [xAI](https://docs.x.ai/developers/grok-4-7) |
| Gemini 3.8 Flash | 1,048,576 | low / medium / high | [Google](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) |
| Opus 4.6 Thinking | 1,000,000 | low / medium / high / max | [Anthropic](https://platform.claude.com/docs/en/models/opus-4-6/overview) |
| 本地模型 | 从服务元数据读取 | 按实际能力 | [Ollama](https://docs.ollama.com/api/openai-compatibility)、[LM Studio](https://lmstudio.ai/docs/developer/openai-compat/responses) |

`-high` / `-thinking` 是代理模型 ID，仍可逐回合调整档位。非思考模型不显示虚假的档位。Opus 的官网上限为 1M；CLIProxyAPI 8.0.32 Antigravity 静态表标注 200K，实际订阅通道的长请求上限仍由上游决定。[代理模型表](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.32/internal/registry/models/models.json)

同聊天跨提供商切换会将保留的历史发送给新选中的接口。当前回复进行中不切换；远程主机与未登记的自定义提供商保持原流程。

## 检查、恢复与开发

```sh
python3 patch.py status "$PATCH_BUILD"
# 先停监测并正常退出应用：
python3 patch.py restore "$PATCH_BUILD"
# 开发回归：
python3 -m unittest discover -s tests -q
node tests/route.test.cjs
# 可选真实回复 / 工具测试，会使用相应接口额度：
python3 scripts/test_connection.py "$PATCH_BUILD" --model MODEL --run
```

恢复核对当前应用和配置，返回安装前状态。macOS 副本采用 ad-hoc 签名；Windows 使用独立受管副本；资源完整性检查保持开启。不要上传 `.local/`、构建产物、日志或个人配置。[NOTICE](NOTICE) 说明第三方资源归属。
