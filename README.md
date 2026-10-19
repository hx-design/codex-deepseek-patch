# Codex 模型扩展补丁

把已有的**云端接口和本地模型**接入 Codex 原生模型菜单，在同一个聊天中切换模型，按各模型能力选择思考档位与上下文。

支持 **Gemini、Grok、Opus、Ollama、LM Studio 和其他 Responses 兼容接口**，保留原生 GPT。使用现有模型服务与登录配置，在 Codex 内完成模型选择和聊天。

本分支为多模型扩展版 **`0.4.6-proxy.1`**，更新通道为 `codex/existing-proxy-models`。

[English](README.en.md) · [验证记录](docs/VALIDATION.md) · [实现说明](docs/ARCHITECTURE.md) · [MIT](LICENSE)

- 在**同一个已保存的本机聊天**中切换已接入模型与 GPT，保留聊天 ID 和历史；等当前回复结束后切换。
- 思考程度按模型实际能力选择，逐回合生效。
- 上下文默认使用模型最大值；保留用户主动调小的窗口，修复旧聊天保存的 128K 默认值与压缩后的窗口回落。
- 工具按需发现，只加载命中的工具定义，避免大量 MCP 工具反复挤满上下文。
- 可选生图工具复用现成 EasyCLI/CLIProxyAPI：聊天中的 Grok、Gemini、GPT 等模型可调用 GPT Image、Grok Imagine 或 Gemini 图片模型。
- Codex 更新后检测兼容性；兼容时自动补装。补丁自身、模型列表与规格也可自动更新。
- Python / Node.js 标准库，无第三方依赖、数据库或额外管理界面。完整支持原生工具协议的接口可以直连；现有代理与需要适配的本地模型使用按需启动、空闲 120 秒后退出的小兼容进程。

非官方补丁。仓库只含补丁源码，不含官方应用、提取后的程序、模型提示词或凭据。支持 macOS Apple Silicon 与 Windows 官方桌面应用，需要 **Python 3.11+、Node.js 18+**。

## 已落地的轻量化设计

| 设计 | 当前实现 | 使用上的收益 |
| --- | --- | --- |
| 复用 Codex 界面 | 使用原生模型菜单、聊天和权限体系 | 模型选择与工作都在已有应用内完成 |
| 标准库实现 | Python / Node.js 标准库，配置与状态使用 JSON / TOML | 安装不需要额外 Python/npm 包或数据库服务 |
| 能直连就直连 | 完整支持原生工具发现协议的模型接口直接连接 | 这类请求无需经过补丁的中转进程 |
| 按需适配 | 转换工具发现与自定义调用格式；只聊天模型去掉工具声明；严格模板整理消息 | 复用现有模型服务，兼容进程空闲约 120 秒后退出 |
| 工具按需加载 | 先发送工具搜索入口，命中后才加载对应定义 | 工具数量增加时，不再把全部定义塞进每次请求 |
| 系统定时调度 | launchd / Windows 任务计划程序按周期执行一次检查 | 检查结束后进程退出；不保持常驻监测循环 |
| 按变化更新 | 比较应用、运行代码和模型快照摘要；复用已校验的版本源码包 | 未变化时不重新构建，重复检查减少下载和处理 |
| 简化接入操作 | `connect` 发现并保存连接，`build --verify` 合并安装前检查 | 减少手改配置和逐项执行验证的步骤 |
| 复用已有会话 | 通过原生接口恢复同一个聊天 ID，保留历史、权限与工作目录 | 不维护另一套聊天数据库或迁移历史副本 |

`0.4.6-proxy.1` 的更新监测运行文件共 **26 个，约 316 KiB**，含安装验证脚本。这个数字统计源码，不含 Python、Node.js、Codex 应用、外部模型服务及模型权重。安装仍保留应用副本和恢复备份；旧构建目前需要手动管理。

### 0.4.2 修复：Windows 后台自动更新

- 修正后台校验对 `pythonw.exe` 与 `python.exe` 的预期，避免把正确的凭据读取配置误判成错误；Windows CI 同时测试普通和无窗口 Python。
- 监测任务在稳定的状态目录启动，更新时先离开将被替换的 runtime 目录。已有任务在构建及全部安装检查通过后迁移工作目录，保留启用状态、运行计划、身份和命令，并保存原定义。
- 扩展版继续跟随 `codex/existing-proxy-models`；独立直连版继续跟随 `main`。更新包验证通道、版本和摘要，两个版本各自更新。

若旧版已出现“应用更新成功，但监测程序仍为旧版”，在更新后的仓库目录运行一次 `python monitor.py run-once --sync-now` 恢复激活；之后由正常监测继续更新。

### 0.4.1 修复：大量工具引起的反复压缩

旧扩展版关闭了代理和本地模型的工具发现能力，大量 MCP 工具定义会提前进入请求；压缩聊天后这些固定定义仍会重新加入。此问题也影响旧版 GitHub 默认配置，模型窗口设为最大值仍可能触发。

新版本默认启用支持工具的模型按需发现，并为普通函数接口转换原生工具搜索协议。搜索、工具执行和权限判断仍由 Codex 完成；压缩请求不会重新带入工具定义。上下文最大值、用户调小设置和原聊天历史保留。更新后重启 Codex，再继续原聊天。

## macOS 更新与系统授权

