# Codex model extension patch

Bring **existing cloud endpoints and local models** into the native Codex model menu. Switch models within the same chat and select supported reasoning levels and context windows.

Connect **Gemini, Grok, Opus, Ollama, LM Studio and other Responses-compatible services**, alongside native GPT. Reuse existing model services and login configuration, with model selection and conversations inside Codex.

This is the **0.4.0-proxy.1 multi-model extension**, on the independent `codex/existing-proxy-models` update channel.

[中文完整指南](README.md) · [Validation](docs/VALIDATION.md) · [Architecture](docs/ARCHITECTURE.md) · [MIT](LICENSE)

## Features

- Switch registered providers and native GPT within the same saved local chat, retaining its ID and history. Finish the current reply before switching.
- Select supported reasoning levels per turn, including proxy model IDs ending in `-high` or `-thinking`.
- Use each model's maximum context by default; preserve deliberate reductions. Refresh legacy saved-chat windows and retain the correct window after compaction.
- Detect Codex updates, check compatibility, rebuild and reinstall after normal exit. Optional patch updates and model/spec synchronization use separate edition feeds.
- Standard-library Python and Node.js only: no extra packages, database or management UI. Natively compatible local models use direct Responses connections. Chat-only models and strict message templates use a small authenticated helper that starts on demand and exits after 120 idle seconds.

Requires Python 3.11+, Node.js 18+, and the official macOS Apple Silicon or Windows desktop app. This unofficial source-only project does not distribute the application, extracted model prompts or credentials.

## Lightweight by design

| Design | Implementation | Benefit |
| --- | --- | --- |
| Native interface | Reuse the model menu, chats and permission controls | Work within the existing Codex application |
| Standard libraries | Python / Node.js standard libraries; JSON / TOML state | No additional Python/npm packages or database service |
| Direct connections | Compatible models call existing Responses endpoints directly | These requests do not traverse a patch relay |
| On-demand adaptation | Strip unsupported tools or normalize strict message templates | The helper starts when needed and exits after about 120 idle seconds |
| Native scheduling | launchd or Windows Task Scheduler runs one check per interval | Each check exits; no persistent monitoring loop |
| Change-based updates | Compare app, runtime and model digests; reuse verified source packages | Unchanged checks avoid rebuilding and redundant downloads |
| Short setup flow | `connect` discovers and saves endpoints; `build --verify` runs all gates | Less manual configuration and fewer separate commands |
| Existing conversations | Resume the same native chat ID with its history and permissions | No second conversation database or history-copy workflow |

The `0.4.0-proxy.1` update runtime uses **22 source files totaling about 263 KiB**, including verification scripts. This measures source size, excluding Python, Node.js, Codex, external services and model weights. Installation still retains application copies and recovery backups; old builds currently require manual management.

## Connect and install

```sh
git clone --branch codex/existing-proxy-models https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 patch.py connect --name local --kind ollama --url http://127.0.0.1:11434/v1
```

Use `--kind lmstudio` for LM Studio (usually port 1234), or `--kind openai` for other **Responses-compatible** services. Pure Chat Completions endpoints are not supported. By default, discover all generation models; `--models ID1 ID2` selects a fixed subset. Missing context metadata requires an explicit verified maximum: `--models MODEL --context-window N`.

`--respect-server-context` preserves an existing smaller local-server window while retaining the model's maximum separately. `--key-file /absolute/path/key.txt` reads an existing API key without copying it into connection settings.

Use the `saved` connection-file path printed by `connect`:

```sh
PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/build-040"
python3 patch.py build --output "$PATCH_BUILD" --connections "/path/to/connections.json" --verify
# Quit Codex / ChatGPT normally, then:
python3 patch.py install "$PATCH_BUILD"
python3 monitor.py enable --app "/Applications/ChatGPT.app" --baseline "$PATCH_BUILD"
```

