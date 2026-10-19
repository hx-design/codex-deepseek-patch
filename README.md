# Codex DeepSeek 适配器

把 DeepSeek Flash 加入 **官方 Codex 的模型菜单**，在同一个聊天中切换，继续使用原登录、聊天记录和原生工具。

DS 版 **0.4.1** · 更新分支 `main`

[English](README.en.md) · [验证记录](docs/VALIDATION.md) · [实现说明](docs/ARCHITECTURE.md) · [MIT](LICENSE)

## 两个版本

| 版本 | 接入范围 | 更新通道 |
| --- | --- | --- |
| [DS 版](https://github.com/limyum-creator/codex-deepseek-patch/tree/main) | 原生 GPT + DeepSeek Flash | `main` |
| [扩展版](https://github.com/limyum-creator/codex-deepseek-patch/tree/codex/existing-proxy-models) | 原生 GPT + 已有云端接口 / 本地服务；DeepSeek 可选 | `codex/existing-proxy-models` |

两个版本分别发布、分别更新。扩展版不要求配置 DeepSeek，也不管理外部服务的订阅登录。

## 这一版怎么工作

- **官方应用保持原样**：不改 ASAR、不复制 Windows 应用、不重新签名 Mac 应用。Windows 使用原 Store 包身份，应用自身的更新由官方处理。
- **同聊天切换**：模型菜单使用原生目录。聊天 ID、工作目录、权限和历史保留；等当前回复结束再切换。
- **原登录和工具**：GPT 沿用官方认证；其他模型只使用各自服务的凭据。原生工具继续由 Codex 发现、授权和执行。
- **上下文按模型最大值**：规格与选择共用一份目录；用户主动调小的窗口、隐藏型号继续保留。本地服务加载窗口可单独保留。
- **原生压缩**：使用 Codex 的可读摘要压缩，换模型后仍使用该模型的窗口。跨模型隔离专有思考签名，保留可读对话与工具结果。
- **自动维护**：官方应用更新后验证兼容性；适配层代码、模型列表和规格分别检查。无变化时安静运行，失败保留当前可用版本。

扩展版按 **Responses / Chat Completions 协议和显式能力**工作，不按厂商名称决定路由。Claude、GLM、Qwen 等可通过提供这两类兼容接口的现有服务接入；原生 Anthropic Messages API 尚未实现。服务没有提供的规格可以手动声明，不给未知型号套用任意小窗口。

## 轻量化设计

运行时是一个仅监听本机回环地址的 Python 标准库进程，负责请求转发与必要的格式转换。没有额外聊天界面、数据库、模型运行引擎或订阅登录系统。模型推理由原有服务完成，工具由 Codex 执行。

适配器由系统按用户启动；更新监测由 launchd / Windows 任务计划程序定时执行，检查后退出。Windows 使用无控制台 Python。发布包仅含源码和文档；旧应用修改逻辑保留用于历史版本的恢复和回归，新安装路径不执行它。

需要 **Python 3.11+** 和已安装的官方 macOS / Windows Codex。新架构安装与自动验证无需额外 Node.js；原生工具使用 Codex 自带运行时。外部模型服务需自行准备。

## 安装 DS 版

需要已有 DeepSeek API key，直接连接 DeepSeek API，无需额外代理软件。保留原来的 GPT 登录；只添加 DeepSeek Flash。

```sh
git clone https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 patch.py setup-key
python3 patch.py build --app /Applications/ChatGPT.app --output /absolute/path/build --verify
# 正常退出 Codex 后：
python3 patch.py install /absolute/path/build
python3 monitor.py enable --app /Applications/ChatGPT.app --baseline /absolute/path/build
```

Windows 使用 `python` 和 Windows 路径；`--app` 可省略，自动发现官方安装。

验证使用原生 CLI、隔离状态及本机模拟接口，在后台检查 GPT ↔ DS、工具、思考档位、上下文和压缩，不会打开测试桌面窗口或调用付费模型。验证通过后才允许安装。

从旧补丁迁移时加入 `--baseline /path/to/old-build`。Mac 的 `--app` 指向旧构建保留的、通过官方签名验证的 `original.app`，`--install-target` 指向原安装位置；首次迁移恢复官方应用，后续更新只替换适配代码和受管配置。Windows 沿用已注册的官方 Store 应用。

已有聊天通过原生元数据接口保留 ID、模型、思考档位、归档和历史。旧 GPT 聊天若含专有压缩块，首次切至 DS 时通过原 GPT 生成一次可读任务摘要并缓存，会使用一次 GPT 请求。

## 非阻塞更新进度

发现新版本后显示系统原生进度窗口，可继续工作或关闭窗口。点击“开始更新”后，准备和兼容性校验通过才正常关闭 Codex；安装时提示暂勿启动。关窗不会中断后台更新。完成后显示“启动 Codex”按钮，由用户决定何时打开。

有实际文件数量的阶段显示计数，无法预估时使用活动进度条。相同版本不会每次检查都重复弹窗；关闭过程窗口后，完成时会再提示一次。旧版本首次升级在下载到新版组件后开始显示进度；后续从发现版本开始显示。

## 更新与偏好

```sh
python3 monitor.py status
python3 monitor.py run-once --sync-now
python3 monitor.py model-options --model MODEL_ID --context-window 32768
python3 monitor.py model-options --model MODEL_ID --hide
python3 monitor.py model-options --model MODEL_ID --reset-context
```

监测默认启用代码更新与模型同步，可在启用时分别用 `--no-code-updates` / `--no-model-sync` 关闭。正在使用 Codex 时等待退出后安装。更新包检查版本通道、不可变提交、文件摘要及隔离测试，DS 与扩展不会交叉覆盖。

```sh
python3 patch.py status /absolute/path/build
python3 monitor.py disable
python3 patch.py recover /absolute/path/build  # 恢复中断的安装
python3 patch.py restore /absolute/path/build  # 撤销本适配层，恢复先前配置
```

恢复命令保留官方应用；旧补丁迁移前已有的外部提供方配置仍按原记录恢复。Mac 不重置钥匙串或系统隐私权限；首次从旧本地签名切回官方身份时，系统可能要求确认相应权限。日常更新不再重新签名。

非官方项目。请勿提交官方程序、生成的模型目录、个人日志、账号配置或凭据。问题反馈请附系统、Codex 版本、适配层版本、接口类型和脱敏错误。真实模型实测与模拟协议覆盖分别记录在[验证文档](docs/VALIDATION.md)。
