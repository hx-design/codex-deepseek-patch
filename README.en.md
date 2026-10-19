# Codex Model Extension

Add existing cloud endpoints and local models to the **official Codex model picker**, retaining the original login, chat history and native tools.

Extension **0.5.6-proxy.1**, update branch `codex/existing-proxy-models`.

[中文](README.md) · [Architecture](docs/ARCHITECTURE.md) · [Validation](docs/VALIDATION.md) · [MIT](LICENSE)

## Editions

| Edition | Models | Independent channel |
| --- | --- | --- |
| [DS](https://github.com/limyum-creator/codex-deepseek-patch/tree/main) | Native GPT + DeepSeek Flash | `main` |
| Extension | Native GPT + existing cloud/local services; optional DeepSeek | `codex/existing-proxy-models` |

The extension does not require DeepSeek. Existing services continue to own subscription login and inference.

## Design

The official app stays unchanged: no ASAR edits, macOS re-signing, or copied Windows app. Windows retains its Store package identity and native updater. A small Python standard-library service listens only on loopback, forwarding GPT authentication to its original destination and using separate credentials for registered model services.

Model selection, permissions, tool execution and readable-summary compaction remain native. Switch models after the current reply completes. Provider-specific reasoning signatures are isolated across models; readable history and tool results remain available. Context defaults follow model maxima, while explicit reductions and hidden models are preserved.

The adapter supports Responses and Chat Completions protocols. Claude, GLM, Qwen and other models can use existing services exposing these compatible APIs. Raw Anthropic Messages is not implemented. Capabilities are declared by service metadata or configuration, not guessed from a vendor name. Unknown specifications do not receive arbitrary small context limits.

Python 3.11+ and the official macOS/Windows Codex app are required. The native path has no additional Node.js requirement, UI, database or model engine. A per-user system service runs the adapter; a separate scheduled monitor checks code/models and exits. Windows processes are windowless. Legacy code remains for recovery and regression, not new app modification.

## Connect and install

```sh
git clone --branch codex/existing-proxy-models https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 patch.py connect --name local --kind ollama --url http://127.0.0.1:11434/v1
```

Use `--kind lmstudio` for LM Studio, or `--kind openai` for compatible services. Chat Completions endpoints require `--wire-api chat_completions`. Authentication can use `--key-file /absolute/path/key.txt`; no key is stored in the connection JSON. Select models with `--models ID1 ID2`. If metadata omits the maximum context, supply the actual model specification with `--context-window N` or `model_specs` in the connection file.

`--respect-server-context` preserves a smaller local server window without changing server settings. Enable `--supports-reasoning-effort` only for a Chat Completions service that accepts this field. `model_specs` can declare context, reasoning levels, input modalities and tool support.

```sh
python3 patch.py build --app /Applications/ChatGPT.app \
  --connections /absolute/path/connections.json --output /absolute/path/build --verify
# Quit Codex before activation:
python3 patch.py install /absolute/path/build
python3 monitor.py enable --app /Applications/ChatGPT.app --baseline /absolute/path/build
```

On Windows use `python`, Windows paths, and omit `--app` to discover the official installation. Existing EasyCLI/CLIProxyAPI can be selected with `--proxy-config /absolute/path/config.yaml` and optionally `--proxy-base-url`. Combine connections and proxy options as needed. DeepSeek is opt-in: `setup-key`, then build with `--with-deepseek`.

Verification uses the original CLI, isolated state and synthetic local endpoints in the background, without GUI launches or paid inference. Installation requires successful validation. For legacy migration, supply `--baseline`; on Mac point `--app` to its verified `original.app` backup and `--install-target` to the installed app. Only this first transition restores the official app.

Existing chats migrate through native metadata APIs without rewriting history or SQL. Historical external signatures are indexed by hash locally. When an old GPT chat contains proprietary compacted state, its first external-model request obtains a readable task summary through GPT and caches it; this uses one GPT request.

## Non-blocking progress

A native, dismissible progress window appears for a new update. Continue working or close it; background work continues. Pressing Start Update requests a normal Codex exit only after preparation and validation. Installation asks you to keep Codex closed. Completion offers Launch Codex, without opening it automatically.

Known work units show real counts; unknown-duration phases use an activity bar. The same version does not repeatedly reopen the prompt; completion can appear once after a progress view was dismissed. During the first legacy upgrade, the panel becomes available after the new components are downloaded.

## Maintenance

```sh
python3 monitor.py status
python3 monitor.py run-once --sync-now
python3 monitor.py model-options --model MODEL_ID --context-window 32768
python3 monitor.py model-options --model MODEL_ID --hide
python3 monitor.py model-options --model MODEL_ID --reset-context
python3 monitor.py disable
python3 patch.py recover /absolute/path/build
python3 patch.py restore /absolute/path/build
```

Code/model checks are independently configurable and edition-pinned. New Grok, Gemini Flash and Opus family versions are added while explicit exclusions remain. Installation waits for Codex to exit. Interrupted activation is recoverable. Restore reverses this adapter's configuration and keeps the official app; earlier legacy provider definitions are restored as recorded.

macOS privacy/keychain databases are not reset. Returning from a locally signed legacy app to the official identity may require one system permission confirmation; subsequent adapter updates do not re-sign the app.

Optional images: `python3 patch.py setup-images --proxy-config /absolute/path/config.yaml` uses existing CPA image endpoints through MCP. Model availability and account quotas belong to that service.

Unofficial project. Source only: never publish app bundles, extracted catalogs, personal configuration, credentials or raw user logs. See the validation document for real-model and synthetic coverage separately.

## Repository layout

The root `patch.py` and `monitor.py` remain the public command entries. Implementation, internal tests and maintenance scripts live under [`src/`](src/); design and validation notes live in [`docs/`](docs/). Portable release packages preserve historical paths for existing automatic updaters. Source organization adds no runtime service. Model-only synchronization stages quietly and waits for normal app exit; new code releases retain the update window.
