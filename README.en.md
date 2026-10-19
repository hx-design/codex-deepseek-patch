# Codex DeepSeek Adapter

Add DeepSeek Flash to the **official Codex model picker**, retaining the original login, chat history and native tools.

DS **0.4.11**, update branch `main`.

[中文](README.md) · [Architecture](docs/ARCHITECTURE.md) · [Validation](docs/VALIDATION.md) · [MIT](LICENSE)

## Editions

| Edition | Models | Independent channel |
| --- | --- | --- |
| [DS](https://github.com/limyum-creator/codex-deepseek-patch/tree/main) | Native GPT + DeepSeek Flash | `main` |
| [Extension](https://github.com/limyum-creator/codex-deepseek-patch/tree/codex/existing-proxy-models) | Native GPT + existing cloud/local services; optional DeepSeek | `codex/existing-proxy-models` |

The extension does not require DeepSeek. Existing services continue to own subscription login and inference.

## Design

The official app stays unchanged: no ASAR edits, macOS re-signing, or copied Windows app. Windows retains its Store package identity and native updater. A small Python standard-library service listens only on loopback, forwarding GPT authentication to its original destination and using separate credentials for registered model services.

Model selection, permissions, tool execution and readable-summary compaction remain native. Switch models after the current reply completes. Provider-specific reasoning signatures are isolated across models; readable history and tool results remain available. Context defaults follow model maxima, while explicit reductions and hidden models are preserved.

The adapter supports Responses and Chat Completions protocols. Claude, GLM, Qwen and other models can use existing services exposing these compatible APIs. Raw Anthropic Messages is not implemented. Capabilities are declared by service metadata or configuration, not guessed from a vendor name. Unknown specifications do not receive arbitrary small context limits.

Python 3.11+ and the official macOS/Windows Codex app are required. The native path has no additional Node.js requirement, UI, database or model engine. A per-user system service runs the adapter; a separate scheduled monitor checks code/models and exits. Windows processes are windowless. Legacy code remains for recovery and regression, not new app modification.

## Install DS

```sh
git clone https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 patch.py setup-key
python3 patch.py build --app /Applications/ChatGPT.app --output /absolute/path/build --verify
# Quit Codex:
python3 patch.py install /absolute/path/build
python3 monitor.py enable --app /Applications/ChatGPT.app --baseline /absolute/path/build
```

Use `python` and Windows paths on Windows; omit `--app` for official installation discovery. Verification is background-only using the original CLI and synthetic interfaces, without paid inference. Legacy migration uses `--baseline`; on Mac select the verified `original.app` backup with `--app` and the installation path with `--install-target`. The first migration restores the official app; later updates only replace adapter/configuration artifacts.

Existing chats retain their native identity, content, archive state, model and reasoning preference. Old GPT compaction is recovered from locally verified readable records and summarized by the selected external model with its own credentials. **No GPT request or GPT quota is needed.** Shared fork history stops at the exact fork boundary; original files are not rewritten.

Existing handoff caches remain usable. Long records and oversized old summaries are reduced within the selected model's effective context. Missing, corrupt or unsupported local records produce an explicit recovery message instead of silent history loss. Summarization uses the selected service's quota.

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
python3 patch.py recover /absolute/path/build
python3 patch.py restore /absolute/path/build
python3 monitor.py disable
```

Code/model checks are independently configurable and edition-pinned. New Grok, Gemini Flash and Opus family versions are added while explicit exclusions remain. Installation waits for Codex to exit. Interrupted activation is recoverable. Restore reverses this adapter's configuration and keeps the official app; earlier legacy provider definitions are restored as recorded.

macOS privacy/keychain databases are not reset. Returning from a locally signed legacy app to the official identity may require one system permission confirmation; subsequent adapter updates do not re-sign the app.

Unofficial project. Source only: never publish app bundles, extracted catalogs, personal configuration, credentials or raw user logs. See the validation document for real-model and synthetic coverage separately.

## Repository layout

The root `patch.py` and `monitor.py` remain the public command entries. Implementation, internal tests and maintenance scripts live under [`src/`](src/); design and validation notes live in [`docs/`](docs/). Portable release packages preserve historical paths for existing automatic updaters. Source organization adds no runtime service. Model-only synchronization stages quietly and arms a temporary process-exit observer and applies after app exit; a fast relaunch safely defers installation; new code releases retain the update window.
