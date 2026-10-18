# Codex DeepSeek Patch

Add **DeepSeek 4.1 Flash** to the macOS / Windows Codex model picker while retaining GPT models and default settings. New local chats selecting `deepseek-flash` use the DeepSeek API.

**v0.3.0** adds edition-pinned patch self-updates and model/specification synchronization, preserving explicit smaller context settings alongside application-update compatibility checks and automatic reinstallation.

This unofficial repository ships patch source only. It does not redistribute the app, extracted bundles, model system prompts, user configuration, or credentials. [中文文档](README.md) · [Validation](docs/VALIDATION.md) · [Architecture](docs/ARCHITECTURE.md)

## Compatibility

- Apple Silicon macOS; app and build directory on the same APFS volume.
- Windows official Codex app. Store packages are read-only sources; installation uses a separate user-owned copy.
- Inspected baseline: app **26.1013.40934**, build **20770**. The historical tested CLI is **0.163.0-alpha.10.2**; see validation records for platform-specific evidence.
- Python **3.11+**, Node.js **18+**. No third-party Python/npm dependencies.

Compatible mode verifies the official publisher, resource integrity, unique request contracts, and the behavior of extracted methods. Asset hashes, filenames, and minified identifiers may change. Unknown control flow stops the build. The v0.1 fixed-version build remains available for its original baseline; use `--compatible` for new installations and updates.

