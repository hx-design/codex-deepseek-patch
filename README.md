# Codex DeepSeek Patch

[![Tests](https://github.com/limyum-creator/codex-deepseek-patch/actions/workflows/tests.yml/badge.svg)](https://github.com/limyum-creator/codex-deepseek-patch/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

在 macOS Codex 桌面版的模型菜单中添加 **DeepSeek 4.1 Flash**，保留 GPT 模型和默认设置。选择 DS 后，新建的本机聊天使用 DeepSeek API。

这是非官方、本地安装的补丁。仓库只提供补丁源码，不提供 ChatGPT/Codex 应用、提取后的程序、模型系统提示词或 API 密钥。

[English](README.en.md) · [验证记录](docs/VALIDATION.md) · [实现说明](docs/ARCHITECTURE.md)

## 支持范围

| 项目 | 当前版本 |
| --- | --- |
| 系统 | macOS，Apple Silicon，应用与构建目录位于同一 APFS 卷 |
| 应用 | `26.1013.40934`，build `20770` |
| 已验证内置 CLI | `0.163.0-alpha.10.2` |
| 新增模型 | `deepseek-flash`，显示为 **DeepSeek 4.1 Flash** |
| 运行依赖 | Python **3.11+**、Node.js **18+**，无需安装 Python/npm 第三方包 |

2026-10-18 核对时，`deepseek-flash` 对应 **DeepSeek-V4.1-Flash-1017**。后端版本由 DeepSeek 维护，见[官方模型说明](https://api-docs.deepseek.com/quick_start/agent_integrations/)。

版本、原始包哈希或修改锚点不匹配时，工具会停止。其他版本需先适配。

## 安装

先安装官方应用并完成正常登录。安装过程会保留完整原版备份；确认磁盘有空间，并保持构建目录可长期访问。

```sh
git clone https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 --version
node --version

# API key 由 macOS security 工具交互读取并保存到钥匙串。
# 已存在同名条目时可跳过；不会将 key 写入配置或仓库。
python3 patch.py setup-key

PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/26.1013.40934"
python3 patch.py build --output "$PATCH_BUILD"
python3 patch.py test-boot "$PATCH_BUILD"
```

`build` 创建副本并验证，正式应用仍保持原样。`test-boot` 会打开一个使用临时数据的测试窗口，随后关闭测试进程。

然后**正常退出 Codex/ChatGPT 应用**，在 Terminal 执行：

```sh
python3 patch.py install "$PATCH_BUILD"
```

重新打开应用，**新建本机聊天 → 打开模型菜单 → 选择 DeepSeek 4.1 Flash**。已有聊天保留原来的提供商；GPT 与 DS 之间切换请新建聊天。此版本不增加远程主机的 DS 路由。

默认识别 `/Applications/ChatGPT.app`，不存在时尝试 `/Applications/Codex.app`。自定义位置：

```sh
python3 patch.py build \
  --app "/path/to/ChatGPT.app" \
  --codex-home "$HOME/.codex" \
  --output "$PATCH_BUILD"
```

构建目录必须是新目录。已有自定义模型目录、不同默认服务商或同名 DS 服务商时，工具会提示先核对配置。API key 条目默认使用当前 macOS 用户名和 `Codex DeepSeek API Key`；可用 `--keychain-account`、`--keychain-service` 自定义，`setup-key` 与 `build` 使用相同参数即可。

## 检查与恢复

```sh
python3 patch.py status "$PATCH_BUILD"

# 先正常退出应用。
python3 patch.py restore "$PATCH_BUILD"
```

恢复会切回原版应用和安装前配置。安装后若配置被修改，恢复操作会停止，保留现场供核对，避免覆盖新设置。构建目录中的 `config-before.toml` 是安装前备份。

**保留构建目录**：其中的模型目录仍由已安装配置引用，原版应用也保存在该目录中。

## 测试

离线测试可在 Linux/macOS 执行，不使用 API：

```sh
python3 -m unittest discover -s tests -v
node tests/route.test.cjs
```

构建后还可测试提取自实际应用的请求方法：

```sh
node tests/route.test.cjs "$PATCH_BUILD/patched-client.mjs"
```

可选的联网测试会向 DeepSeek 发送两次小型测试对话，验证回复和读取临时文件的工具调用，**会产生 API 用量**。只在显式传入 `--run` 时执行：

```sh
python3 scripts/test_connection.py "$PATCH_BUILD" --run
```

## 实现与注意事项

- 新建本机 DS 聊天时，将 `thread/start` 的提供商设为 `deepseek-desktop`；保留其他请求字段。
- 跳过本机新建聊天的预热缓存复用，防止误用另一个提供商的会话。新建聊天可能略慢。
- 从用户自己的模型目录读取 GPT 项，再添加一个 Flash 项。生成的目录是构建时快照；模型列表有更新时需要重新构建。
- 同步更新 ASAR 包头、修改文件的分块哈希、`Info.plist` 及 Framework 的配套摘要，保持资源完整性验证。
- 应用会改用本地 ad-hoc 签名，可能影响依赖官方签名的自动更新、访问组、推送或系统权限。完整官方备份可恢复原签名。
- 应用官方更新后需要重新检查兼容性。工具不后台自动修改应用，也不强制结束正在运行的聊天。
- `.local/` 和其他构建产物包含程序副本或本机配置，已加入忽略规则。请勿将自己生成的构建目录上传到仓库或 Issue。

## 许可证

项目新增补丁代码采用 [MIT](LICENSE) 许可证。ChatGPT、Codex、DeepSeek、Electron 及其程序资源归各自权利人所有，参见 [NOTICE](NOTICE)。