本版在本机创建并复用固定的代码签名证书，私钥保存在当前用户的登录钥匙串中。签名身份记录独立存放于 `~/Library/Application Support/CodexDeepSeekPatch/signing/`，DS 与扩展版共用本机身份，但继续使用各自的更新通道。

应用更新时沿用相同的身份要求，并保留麦克风、相机、Apple Events 等可本地使用的能力声明。只重签修改过的 Framework、其辅助程序与外层应用；未修改的内置 CLI、Sparkle 等保留官方签名。安装前后都会核对身份和能力声明。macOS 通过签名身份识别跨版本的钥匙串与隐私授权，见 [Apple TN3127](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)。

**第一次从旧临时签名迁移时，按 macOS 提示重新确认一次所需权限；后续补丁更新保持这个身份。** 原有官方身份的专属应用组和钥匙串组由厂商证书控制，本地补丁不能冒用。更新不会修改 TCC 授权数据库或清空用户钥匙串。

初次构建自动准备签名身份，也可提前运行 `python3 patch.py setup-signing`。请保留登录钥匙串中的 **Codex Model Patch Local Signing** 条目；已有证书丢失或与已安装版本不一致时，更新会停止并提示恢复，不会偷偷换证书。旧版本构建目录可以按恢复需要管理，签名身份不存放在这些目录内。

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

## 可选：在聊天里调用生图

自定义聊天模型不会自动获得 Codex 原生 `image_gen`。扩展版提供一个独立的图片工具入口，聊天模型和出图模型可以分别选择。例如用 Grok 4.7 聊天，让它调用 Gemini 图片模型或 GPT Image 生成图片。

先在现成的 EasyCLI/CLIProxyAPI 中配置相应账户。启用时只读取其配置文件位置，不复制 OAuth 或 API 密钥：

```sh
python patch.py setup-images --proxy-config "/path/to/CPA/config.yaml"
```

Windows 同样使用 `python`，将路径替换为本机实际位置。启用自动更新时，让工具指向该版本通道的固定运行目录，后续随扩展版更新：

```sh
python patch.py setup-images --proxy-config "/path/to/CPA/config.yaml" \
  --script "/path/to/extension-monitor/runtime/cpa_images.py"
```

重启 Codex 后，模型可发现 `cpa_images.list_image_models` 与 `cpa_images.generate_image`。先查询当前账户暴露的图片型号，再按用户要求选择型号。生成或编辑返回图片预览及准确本地文件路径；支持最多 5 张本地参考图。输出目录使用绝对路径，生成新文件，不覆盖原图。

| 图片后端 | 接口 | 参数 |
| --- | --- | --- |
| GPT Image | `/v1/images/generations`、`/v1/images/edits` | `size`、`background` |
| Grok Imagine | 同上 | `aspect_ratio`、`resolution` |
| Gemini 图片模型 / Nano | `/v1beta/models/{model}:generateContent` | `aspect_ratio`、`resolution` |

这里只调用用户已有 CPA 接口，使用该账户实际开放的模型；普通 Gemini/Grok 聊天型号与图片型号分别处理。此功能通过可选 MCP 提供，不冒用 Codex 原生工具身份。它使用 Python 标准库、无额外依赖，不新增 HTTP 监听服务；由 Codex 管理 stdio 进程，只在调用时发起图片请求，失败不自动重试生成。

配置前保存完整备份；同名的用户自定义工具配置会保留并提示核对。移除本工具使用 `python patch.py setup-images --disable`，保留模型、凭证与其他 MCP 配置。DS 独立直连版不安装这项可选功能。

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

`--verify` 一次执行目录、思考档位、上下文、同聊天切换、160 个 MCP 工具的发现/执行/压缩恢复及隔离启动检查；全部使用临时配置与本机模拟接口，不消耗模型额度。可用 `python3 patch.py verify BUILD` 重跑检查。测试窗口可能短暂出现。

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

- GPT 刷新原生目录；代理自动加入现有 **Grok、Gemini Flash、Opus** 系列的新版本，再核对厂商规格。可选的官方直连服务从其官方接口同步。
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
| Grok 4.5 | 500,000 | low / medium / high | [xAI](https://docs.x.ai/developers/models/grok-4.5) |
| Grok 4.7 | 500,000 | low / medium / high / xhigh | [xAI](https://docs.x.ai/developers/grok-4-7) |
| Gemini 3.8 Flash | 1,048,576 | low / medium / high | [Google](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) |
| Opus 4.6 Thinking | 1,000,000 | low / medium / high / max | [Anthropic](https://platform.claude.com/docs/en/models/opus-4-6/overview) |
| 本地模型 | 从服务元数据读取 | 按实际能力 | [Ollama](https://docs.ollama.com/api/openai-compatibility)、[LM Studio](https://lmstudio.ai/docs/developer/openai-compat/responses) |
| DeepSeek Flash（可选） | 1,048,576 | low / high / max | [DeepSeek](https://api-docs.deepseek.com/quick_start/agent_integrations/) |

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

恢复核对当前应用和配置，返回安装前状态。macOS 副本采用固定本机证书签名；Windows 使用独立受管副本；资源完整性检查保持开启。不要上传 `.local/`、构建产物、日志或个人配置。[NOTICE](NOTICE) 说明第三方资源归属。

## 其他版本

只需要 DeepSeek 官方直连时，可使用 [main 分支的独立直连版](https://github.com/limyum-creator/codex-deepseek-patch/tree/main)。本分支按需组合云端接口与本地模型，各版本使用独立更新通道。
