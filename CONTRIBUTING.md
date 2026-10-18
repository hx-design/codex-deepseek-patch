# Contributing

Issues and pull requests for additional app versions are welcome. Include the app version, build, CPU architecture, CLI version, failing step, and a redacted error message.

Do not upload app bundles, complete extracted JavaScript files, generated model catalogs, user configuration, API keys, authentication files, or raw personal logs.

For a new adapter:

1. Identify the matching request and prewarm behavior in a locally obtained official app.
2. Add an explicit version/build and original-hash gate. Every replacement anchor must match exactly once.
3. Preserve GPT, remote hosts, explicit custom providers, existing chats, and unrelated configuration.
4. Keep integrity checks enabled and update both ASAR and framework digests.
5. Run offline tests, actual-bundle routing checks, an isolated application boot, and an install/restore test against a disposable target.
6. Record exactly what was verified. Run live provider tests only when explicitly requested; they use API credits.

```sh
python3 -m unittest discover -s tests -v
node tests/route.test.cjs
```
