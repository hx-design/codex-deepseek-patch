# Contributing

Issues and pull requests for app compatibility or platform support are welcome. Include OS/CPU, app version/build, bundled CLI version, the failing command, and a redacted error. For monitor issues, include its phase and the relevant redacted event.

Do not upload app bundles, complete extracted JavaScript, generated catalogs, user configuration, API keys, authentication files, or raw personal logs. Local evidence and backups belong in ignored build directories.

## Compatibility changes

1. Inspect a locally obtained official app and verify its publisher and resource integrity. Preserve the v0.1 fixed-version path when changing compatible mode.
2. Describe the request/prewarm contract in `compatibility.py`. Asset filenames and minified names may vary; unknown behavior, missing anchors, duplicate targets, or existing patch markers must stop the operation. A version-only allowlist is insufficient for compatible mode.
3. Keep edits restricted to recorded spans and the route helper. Add tests for renamed variables/assets, changed control flow, ambiguity, marker detection, and preservation outside intended changes.
4. Exercise actual extracted methods in the Node VM probe. Preserve GPT and remote routes, explicit providers, existing chat behavior, config reads, plugin timeouts, permissions, and unrelated configuration.
5. Preserve ASAR hashes and native integrity enforcement. Keep Store installations read-only and Windows deployments in the managed user directory.
6. Bind inspection evidence to the source archive, verify copied sources, and recheck before installation. Test transactions and rollback against disposable targets.

## Monitor and configuration changes

Keep unchanged checks quiet, notifications deduplicated, and state writes atomic. The monitor must wait for normal app exit, retain recovery data, and never submit paid inference or fetch new patch code automatically. Scheduler registration must remain optional and reversible.

Managed updates may refresh the registered catalog path while preserving unrelated user edits. Changes to managed provider/catalog fields must stop for review. Test baseline adoption separately for in-place macOS installation and Windows's separate deployment/source paths.

## Validation

```sh
python3 -m unittest discover -s tests -v
node tests/route.test.cjs
```

For a locally built compatible app:

```sh
python3 patch.py build --compatible --app "/path/to/official/app" --output "/new/build/directory"
python3 scripts/test_offline.py "/new/build/directory"
python3 patch.py test-boot "/new/build/directory"
```

On Windows, use `python` and Windows paths. Run an install/restore cycle against a disposable target before claiming deployment support. Document the platform, exact version, command, result, and evidence layer in `docs/VALIDATION.md`. Distinguish synthetic tests, source/syntax checks, offline app-server routing, isolated GUI startup, install/restore, and live model calls.

Live provider tests require an explicit request and incur API usage. A previous live result must retain its original version/date; it should not be presented as validation of a new runtime.
