# Codex DeepSeek Patch

Add **DeepSeek 4.1 Flash** to the macOS Codex model picker while retaining the existing GPT models and default selection. New local chats using `deepseek-flash` are routed to the DeepSeek API.

This is an unofficial, source-only patch. It does not redistribute the desktop app, extracted bundles, model system prompts, user configuration, or credentials. [中文文档](README.md)

## Compatibility

- Apple Silicon macOS; app and build directory on the same APFS volume.
- App version **26.1013.40934**, build **20770**.
- Tested bundled CLI: **0.163.0-alpha.10.2**.
- Python **3.11+**, Node.js **18+**. No third-party Python/npm dependencies.
- Exact version, original archive hash, source anchors, and integrity metadata are checked before modifying a copy. Unsupported builds are rejected.

As verified on October 18, 2026, `deepseek-flash` is served by DeepSeek-V4.1-Flash-1017; the provider controls future alias updates. [Official API documentation](https://api-docs.deepseek.com/quick_start/agent_integrations/)

## Install

```sh
git clone https://github.com/limyum-creator/codex-deepseek-patch.git
cd codex-deepseek-patch
python3 patch.py setup-key

PATCH_BUILD="$HOME/Library/Application Support/CodexDeepSeekPatch/26.1013.40934"
python3 patch.py build --output "$PATCH_BUILD"
python3 patch.py test-boot "$PATCH_BUILD"
```

`setup-key` prompts through the macOS security tool and stores the key in Keychain. Skip it if the matching entry already exists. No key is included in command arguments or configuration files.

The build uses copies; it does not change the installed app. The boot test briefly opens an isolated window and then stops only its own probe. **Quit the desktop app normally**, then run in Terminal:

```sh
python3 patch.py install "$PATCH_BUILD"
```

Open the app and create a **new local chat**, then select **DeepSeek 4.1 Flash**. Existing chats retain their provider; start a new chat when switching between GPT and DeepSeek. Remote hosts are outside this patch's scope.

Use `--app`, `--codex-home`, `--keychain-account`, and `--keychain-service` on `build` to customize paths and Keychain lookup. Each build needs a new output directory. Custom catalog/default-provider conflicts are rejected for review.

## Restore and tests

```sh
python3 patch.py status "$PATCH_BUILD"
# Quit the app before restoring.
python3 patch.py restore "$PATCH_BUILD"

python3 -m unittest discover -s tests -v
node tests/route.test.cjs
node tests/route.test.cjs "$PATCH_BUILD/patched-client.mjs"

# Optional: sends two small requests and incurs DeepSeek API usage.
python3 scripts/test_connection.py "$PATCH_BUILD" --run
```

Keep the build directory: it contains the original app backup and the model catalog referenced by the installed configuration. Restore refuses to overwrite configuration changed after installation.

## Operational details

The patch updates ASAR member/block hashes, the header hash, and the framework integrity dictionary digest. Integrity validation remains enabled. The modified bundle uses ad-hoc signing, which may affect automatic updates, access groups, notifications, and services requiring the official signing identity. Restore recovers the original signed bundle.

Local thread prewarm reuse is disabled to prevent provider mismatches. GPT models are a snapshot taken at build time; refresh by rebuilding when the catalog changes. Official app updates require a compatibility check. No background updater or automatic process termination is installed.

Generated directories contain vendor resources and potentially private configuration. Keep them local; only the repository's source files should be shared. See [validation](docs/VALIDATION.md), [implementation](docs/ARCHITECTURE.md), and [NOTICE](NOTICE).

## License

[MIT](LICENSE) for the original patch code; vendor applications, trademarks, and resources retain their respective ownership and licenses.
