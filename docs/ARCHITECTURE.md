# 实现说明

所有运行时代码使用 Python / Node.js 标准库。`patch.py` 提供构建、验证、连接与恢复入口；`backend.py` 管理平台安装事务；`compatibility.py` 校验原生客户端请求结构；`model_sync.py` 生成一致的目录和路由；`monitor.py` / `updater.py` 管理独立通道的周期更新。

## 原生聊天路由

`route.js` 仅处理 `local` 主机。新聊天为已登记模型选择提供商与对应窗口；聊天列表包含所有提供商，预热缓存不跨提供商复用。未登记提供商及远程主机保留原流程。

同聊天切换由实际 `sendRequest` 中的异步钩子处理。发送第一回合或改选模型时，读取聊天，确认当前回合结束，读取当前权限与工作目录，再通过原生 `thread/unsubscribe` → `thread/resume` 重载同一个聊天 ID，最后发送原回合。仅对已登记服务商与原生 GPT 生效。恢复后核对模型、提供商、权限和目录；失败则尝试恢复原提供商，不发送新消息。无需改写数据库或复制聊天历史。

旧聊天的 128K 配置会在重载时被当前模型规格与持久偏好替换。返回 GPT 时恢复原生目录和用户的全局窗口设置。权限、审批、工作目录和运行时工作区沿用当前聊天；进行中的回复不会被卸载。

## 提供商与轻量化

- `proxy_support.py` 读取现有代理客户端 key，订阅 OAuth 和刷新仍归已有代理负责。
- `local_support.py` 从 Ollama、LM Studio 或通用 Responses 服务发现模型及规格，生成原生服务商配置。匿名本地接口无需凭据 helper；认证接口读取用户指定的密钥文件。
- 完整支持原生工具协议的本地模型可以直接连接原服务；通用服务可在 `model_specs` 中用 `native_tool_search: true` 声明该能力。其余支持工具的模型启用按需发现，并使用兼容进程转换原生工具搜索与自定义调用。只聊天模型去掉工具声明；Qwen3.5 等严格模板合并前置系统/开发者说明。
- `responses_compat.py` 将搜索请求转换为普通函数调用，将返回恢复为 Codex 原生搜索事件；仅将搜索结果中的工具加入后续请求。自定义工具输入作为字符串传递，完整恢复调用 ID 和原始输入。工具查找、执行和权限检查仍由 Codex 负责。压缩关闭工具时不会从旧搜索历史重新启用工具。
- `local_bridge.py` 仅由认证 helper 按需拉起，绑定回环地址并验证随机令牌，空闲 120 秒退出。复用现有代理的客户端凭据；内部令牌不转发到上游，禁止凭据跟随重定向。没有新数据库、账号管理界面或第三方包。

- DeepSeek 官方直连可选；扩展版新安装默认不注册 DS，也不要求其密钥。旧安装继承选择。

不改变模型权重、模板和本地服务启动参数。模型能力与加载窗口分别记录；`respect_server_context` 会将发现的较小加载窗口保存为持久偏好。用户没有启用此项时，客户端目录按最大规格配置，服务端加载参数仍由原服务管理。

## 模型规格、思考与同步

目录的默认上下文和最大窗口均从可信规格获得，取消继承模板的百分比折减。偏好可降低当前窗口和压缩阈值；默认压缩阈值为窗口的 90%。没有规格的新模型会报告待处理，不猜测小上限。Ollama 读取 `/api/show` 能力与最大值，必要时读取 `/api/ps` 的加载窗口；LM Studio 使用模型信息接口；通用服务可由用户补充真实规格。

`configure_reasoning` 为型号生成支持的档位和有效默认值。`reasoning.effort` 控制逐回合选择；本地接口关闭原生专用的 `configuration_update` 消息能力。非思考模型不生成虚假档位。

`synced-models.json` 同时包含目录、路由、连接和 DS 启用状态，摘要覆盖影响运行的全部字段。元数据失败时保留已知可用项；用户隐藏与调小设置独立持久化。接口地址变更同样触发重建。

## 兼容性、安装与恢复

从官方签名 ASAR 中找到唯一受支持的客户端，词法扫描排除字符串、注释等伪锚点。检查请求分派、配置读取、队列与预热契约，在 Node VM 中执行提取的方法，再检查完整修改文件语法。资源文件名和压缩变量名可以变化；未知结构直接停止。证据绑定官方 ASAR 摘要。

只修改登记的客户端成员，保留其余归档内容与完整性校验。macOS 更新 ASAR、Info.plist 与 Framework 配套摘要并为副本使用固定本机证书重签；Windows 从只读 Store 来源复制到独立用户目录。安装前核对构建摘要、当前目标和配置，要求目标退出。macOS 原子交换应用，Windows 事务替换副本与快捷方式；失败恢复前态。

