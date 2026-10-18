# Codex DeepSeek Patch · Existing Proxy Edition

This is **`codex/existing-proxy-models`**, an optional extension based on v0.2.1, versioned `0.2.1-proxy.1`. Grok / Gemini / Claude require an existing running EasyCLIProxyAPI setup. The standalone DeepSeek edition remains on [main](https://github.com/limyum-creator/codex-deepseek-patch/tree/main).

Add **DeepSeek 4.1 Flash** to the macOS / Windows Codex model picker while retaining GPT models and default settings. New local chats selecting `deepseek-flash` use the DeepSeek API. Optionally add **Grok 4.5 / 4.7, Gemini 3.8 Flash, and Claude Opus 4.6 Thinking** through an existing EasyCLIProxyAPI endpoint.

**The proxy edition** adds optional existing-proxy integration. Native GPT and direct DeepSeek connections remain intact, with compatibility checks, automatic reinstallation, notifications, and restoration.

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
git clone --branch codex/existing-proxy-models https://github.com/limyum-creator/codex-deepseek-patch.git
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

## Optional existing proxy integration

Configure and run [EasyCLIProxyAPI](https://github.com/router-for-me/EasyCLIProxyAPI) first. Add `--proxy-config "/path/to/existing/config.yaml" --proxy-base-url "http://127.0.0.1:8317/v1" --proxy-models grok-4.5 grok-4.7 gemini-3.8-flash-high claude-opus-4-6-thinking` to `build --compatible`. Choose only models available from your endpoint.

The helper reads client `access.api-keys` (or legacy root `api-keys`) from the existing configuration. It does not copy keys or import OAuth credentials. Keep the proxy running and that file in place. Remote endpoints require HTTPS; loopback HTTP is supported. The presets use text input, shell tools, conservative 128k context, and provider reasoning presets.

Run offline/startup tests and `python3 scripts/test_connection.py BUILD --model MODEL_ID --run` for each selected model before installation. Live tests consume provider usage. Start a new local chat when switching providers. Re-enable monitoring with the installed baseline; later builds retain its selected models and settings without inference calls.

To refresh a patched Mac from a retained signed original backup, combine `--app ORIGINAL_APP --baseline PREVIOUS_BUILD --install-target /Applications/ChatGPT.app`. The target must match the baseline; restore returns to the prior app and configuration. Windows uses its existing separate-deployment workflow with a local proxy configuration path.

## Optional automatic reinstallation after updates

After one successful installation, enable the monitor. macOS:

```sh
python3 monitor.py enable --app "/Applications/ChatGPT.app" --baseline "$PATCH_BUILD"
python3 monitor.py status
```

Windows PowerShell, with the official app as source and the installed build as baseline:

```powershell
$CodexApp = Join-Path (Get-AppxPackage -Name OpenAI.Codex | Select-Object -First 1 -ExpandProperty InstallLocation) "app"
python monitor.py enable --app "$CodexApp" --baseline "$PatchBuild"
python monitor.py status
```

Existing macOS builds from the earlier local installer can also be passed as the baseline. The monitor verifies and adopts the installed patch before watching for updates.

The service uses launchd on macOS and a per-user Task Scheduler task on Windows. It checks at login and every **five minutes** by default. After a stable app change, it verifies the signature and source contracts, builds a copy, runs offline routing checks, and briefly opens an isolated startup window. Passing builds wait for you to quit normally before installation. It never terminates your running chats.

Unknown structures and failures retain evidence and produce a notification deduplicated for that problem. Unchanged checks stay quiet. Builds and recovery data are retained. The monitor does not download new patch code, refresh a catalog over the network, or invoke paid model APIs.

```sh
python3 monitor.py status
python3 monitor.py run-once --retry
python3 monitor.py disable
```

On Windows, use `python` instead of `python3`. Disabling preserves installed files and records. After updating this repository, run `enable` again to refresh the monitor's private runtime copy.

## Restore and tests

```sh
python3 patch.py status "$PATCH_BUILD"
# Disable the monitor if enabled, then quit the app normally.
python3 patch.py restore "$PATCH_BUILD"

python3 -m unittest discover -s tests -v
node tests/route.test.cjs
```

macOS restore recovers the previous app and prior configuration. Windows restore recovers the previous managed copy, shortcut, and configuration; the first installation removes its managed copy on restore. Restore stops when configuration or the target has changed since installation.

Keep build directories: installed configuration references their catalogs, and recovery depends on their contents. Compatible builds save `compatibility.json` with source digests, insertion locations, extracted methods, and behavior-check results for local review.

Optional live testing sends two small DeepSeek conversations and incurs API usage. It runs only with explicit `--run`:

```sh
python3 scripts/test_connection.py "$PATCH_BUILD" --run
```

Platform/version-specific checks and their scope are recorded in [validation](docs/VALIDATION.md).

## Operational details

The adapter routes new local DS threads to `deepseek-desktop`, preserves other request fields, and lists chats across providers. Local prewarm reuse is bypassed to prevent provider mismatches; new chat creation may be slightly slower.

GPT entries come from the bundled CLI's offline catalog, with one Flash entry added. Rebuilding refreshes that snapshot. ASAR integrity checks remain enabled: the builder updates member/block hashes, the header hash, and native integrity metadata. macOS copies use ad-hoc signing; Windows copies modify the integrity carrier. Features requiring the original signature, including updating, may be affected.

Generated files contain vendor resources and potentially private configuration. Keep them local. See [NOTICE](NOTICE).

## License

[MIT](LICENSE) for original patch code; vendor applications, trademarks, and resources retain their respective ownership and licenses.