On Windows, use `python` in PowerShell, choose a build directory under `$env:LOCALAPPDATA`, and pass the official Store app path to monitor `--app`. Builds discover the Store source automatically. The patched app is installed separately under `%LOCALAPPDATA%\CodexDeepSeekPatch\app`; the existing **Codex DeepSeek** Start menu shortcut launches it. The official package remains read-only.

`--verify` runs all isolated catalog, reasoning, context, same-chat switching and boot checks without provider inference. A temporary window may appear. Repeat with `python3 patch.py verify BUILD`. Keep the build directory: configuration references its catalog/helpers and it contains recovery data. App copies require disk space.

## Optional providers

Existing EasyCLIProxyAPI: add these build arguments, using your already configured client endpoint and config file:

```sh
--proxy-config "/path/to/config.yaml" --proxy-base-url "http://127.0.0.1:8317/v1" \
--proxy-models grok-4.5 grok-4.7 gemini-3.8-flash-high claude-opus-4-6-thinking
```

Only the client API key is read; existing software keeps responsibility for subscription login and refresh. Remote proxy endpoints require HTTPS. Select any subset of supported IDs.

DeepSeek: run `python3 patch.py setup-key` and add `--with-deepseek`. Credentials use macOS Keychain or Windows Credential Manager. Fresh extension installs omit DS by default; updates inherit the installed choice. `--without-deepseek` removes a previously managed DS provider unless it is still the global default.

## Update and preferences

Application checks run every five minutes; patch and model checks every hour. Immutable commits, per-file hashes and edition-specific sequences are verified before isolated testing. Installations wait for normal exit. Unchanged states stay quiet; actionable failures are deduplicated.

GPT metadata comes from the native CLI; DS from its official endpoint. Existing proxy Grok, Gemini Flash and Opus families discover new versions and verify vendor specifications. Local connections follow their available generation models unless a fixed subset was selected. Unknown maxima are reported instead of receiving an arbitrary small cap.

```sh
python3 monitor.py status
python3 monitor.py run-once --sync-now
python3 monitor.py model-options --model MODEL --context-window 32768
python3 monitor.py model-options --model MODEL --reset-context
python3 monitor.py model-options --model MODEL --hide
python3 monitor.py model-options --model MODEL --show
python3 monitor.py disable
```

Running `connect` again updates a monitored connection; the next check validates a replacement. Restart after installation; existing chats adopt the selected model's window on the next turn. Per-model reductions and hidden selections persist. `enable --no-code-updates` or `--no-model-sync` disables either feature independently.

For manual updates use a fresh output directory and `--baseline OLD_BUILD`. On macOS, a same-version rebuild uses the signed `OLD_BUILD/original.app` as `--app` and the live application as `--install-target`. Existing managed configuration is validated before replacement. Full commands and current context/reasoning tables are in the [Chinese guide](README.md).

Switching providers sends retained chat history to the newly selected endpoint. Remote-host chats and unregistered custom providers retain their original behavior. Upstream subscription limits still apply; notably the current Antigravity adapter's static Opus context declaration differs from the vendor maximum.

## Validation and recovery

```sh
python3 -m unittest discover -s tests -q
node tests/route.test.cjs
# Optional live reply/tool smoke test, using provider quota:
python3 scripts/test_connection.py "$PATCH_BUILD" --model MODEL --run
# Stop monitoring and quit the app before restoring:
python3 patch.py restore "$PATCH_BUILD"
```

Recovery validates the current app/config and restores the previous state. macOS copies use ad-hoc signatures; resource integrity checks remain enabled. Never publish `.local/`, builds, logs or personal configuration. See [NOTICE](NOTICE) for third-party ownership.

## Other edition

For DeepSeek official direct access alone, use the [standalone edition on main](https://github.com/limyum-creator/codex-deepseek-patch/tree/main). This extension combines cloud endpoints and local models as needed. The editions have independent update channels.
