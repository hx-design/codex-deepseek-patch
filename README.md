# Codex DeepSeek Patch

[![Tests](https://github.com/limyum-creator/codex-deepseek-patch/actions/workflows/tests.yml/badge.svg)](https://github.com/limyum-creator/codex-deepseek-patch/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

在 macOS / Windows Codex 桌面版的模型菜单中添加 **DeepSeek 4.1 Flash**，保留 GPT 模型和默认设置。本机已有 GPT 聊天也能直接切换到 DS，并在同一聊天切回 GPT。

**v0.3.4** 新增已有聊天内 **GPT ↔ DS** 切换，保留聊天记录、工作目录和权限；切换与压缩后沿用对应模型的上下文设置。同时提供补丁自动更新、模型列表与规格同步、Codex 更新后兼容检查和自动补装。DS 与代理扩展版分别更新，默认按模型最大上下文同步，并保留用户主动调小的设置。

本仓库有两种版本：**当前 main 为独立 DS 版**；[扩展版](https://github.com/limyum-creator/codex-deepseek-patch/tree/codex/existing-proxy-models) 支持现有 Gemini / Grok / Opus 接口与本地模型，DeepSeek 可选，并支持同一聊天切换模型。两个版本各自更新。

这是非官方、本地安装的补丁。仓库只提供补丁源码，不提供 ChatGPT/Codex 应用、提取后的程序、模型系统提示词或 API 密钥。

[English](README.en.md) · [验证记录](docs/VALIDATION.md) · [实现说明](docs/ARCHITECTURE.md)

## 支持范围

| 项目 | 范围 |
| --- | --- |
| macOS | Apple Silicon；应用与构建目录位于同一 APFS 卷 |
| Windows | 官方 Codex 应用；Store 安装作为只读来源，补丁安装到独立的用户目录 |
| 已检查的基线应用 | `26.1013.40934`，build `20770`；具体平台验证见验证记录 |
| 历史已验证 CLI | `0.163.0-alpha.10.2` |
| 新增模型 | `deepseek-flash`，显示为 **DeepSeek 4.1 Flash** |
| 依赖 | Python **3.11+**、Node.js **18+**；无需第三方 Python/npm 包 |

兼容模式检查官方签名、资源完整性、唯一请求结构和提取方法的运行行为，允许资源哈希、文件名及压缩变量名变化。请求结构发生未知变化时停止并报告，不靠版本号直接放行。所有构建统一使用兼容检查路径；旧命令中的 `--compatible` 参数仍可使用。

2026-10-18 核对时，`deepseek-flash` 对应 **DeepSeek-V4.1-Flash-1017**。后端版本由 DeepSeek 维护，见[官方模型说明](https://api-docs.deepseek.com/quick_start/agent_integrations/)。

## macOS 安装

先安装官方应用并完成正常登录。构建目录保存应用备份和模型目录，请选择可长期保留的位置。

```sh
git clone https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 --version
node --version

# 交互输入 API key，保存到 macOS 钥匙串。
python3 patch.py setup-key

PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/build-20261018"
python3 patch.py build --output "$PATCH_BUILD" --verify
```

`build --verify` 创建副本，并运行目录、思考档位、上下文、同一聊天切换与隔离启动检查。测试通过实际内置 CLI 向本机模拟服务发送合成请求，不使用个人凭据或调用付费模型。启动测试会短暂打开隔离窗口，检查界面和后台启动，再关闭自身测试进程。

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
python patch.py build --output "$PatchBuild" --verify

# 正常退出 Codex 后安装。
python patch.py install "$PatchBuild"
```

构建时自动查找官方 Store 应用，也可用 `--app "C:\path\to\app"` 明确指定来源。API key 通过交互输入保存到 **Windows 凭据管理器**。

安装目标为 `%LOCALAPPDATA%\CodexDeepSeekPatch\app`，开始菜单新增 **Codex DeepSeek** 快捷方式，启动该目录中的 `ChatGPT.exe`。以后通过此快捷方式使用补丁版本；Store 中的官方应用保留在原位置。构建目录与安装目标需要处于同一磁盘卷，并为完整应用副本及恢复数据预留空间。

## 使用与配置

重新打开补丁应用，在**已有或新建的本机聊天 → 打开模型菜单 → 选择 DeepSeek 4.1 Flash**，下一条消息就使用 DS；选择 GPT 则切回 GPT。聊天 ID、历史、工作目录和权限保持原样。先等待当前回复完成再切换；远程主机和自行配置的其他提供商沿用原有路由。

切换时重新载入同一聊天，并应用当前模型的窗口和压缩阈值，修正旧聊天遗留的 128K 默认值。用户在模型偏好中主动调小的窗口继续保留；切回 GPT 时恢复 GPT 的配置及网页搜索设置。

每次构建使用新目录。初次安装发现已有自定义模型目录、不同默认服务商或同名 DS 服务商时，会提示核对配置。更新已有补丁可在 `build --compatible` 中增加 `--baseline "旧构建目录"`，刷新已登记的模型目录并保留其他用户设置。已登记的目录或 DS 服务商被手动修改时，工具停止供核对。

手动更新示例：来源选正式安装位置的官方更新版。若要重建同一版本，先恢复原版，再按首次安装步骤构建并省略 `--baseline`，因为恢复也会还原配置。macOS 的 `--app` 同时是安装目标。已打补丁的副本不能作为兼容构建来源。

```sh
NEW_PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/build-next"
python3 patch.py build --compatible \
  --app "/path/to/official/ChatGPT.app" \
  --baseline "$PATCH_BUILD" --output "$NEW_PATCH_BUILD"
python3 patch.py verify "$NEW_PATCH_BUILD"
# 正常退出应用后：
python3 patch.py install "$NEW_PATCH_BUILD"
```

凭据默认使用当前用户名和 `Codex DeepSeek API Key`；可用 `--keychain-account`、`--keychain-service` 自定义，`setup-key` 与 `build` 使用相同参数。

## 思考程度选择

新增模型在模型菜单中显示各自支持的思考档位；同一聊天可以逐回合调整。自动同步和重新安装会保留完整档位列表。

DeepSeek Flash 提供 **low / high / max**。本版继续使用 DeepSeek 直连接口；公司 Windows 电脑可以保持独立 DS 更新通道。

安装前的 `test_reasoning.py` 会通过实际内置 CLI 读取菜单模型数据，逐档发送本机模拟请求，并切回第一档检查选择是否生效；自动更新也执行此项检查。

## 自动更新与模型同步

完成一次安装后运行 `monitor.py enable`，即可同时启用 Codex 更新后自动补装、补丁自身更新和模型同步。macOS 使用 launchd，Windows 使用当前用户的任务计划程序。

| 通道 | 跟随分支 | 模型来源 |
| --- | --- | --- |
| 独立 DS 版 | `main` | 原生 GPT + DeepSeek 官方接口 |
| 多模型扩展版 | `codex/existing-proxy-models` | 原生 GPT + 可选 DS / 现有代理 / 本地模型 |

两个通道分别发布 `update.json`，锁定版本、提交和文件摘要。安装时登记的通道保持固定，拒绝跨通道更新、倒退版本和摘要不符的文件。扩展版可按需接入本地或已有兼容接口；使用订阅代理时才需要 EasyCLIProxyAPI。DS 版直接连接 DeepSeek。

```sh
python3 monitor.py enable --app "/Applications/ChatGPT.app" --baseline "$PATCH_BUILD"
python3 monitor.py status
# 立即检查代码、目录和规格；仍等待应用正常退出才安装。
python3 monitor.py run-once --sync-now
```

Windows 将 `python3` 换为 `python`，`--app` 指向官方 Store 来源，`--baseline` 指向已安装构建。已有旧监测服务的用户，安装本次版本后执行一次 `enable` 完成迁移，以后补丁代码自动更新。

默认每 **5 分钟**检查应用变化，每 **1 小时**检查补丁发布和模型规格。发现变化后下载并核对源码，先运行隔离单元测试，再检查官方应用兼容性、构建副本、验证目录、思考档位、上下文与同一聊天切换，再测试隔离启动。全部通过后等待应用正常退出，完成安装与监测程序更新。测试窗口可能短暂出现。未知结构、断网、无规格的新型号或失败会去重通知，保留当前可用版本和恢复数据。相同状态保持安静；检查不向供应商提交推理回合。

GPT 通过内置 CLI 刷新原生目录；DS 优先读取官方 `/models` 的真实规格；扩展版读取现有代理 `/models` 发现型号，再核对 xAI、Google、Anthropic 官方规格。自动加入现有 **Grok、Gemini Flash、Opus** 系列的新型号，DS 同步 Flash 系列。首次同步建立现有型号基线，保留此前未选型号；后续新出现的型号自动加入。暂时下线的型号在连续两次成功查询都缺失后移除，重新出现后恢复。新型号缺少可信规格时保留待处理记录并提醒，不套用小窗口。

默认上下文使用模型支持的**最大值**，不继承模板的折减百分比。保留用户主动调小的单模型设置与已有 GPT / DS 全局设置；代理模型使用各自窗口。自动压缩默认在配置窗口的 90% 触发，用户更低阈值继续保留。最大窗口变大时默认同步增大；用户已设置的较小窗口保持原值。

```sh
# 单模型调小；后续自动同步会保留。
python3 monitor.py model-options --model deepseek-flash --context-window 262144
# 恢复跟随最大窗口。
python3 monitor.py model-options --model deepseek-flash --reset-context
# 隐藏或重新显示；扩展版同样适用于 Grok / Gemini / Opus。
python3 monitor.py model-options --model deepseek-flash --hide
python3 monitor.py model-options --model deepseek-flash --show
# 重试失败版本；停止服务会保留应用、配置与恢复数据。
python3 monitor.py run-once --retry
python3 monitor.py disable
```

`model-options` 在下一次同步后应用，修改模型路由需正常退出并重新打开应用，已有聊天在下一次发送 DS 消息时应用新规格，新建聊天同样生效。偏好保存在监测目录的 `model-preferences.json`，状态与告警可通过 `status` 查看。可用 `enable --no-code-updates` 或 `--no-model-sync` 分别关闭对应功能。构建与更新缓存需要磁盘空间，当前及上一份可恢复构建请保留。

## 检查与恢复

```sh
python3 patch.py status "$PATCH_BUILD"

# 先正常退出应用；如启用了自动监测，先 disable。
python3 patch.py restore "$PATCH_BUILD"
```

macOS 恢复原版应用和安装前配置。Windows 恢复此前的受管副本、快捷方式和配置，首次安装则移除本次受管副本。安装后若配置或目标被修改，恢复操作会停止供核对。`config-before.toml` 保存安装前配置。

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

- 新建本机 DS 聊天时设置 `deepseek-desktop`；已有 GPT / DS 聊天在下一回合前按选中模型重新载入同一 ID。切换失败时尝试恢复原模型并停止发送；本机聊天列表显示各服务商的聊天。
- 跳过本机新建聊天的预热缓存复用，避免提供商混用；新建聊天可能略慢。
- 首次构建使用内置目录；启用监测后刷新 GPT、DS 模型及规格，验证后重新构建。
- 保持 ASAR 资源校验启用，更新成员与分块哈希、包头及原生配套摘要。macOS 副本使用 ad-hoc 签名；Windows 副本修改完整性载体。依赖官方签名的系统功能或更新可能受到影响。
- `.local/`、构建产物和配置备份包含程序副本或本机信息。请勿上传到仓库或 Issue。

## 许可证

项目新增补丁代码采用 [MIT](LICENSE) 许可证。ChatGPT、Codex、DeepSeek、Electron 及其程序资源归各自权利人所有，参见 [NOTICE](NOTICE)。