`build --verify`、`verify BUILD` 和自动更新共用一个验证入口：

1. 实际内置 CLI 的目录与临时线程路由。
2. 每个思考档位及回切的实际请求参数。
3. 连续模拟占用下的窗口与重复压缩。
4. 同聊天跨提供商切换、旧窗口修复、实际压缩后的窗口、历史和权限保留。
5. 挂载 160 个大定义 MCP 工具，检查初始请求体积、搜索后实际调用、连续调用、强制自动压缩后继续调用，以及窗口不变。
6. 隔离应用 renderer / app-server 启动。

这些检查只使用临时配置和本机模拟接口。真实短回复与文件工具测试需显式运行 `scripts/test_connection.py ... --run`。详细实测范围见 [VALIDATION.md](VALIDATION.md)。

## 独立自动更新

`edition.json` 固定通道。更新清单锁定不可变提交、文件摘要和单调序号，拒绝跨通道、降级和篡改。候选代码在隔离进程中测试，通过后才构建；应用安装后原子激活监测 runtime，并保存中断恢复状态。

macOS 使用 launchd，Windows 使用当前用户任务计划程序；默认每五分钟检查应用、每小时检查补丁与规格，系统锁防止并发安装。变化先稳定检查，全部验证通过后等待正常退出；不会结束用户正在运行的聊天。失败和需要处理的状态去重通知，未变化时安静。

Windows 任务的工作目录使用监测状态目录，runtime 替换前也主动离开该目录。自动构建完成全部验证后，`migrate_windows_working_directory` 仅迁移旧调度任务的工作目录，备份原 XML 并保留其他字段；未注册或已经迁移的任务不改动。旧进程持有 runtime 时保留激活恢复记录，下一轮从新工作目录恢复。

后台下载预检通过 `pythonw.exe` 运行时，凭据 helper 仍使用能够输出凭据的 `python.exe`；测试按这个实际行为检查。CI 使用真实的无窗口 Python 运行完整单元测试，覆盖 Windows 后台执行路径。

发布流程：先提交并验证源码，再执行 `scripts/release_manifest.py --commit FULL_SHA --sequence N` 并提交清单。发布清单本身不计入源码包摘要。CI 覆盖 Windows / Linux 与 Python 3.11 / 3.13；实际应用检查按平台单独记录。

## macOS 固定本机签名

`macos_signing.py` 在登录钥匙串创建不可导出的 RSA 签名私钥，并把证书指纹固定在独立状态目录。后续构建只读取并复用；身份记录丢失但钥匙串仍有唯一匹配条目时恢复记录，已登记密钥丢失或与 baseline 不一致则停止更新。身份要求绑定证书与各组件原标识，而不绑定每版变化的代码哈希。

签名从内到外执行，不使用 `--deep` 重签；验证仍使用严格深层完整性检查。修改的 Codex Framework 及其应用辅助程序与外层应用使用本机证书，其他供应商代码保持原签名。可本地使用的能力声明保留，厂商应用标识、团队、应用组及钥匙串组不复制到本地签名。构建记录每个组件的身份要求与能力声明，安装前后核对。

独立 macOS CI 创建临时签名身份、两个代码不同的原生应用和测试钥匙串项目：第二版必须满足第一版身份要求，并在禁止授权弹窗的条件下读取该项目；临时签名对照必须被拒绝。测试结束删除测试钥匙串，保留原搜索列表中的其他条目。实际麦克风、录屏等权限仍通过 macOS 的正常授权流程。

## Optional image tools

`cpa_images.py` is a stdlib stdio MCP separate from chat transport. It discovers image models via the existing CPA model endpoint, reads the existing client key for each request, uses Images for GPT/Grok and native generateContent for Gemini, and returns validated PNG/JPEG/WebP bytes plus new local output paths. No OAuth material or API key is stored in Codex configuration. `setup-images` adds only its managed MCP section with a full config backup; the stable extension runtime path keeps future updates on the selected edition. Generation has no automatic retry.

## v0.4.8 连接生命周期与数字参数

按需兼容进程空闲 120 秒后退出。原生 CLI 将 `auth.refresh_interval_ms=0` 解释为永久缓存，旧配置因此在同一聊天的后续消息中跳过启动 helper。适配连接改为 1 ms，发起新请求时重新检查健康状态并按需启动；完整支持协议的直连接口仍保留原有凭据缓存策略。无需常驻监测循环。

原生工具声明中的 `number` 字段可能由执行器按整数反序列化。适配器按声明的 `number` / `integer` 字段解析完整调用，仅将范围内且数值为整数的十进制数写成 JSON 整数；使用标准库 Decimal 保留数值精度，不转换字符串或未知字段。支持嵌套声明与命名空间，流式调用在完整参数到达后统一处理；执行、校验和审批仍由 Codex 完成。原生工具测试追加一次无副作用的 echo 调用，检验实际执行结果。