As verified on October 18, 2026, `deepseek-flash` is served by DeepSeek-V4.1-Flash-1017. DeepSeek controls alias updates. [Official API documentation](https://api-docs.deepseek.com/quick_start/agent_integrations/)

## macOS installation

Install and sign in to the official app first. Choose a new build directory that you can retain.

```sh
git clone https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 --version
node --version
python3 patch.py setup-key

PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/build-20261018"
python3 patch.py build --compatible --output "$PATCH_BUILD"
python3 scripts/test_offline.py "$PATCH_BUILD"
python3 patch.py test-boot "$PATCH_BUILD"

# Quit Codex normally, then install.
python3 patch.py install "$PATCH_BUILD"
```

`setup-key` stores the interactively entered API key in macOS Keychain. Build works on copies. The offline check uses temporary configuration and a loopback placeholder provider to verify catalog and ephemeral thread routing without an inference turn. The startup check briefly opens an isolated window, checks the renderer and backend, and stops only its own test processes.

The default source is `/Applications/ChatGPT.app`, falling back to `/Applications/Codex.app`. Set `--app "/path/to/ChatGPT.app"` or `--codex-home "$HOME/.codex"` on `build` as needed.

## Windows installation

Install official Codex, Python 3.11+, and Node.js 18+. In PowerShell, from the repository directory:

```powershell
python --version
node --version
python patch.py setup-key

$PatchBuild = Join-Path $env:LOCALAPPDATA "CodexDeepSeekPatch\build-20261018"
python patch.py build --compatible --output "$PatchBuild"
python scripts/test_offline.py "$PatchBuild"
python patch.py test-boot "$PatchBuild"

# Quit Codex normally, then install.
python patch.py install "$PatchBuild"
```

Build discovers the official Store app; use `--app "C:\path\to\app"` for an explicit source. The key is entered interactively and stored in **Windows Credential Manager**.

The patched copy lives at `%LOCALAPPDATA%\CodexDeepSeekPatch\app`. Open **Codex DeepSeek** from the Start menu; its shortcut launches that copy's `ChatGPT.exe`. The official Store app stays in its original location. Keep the build and managed target on the same volume, with space for complete application copies and recovery data.

## Usage and configuration

Open the patched app, create a **new local chat**, and select **DeepSeek 4.1 Flash**. Existing chats retain their provider; start a new chat when switching between GPT and DeepSeek. Remote hosts retain their existing routing.

Each build needs a fresh output directory. Initial installation rejects custom catalog/default-provider conflicts for review. To update an existing installation, pass `--baseline "PREVIOUS_BUILD"` to `build --compatible`. This refreshes the registered catalog while preserving unrelated user settings. Changes to the managed catalog or DeepSeek provider stop the update for review.

For a manual refresh, select the official updated app at its intended installation path. To rebuild the same version, restore first, then follow the initial installation steps without `--baseline`, since restore also reverts configuration. On macOS, `--app` is also the installation target. A patched copy is rejected as a compatible-build source:

```sh
NEW_PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/build-next"
python3 patch.py build --compatible \
  --app "/path/to/official/ChatGPT.app" \
  --baseline "$PATCH_BUILD" --output "$NEW_PATCH_BUILD"
python3 scripts/test_offline.py "$NEW_PATCH_BUILD"
python3 patch.py test-boot "$NEW_PATCH_BUILD"
# Quit the app normally before installing.
python3 patch.py install "$NEW_PATCH_BUILD"
```

Use matching `--keychain-account` and `--keychain-service` values with `setup-key` and `build` to customize the credential lookup. Defaults are the current username and `Codex DeepSeek API Key`.

## Automatic updates and model synchronization

After the first installation, enable all three functions: application-update reinstallation, patch self-updates, and model/specification refresh.

```sh
python3 monitor.py enable --app "/Applications/ChatGPT.app" --baseline "$PATCH_BUILD"
python3 monitor.py status
python3 monitor.py run-once --sync-now
```

Windows uses `python`, the official Store source for `--app`, and the installed build for `--baseline`. Existing users must run `enable` once with this release to upgrade their saved monitor runtime.

The DS edition follows `main`; the proxy edition follows `codex/existing-proxy-models`. Each publishes its own `update.json` pinned to an immutable commit and SHA-256 file inventory. The monitor rejects cross-edition packages, rollbacks, and changed hashes. Only the proxy edition depends on an existing EasyCLIProxyAPI setup.

Application checks run every five minutes; code and model checks run hourly. Downloaded code passes isolated regression tests before a compatible app is rebuilt, checked with offline routing/context probes, and launched in isolation. Installation waits for normal application exit. Runtime activation has a persistent recovery journal. Failures and unknown structures are reported once per issue; unchanged checks are quiet. Checks do not submit inference turns to providers.

Native GPT metadata refreshes through the bundled CLI. DeepSeek metadata comes from its official model endpoint. Proxy IDs come from the configured proxy, with specifications resolved from xAI, Google, and Anthropic documentation. Newly appearing **Grok, Gemini Flash, and Opus** versions are automatically added; DS follows the Flash family. The first successful check records existing IDs without resurrecting older excluded models. Missing models are removed after two successful absent observations and restored if they reappear. New models without verified specifications wait with a notification instead of receiving an arbitrary small context window.

Context defaults follow each model's maximum at 100% effective capacity. Explicit lower per-model preferences and existing GPT/DS global settings survive refreshes; proxy models use their own limits. Automatic compaction defaults to 90% of the configured window, retaining lower user thresholds.

```sh
python3 monitor.py model-options --model deepseek-flash --context-window 262144
python3 monitor.py model-options --model deepseek-flash --reset-context
python3 monitor.py model-options --model deepseek-flash --hide
python3 monitor.py model-options --model deepseek-flash --show
python3 monitor.py run-once --retry
python3 monitor.py disable
```

Preferences live in the monitor's `model-preferences.json` and apply after synchronization and normal application exit. New chats use the refreshed settings. `enable --no-code-updates` and `--no-model-sync` independently disable either feature. Keep the current and previous recovery builds; update packages and app copies consume disk space.

## Restore and tests

```sh
python3 patch.py status "$PATCH_BUILD"
# Disable the monitor if enabled, then quit the app normally.
python3 patch.py restore "$PATCH_BUILD"

python3 -m unittest discover -s tests -v
node tests/route.test.cjs
```

macOS restore recovers the original signed app and prior configuration. Windows restore recovers the previous managed copy, shortcut, and configuration; the first installation removes its managed copy on restore. Restore stops when configuration or the target has changed since installation.

Keep build directories: installed configuration references their catalogs, and recovery depends on their contents. Compatible builds save `compatibility.json` with source digests, insertion locations, extracted methods, and behavior-check results for local review.

Optional live testing sends two small DeepSeek conversations and incurs API usage. It runs only with explicit `--run`:

```sh
python3 scripts/test_connection.py "$PATCH_BUILD" --run
```

Platform/version-specific checks and their scope are recorded in [validation](docs/VALIDATION.md).

## Operational details

The adapter routes new local DS threads to `deepseek-desktop`, preserves other request fields, and lists chats across providers. Local prewarm reuse is bypassed to prevent provider mismatches; new chat creation may be slightly slower.

The first build uses bundled GPT metadata. The monitor then refreshes native GPT and enabled provider metadata before validated rebuilds. ASAR integrity checks remain enabled: the builder updates member/block hashes, the header hash, and native integrity metadata. macOS copies use ad-hoc signing; Windows copies modify the integrity carrier. Features requiring the original signature, including updating, may be affected.

Generated files contain vendor resources and potentially private configuration. Keep them local. See [NOTICE](NOTICE).

## License

[MIT](LICENSE) for original patch code; vendor applications, trademarks, and resources retain their respective ownership and licenses.
