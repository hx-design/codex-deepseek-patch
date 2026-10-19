
# Codex DeepSeek Adapter

Add DeepSeek Flash alongside native GPT in the official Codex model picker, keeping the original login, tools and same-chat switching.

DS **0.5.0**, update branch `main`.

[中文](README.md) · [Architecture](docs/ARCHITECTURE.md) · [Validation](docs/VALIDATION.md) · [MIT](LICENSE)

## Editions

| Edition | Models | Independent channel |
| --- | --- | --- |
| [DS](https://github.com/limyum-creator/codex-deepseek-patch/tree/main) | Native GPT + DeepSeek Flash | `main` |
| Extension | Native GPT + existing cloud/local services; optional DeepSeek | `codex/existing-proxy-models` |

The extension does not require DeepSeek. Existing services continue to own subscription login and inference.

## Design

The official app stays unchanged: no ASAR edits, macOS re-signing, or copied Windows app. Windows retains its Store package identity and native updater. A small Python standard-library service listens only on loopback, forwarding GPT authentication to its original destination and using separate credentials for registered model services.

The built-in `openai` provider identity is retained, including composer predictions. Model selection, permissions, tool execution and compaction scheduling remain native; external models generate portable readable handoffs through their own service. Switch models after the current reply completes. Provider-specific reasoning signatures are isolated across models; readable history and tool results remain available. Context defaults follow model maxima, while explicit reductions and hidden models are preserved.

The adapter supports Responses and Chat Completions protocols. Claude, GLM, Qwen and other models can use existing services exposing these compatible APIs. Raw Anthropic Messages is not implemented. Capabilities are declared by service metadata or configuration, not guessed from a vendor name. Unknown specifications do not receive arbitrary small context limits.

Python 3.11+ and the official macOS/Windows Codex app are required. The native path has no additional Node.js requirement, resident management UI, database or model engine. A per-user system service runs the adapter; a separate scheduled monitor checks code/models and exits. Windows processes are windowless. Legacy code remains for recovery and regression, not new app modification.


## Prepare and install

```sh
git clone --branch main https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 patch.py setup-key
python3 patch.py build --app /Applications/ChatGPT.app --with-deepseek --output /absolute/path/build --verify
# Quit Codex, then activate:
python3 patch.py install /absolute/path/build
python3 monitor.py enable --app /Applications/ChatGPT.app --baseline /absolute/path/build
```

On Windows use `python`, Windows paths, and omit `--app` for official-install discovery.

Verification uses the original CLI, isolated state and synthetic local endpoints in the background, without GUI launches or paid inference. Installation requires successful validation. For legacy migration, supply `--baseline`; on Mac point `--app` to its verified `original.app` backup and `--install-target` to the installed app. Only this first transition restores the official app.

Existing chats retain their native identity, content, archive state, model and reasoning preference. Old GPT compaction is recovered from locally verified readable records and summarized by the selected external model with its own credentials. **No GPT request or GPT quota is needed.** Shared fork history stops at the exact fork boundary; original files are not rewritten.

Existing handoff caches remain usable. Long records and oversized old summaries are reduced within the selected model's effective context. Missing, corrupt or unsupported local records produce an explicit recovery message instead of silent history loss. Summarization uses the selected service's quota.

## Native settings

Open **Codex Model Settings** from the user Applications folder on macOS or Start menu on Windows, or run `python3 patch.py settings`.

Three tabs combine status/diagnostics, models/connections, and updates/maintenance. Discover endpoints and select models, manage credential references, inspect model/server/effective windows, change visibility and reductions, configure update/sync options and optional image tools, reopen update progress, clean old caches, and recover an interrupted installation. Advanced operations are separated from common controls. Legacy-history recovery exposes progress, cache use and cancellation without exporting conversation text.

The panel starts on demand and exits when closed. It uses native AppKit/WinForms and private file messages, with no web server, new dependency or permanent UI service. Initial installation still uses the CLI.

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

## Native cloud connectivity

Dot uses the official client's own cloud WebSocket. Repeated reconnects can come from system DNS or VPN/proxy routing even when HTTP requests through a system proxy work. The adapter does not silently change system DNS, disable a VPN or modify the official app. Use redacted diagnostics when reporting an issue.

## Repository layout

The root `patch.py` and `monitor.py` remain the public command entries. Implementation, internal tests and maintenance scripts live under [`src/`](src/); design and validation notes live in [`docs/`](docs/). Portable release packages preserve historical paths for existing automatic updaters. Source organization adds no runtime service. Model-only synchronization stages quietly and arms a temporary process-exit observer and applies after app exit; a fast relaunch safely defers installation; new code releases retain the update window.

Updates prefer one immutable source archive with exact per-file verification and retain the legacy download fallback. Explicit checks are queued behind busy work. Conservative cleanup protects active installations, recovery references and recent rollback artifacts. Both editions are generated from one core and require matching macOS/Windows validation receipts before their separate feeds are promoted together.
