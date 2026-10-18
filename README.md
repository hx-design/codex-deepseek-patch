# Codex DeepSeek Patch · 现有代理扩展版

本分支为 **`codex/existing-proxy-models`**，基于 v0.2.1 的可选多模型扩展，版本标记 `0.2.1-proxy.2`。使用 Grok / Gemini / Claude 需要自行配置并运行 EasyCLIProxyAPI。独立 DS 补丁继续在 [main 分支](https://github.com/limyum-creator/codex-deepseek-patch/tree/main) 维护。

[![Tests](https://github.com/limyum-creator/codex-deepseek-patch/actions/workflows/tests.yml/badge.svg)](https://github.com/limyum-creator/codex-deepseek-patch/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

在 macOS / Windows Codex 桌面版的模型菜单中添加 **DeepSeek 4.1 Flash**，可选接入现成 EasyCLIProxyAPI 提供的 **Grok 4.5 / 4.7、Gemini 3.8 Flash、Claude Opus 4.6 Thinking**，保留 GPT 模型和默认设置。按所选模型，为新建本机聊天选择对应服务商。

**代理扩展版** 新增可选的现有代理接入。GPT 保留原生连接，DeepSeek 保留直连接口，四个可选模型使用已有代理。跨版本兼容检查、更新后自动补装、异常通知和恢复能力继续保留。

这是非官方、本地安装的补丁。仓库只提供补丁源码，不提供 ChatGPT/Codex 应用、提取后的程序、模型系统提示词或 API 密钥。

[English](README.en.md) · [验证记录](docs/VALIDATION.md) · [实现说明](docs/ARCHITECTURE.md)

## 支持范围

| 项目 | 范围 |
| --- | --- |
| macOS | Apple Silicon；应用与构建目录位于同一 APFS 卷 |
| Windows | 官方 Codex 应用；Store 安装作为只读来源，补丁安装到独立的用户目录 |
| 已检查的基线应用 | `26.1013.40934`，build `20770`；具体平台验证见验证记录 |
| 历史已验证 CLI | `0.163.0-alpha.10.2` |
| 默认新增模型 | `deepseek-flash`，显示为 **DeepSeek 4.1 Flash** |
| 依赖 | Python **3.11+**、Node.js **18+**；无需第三方 Python/npm 包 |

兼容模式检查官方签名、资源完整性、唯一请求结构和提取方法的运行行为，允许资源哈希、文件名及压缩变量名变化。请求结构发生未知变化时停止并报告，不靠版本号直接放行。v0.1.0 的固定版本构建入口仍保留，用于原基线；新安装与更新建议使用 `--compatible`。

2026-10-18 核对时，`deepseek-flash` 对应 **DeepSeek-V4.1-Flash-1017**。后端版本由 DeepSeek 维护，见[官方模型说明](https://api-docs.deepseek.com/quick_start/agent_integrations/)。

## macOS 安装

先安装官方应用并完成正常登录。构建目录保存应用备份和模型目录，请选择可长期保留的位置。

```sh
git clone --branch codex/existing-proxy-models https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 --version
node --version

# 交互输入 API key，保存到 macOS 钥匙串。
python3 patch.py setup-key

PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/build-20261018"
python3 patch.py build --compatible --output "$PATCH_BUILD"
python3 scripts/test_offline.py "$PATCH_BUILD"
python3 patch.py test-boot "$PATCH_BUILD"
```

`build` 创建并验证副本。离线测试使用临时配置与本机占位服务，检查模型目录和临时线程的服务商，不提交推理回合。启动测试会短暂打开隔离窗口，检查界面和后台启动，再关闭自身测试进程。

然后**正常退出 Codex/ChatGPT 应用**，在 Terminal 执行：

```sh
python3 patch.py install "$PATCH_BUILD"
```

默认查找 `/Applications/ChatGPT.app`，不存在时尝试 `/Applications/Codex.app`。其他位置可在 `build` 中指定 `--app "/path/to/ChatGPT.app"`；配置目录可用 `--codex-home "$HOME/.codex"` 指定。

## Windows 安装

安装官方 Codex，准备 Python 3.11+ 和 Node.js 18+，在 PowerShell 中进入仓库目录：

```powershell
python --version
node --version
python patch.py setup-key

$PatchBuild = Join-Path $env:LOCALAPPDATA "CodexDeepSeekPatch\build-20261018"
python patch.py build --compatible --output "$PatchBuild"
python scripts/test_offline.py "$PatchBuild"
python patch.py test-boot "$PatchBuild"

# 正常退出 Codex 后安装。
python patch.py install "$PatchBuild"
```

构建时自动查找官方 Store 应用，也可用 `--app "C:\path\to\app"` 明确指定来源。API key 通过交互输入保存到 **Windows 凭据管理器**。

安装目标为 `%LOCALAPPDATA%\CodexDeepSeekPatch\app`，开始菜单新增 **Codex DeepSeek** 快捷方式，启动该目录中的 `ChatGPT.exe`。以后通过此快捷方式使用补丁版本；Store 中的官方应用保留在原位置。构建目录与安装目标需要处于同一磁盘卷，并为完整应用副本及恢复数据预留空间。

## 使用与配置

重新打开补丁应用，**新建本机聊天 → 打开模型菜单 → 选择 DeepSeek 4.1 Flash**。已有聊天保留原提供商；GPT 与 DS 之间切换请新建聊天。远程主机沿用原有路由。

每次构建使用新目录。初次安装发现已有自定义模型目录、不同默认服务商或同名 DS 服务商时，会提示核对配置。更新已有补丁可在 `build --compatible` 中增加 `--baseline "旧构建目录"`，刷新已登记的模型目录并保留其他用户设置。已登记的目录或 DS 服务商被手动修改时，工具停止供核对。

手动更新示例：来源选正式安装位置的官方更新版。若要重建同一版本，先恢复原版，再按首次安装步骤构建并省略 `--baseline`，因为恢复也会还原配置。macOS 的 `--app` 同时是安装目标。已打补丁的副本不能作为兼容构建来源。

```sh
NEW_PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/build-next"
python3 patch.py build --compatible \
  --app "/path/to/official/ChatGPT.app" \
  --baseline "$PATCH_BUILD" --output "$NEW_PATCH_BUILD"
python3 scripts/test_offline.py "$NEW_PATCH_BUILD"
python3 patch.py test-boot "$NEW_PATCH_BUILD"
# 正常退出应用后：
python3 patch.py install "$NEW_PATCH_BUILD"
```

凭据默认使用当前用户名和 `Codex DeepSeek API Key`；可用 `--keychain-account`、`--keychain-service` 自定义，`setup-key` 与 `build` 使用相同参数。

## 可选：接入现成的 Gemini / Grok / Opus 代理

先在 [EasyCLIProxyAPI](https://github.com/router-for-me/EasyCLIProxyAPI) 中配置并启动接口。补丁复用其 Responses 接口，由现有代理管理订阅登录和凭据刷新。

在兼容构建命令中增加以下参数（配置路径换成本机路径；Windows 使用 `python`，各参数放在同一行）：

```sh
--proxy-config "/path/to/existing/config.yaml" \
--proxy-base-url "http://127.0.0.1:8317/v1" \
--proxy-models grok-4.5 grok-4.7 gemini-3.8-flash-high claude-opus-4-6-thinking
```

默认仍只安装 DeepSeek，可省略不需要的代理模型。所选 ID 必须由自己的接口提供。凭据 helper 只读取现有配置的客户端 `access.api-keys`（兼容旧版根级 `api-keys`），不会复制密钥或导入订阅 OAuth 文件。保留配置文件位置，并在使用这些模型时运行 EasyCLIProxyAPI。远程接口要求 HTTPS，本机回环接口支持 HTTP。

Mac 已安装旧补丁时，可使用保留的官方签名原版备份，增加 `--baseline "旧构建目录" --install-target "/Applications/ChatGPT.app" --app "/path/to/original/ChatGPT.app"`。安装前核对当前应用匹配 baseline；恢复回到安装前的应用和配置。

执行离线和启动测试后，对每个选中模型运行实际回复与读文件工具测试，再正常退出应用并安装：

```sh
python3 scripts/test_connection.py "$NEW_PATCH_BUILD" --model grok-4.7 --run
```

联网测试使用对应账户额度。四个模型采用文本与工具调用配置及提供商推理预设。**默认上下文按各模型官网最大值配置**（2026-10-18 核对）：

| 模型 | 默认 / 最大窗口（token） | 官方规格 |
| --- | ---: | --- |
| Grok 4.5 | 500,000 | [xAI](https://docs.x.ai/developers/models/grok-4.5) |
| Grok 4.7 | 500,000 | [xAI](https://docs.x.ai/developers/grok-4-7) |
| Gemini 3.8 Flash | 1,048,576 | [Google](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) |
| Opus 4.6 Thinking | 1,000,000 | [Anthropic](https://platform.claude.com/docs/en/models/opus-4-6/overview) |

目录窗口按 100% 计入，不再继承 GPT 模板的百分比折减。新建代理聊天时填入该模型的窗口和 90% 自动压缩阈值，避免旧的全局小窗口 / 低阈值覆盖默认值；请求 `config.model_context_window` 和 `config.model_auto_compact_token_limit` 中显式设定的较小值会保留。GPT / DS 的既有全局设置不变。

Opus 官网规格为 1M，但 CLIProxyAPI 8.0.32 的 Antigravity 静态表仍标注 200k；本分支按模型官网设置客户端窗口，上游订阅通道的长请求能力需独立核验。[代理版本模型表](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.32/internal/registry/models/models.json)

代理版安装前还需执行 `python3 scripts/test_context.py "$NEW_PATCH_BUILD"`：它使用真实内置 CLI 和本机模拟服务，连续两轮模拟 180k 占用，检查实际窗口与重复压缩，**不调用订阅接口**。安装后重新启用监测并指定新 baseline；后续自动补装继承这组模型与接口并执行此回归检查。更新后新建本机聊天，以采用修正后的上下文默认值。

## 可选：官方更新后自动重新安装

先完成一次安装，再启用监测。macOS 示例：

```sh
python3 monitor.py enable --app "/Applications/ChatGPT.app" --baseline "$PATCH_BUILD"
python3 monitor.py status
```

Windows PowerShell 示例，`--app` 指向官方来源，`--baseline` 指向已安装的构建：

```powershell
$CodexApp = Join-Path (Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1 -ExpandProperty InstallLocation) "app"
python monitor.py enable --app "$CodexApp" --baseline "$PatchBuild"
python monitor.py status
```

也可将此前本机旧安装器留下的 macOS 构建目录作为 `--baseline`。监测会先核对并接续已安装补丁。

macOS 使用 launchd，Windows 使用当前用户的任务计划程序；默认登录时及每 **5 分钟**检查一次。检测到稳定的新版本后，依次完成签名与兼容检查、构建、离线线程测试和隔离启动测试。期间可能短暂出现测试窗口。全部通过后等待你正常退出应用，再安装并通知结果；运行中的聊天由你自行结束。

未知结构或执行失败会保留记录，并按同一问题去重通知。正常、未变化的检查保持安静。服务保留构建与恢复数据；不会自动下载新补丁代码、联网刷新模型目录或调用付费模型 API。

```sh
# 查看问题详情；明确重试当前失败版本。
python3 monitor.py status
python3 monitor.py run-once --retry

# 停止自动检查，保留补丁、构建和日志。
python3 monitor.py disable
```

Windows 使用相同命令，将 `python3` 换为 `python`。仓库更新后重新执行 `enable`，才会更新监测服务保存的代码副本。

## 检查与恢复

```sh
python3 patch.py status "$PATCH_BUILD"

# 先正常退出应用；如启用了自动监测，先 disable。
python3 patch.py restore "$PATCH_BUILD"
```

macOS 恢复安装前的应用和配置。Windows 恢复此前的受管副本、快捷方式和配置，首次安装则移除本次受管副本。安装后若配置或目标被修改，恢复操作会停止供核对。`config-before.toml` 保存安装前配置。

**保留构建目录**：配置仍引用其中的模型目录，目录中也保存恢复所需文件。

## 测试

离线单元测试不使用 API：

```sh
python3 -m unittest discover -s tests -v
node tests/route.test.cjs
```

兼容构建生成 `compatibility.json`，保存源摘要、精确修改位置、提取方法和行为检查结果，供本机复查。

可选联网测试会向 DeepSeek 发送两次小型测试对话，验证回复和读取临时文件的工具调用，**产生 API 用量**；只有显式传入 `--run` 才执行：

```sh
python3 scripts/test_connection.py "$PATCH_BUILD" --run
```

具体平台、版本与实测层级见[验证记录](docs/VALIDATION.md)。

## 实现与注意事项

- 新建本机 DS 聊天时将 `thread/start` 提供商设为 `deepseek-desktop`，保留其他请求字段；本机聊天列表显示各服务商的聊天。
- 跳过本机新建聊天的预热缓存复用，避免提供商混用；新建聊天可能略慢。
- GPT 模型来自构建时内置 CLI 的离线模型目录，再添加 Flash 和显式选择的代理模型。模型菜单随重新构建刷新。
- 保持 ASAR 资源校验启用，更新成员与分块哈希、包头及原生配套摘要。macOS 副本使用 ad-hoc 签名；Windows 副本修改完整性载体。依赖官方签名的系统功能或更新可能受到影响。
- `.local/`、构建产物和配置备份包含程序副本或本机信息。请勿上传到仓库或 Issue。

## 许可证

项目新增补丁代码采用 [MIT](LICENSE) 许可证。ChatGPT、Codex、DeepSeek、Electron 及其程序资源归各自权利人所有，参见 [NOTICE](NOTICE)。
