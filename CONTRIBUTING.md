# Contributing

Issues and pull requests for app compatibility or platform support are welcome. Include OS/CPU, app version/build, bundled CLI version, the failing command, and a redacted error. For monitor issues, include its phase and the relevant redacted event.

Do not upload app bundles, complete extracted JavaScript, generated catalogs, user configuration, API keys, authentication files, or raw personal logs. Local evidence and backups belong in ignored build directories.

## 每次修复的四组合核对 / Four-variant bug-fix review

每个 bug 都必须在同一次任务中核对以下四种组合，记录受影响、已修复并验证、或不受影响的具体原因：

| Platform | DS edition (`main`) | Extension (`codex/existing-proxy-models`) |
| --- | --- | --- |
| macOS | Required review | Required review |
| Windows | Required review | Required review |

- 同一原因涉及的代码在所有受影响组合同步修复；平台专属问题也要明确核对另一平台的相关路径。
- 每条发布分支独立版本、更新序号和来源摘要，保持两版各自更新。
- 对请求更新的机器核验实际安装版本、运行代码和保留的用户设置，分别记录已构建、已验证、已发布、已安装和已重启状态。
- Every bug fix reviews all four combinations in the same task. Apply shared fixes to every affected combination and state the reason for unaffected cases.
- Keep edition-specific releases and feeds independent. Record validation and the actual installation state on each requested machine separately from publication.

## Native integration changes

1. Verify the original app publisher and bundled CLI before probing. New architecture must not modify ASAR, re-sign the app or copy a Store package.
2. Keep adapter rules protocol/capability based. Add synthetic coverage for arbitrary model identities, both wire APIs, streaming, tool results, errors, cancellation, reasoning provenance and context transitions.
3. Run the original CLI with isolated state and synthetic endpoints in the background. Do not launch GUI windows or touch the user's keychain during routine validation.
4. Preserve native login, tool execution, approvals, chat identity, working directory and readable-summary compaction. Test native API metadata migrations and interruption recovery; never write the history database directly.
5. Stage and validate the same bytes. Recheck source/runtime/profile identity before activation. Test rollback with fault injection and real Windows normal-user service states.

## Monitor and configuration changes

Keep unchanged checks quiet, notifications deduplicated, and state writes atomic. Wait for normal app exit. Scheduled checks must not submit inference. Code updates follow the edition-pinned feed with digests and isolated preflight; reject native-to-legacy downgrades. Registration remains reversible.

Only owned configuration changes are allowed. Rebase against the latest configuration under the installation lock, retain unrelated edits, and restore the immediately previous state after activation failures. Preserve explicit model reductions/hiding and original server configuration. Cloud specification errors retain the last known good value.

## Validation

```sh
cd src
python3 -m unittest discover -s tests -v
node tests/route.test.cjs  # retained legacy regression
python3 patch.py build --app "/path/to/official/app" --connections "/path/to/connections.json" --output "/new/build/directory" --verify
```

On Windows use `python` and Windows paths. Validate both DS/extension on Mac/Windows. Record unit fixtures, original CLI synthetic integration, real model calls, service tests and actual installation separately. Models without credentials receive synthetic protocol coverage, not a real-model success label.

Live provider tests require user authorization and use minimal synthetic chats. Never publish app bundles, generated vendor catalogs, tokens, raw user history or private execution evidence. Real tests should use ephemeral authentication and avoid login refresh. Native verification does not require GUI boot or local signing tests; retain those only for legacy recovery changes.

## Source layout and portable releases

Development code lives in `src/`. Keep the root command wrappers small; all internal imports remain within the source tree. Tests and build fixtures are source materials, not additional running services.

Prepare both editions from one committed source revision, then validate those exact portable bytes on both platforms:

```sh
python3 src/scripts/release_manifest.py --commit FULL_SOURCE_SHA --prepare-pair PRIVATE_CANDIDATE_DIR --ds-version DS_VERSION --proxy-version PROXY_VERSION
python3 src/scripts/release_manifest.py --verify-candidate PRIVATE_CANDIDATE_DIR/candidate.json --edition ds --app OFFICIAL_APP --output PRIVATE_RECEIPTS_DIR
python3 src/scripts/release_manifest.py --verify-candidate PRIVATE_CANDIDATE_DIR/candidate.json --edition proxy --app OFFICIAL_APP --output PRIVATE_RECEIPTS_DIR
```

Run the two verification commands on macOS and Windows. On Windows use ordinary-user `pythonw`, with redirected output and no console. Gather all four receipts and their logs in one private directory. Each receipt binds the candidate, portable commit, shared-core digest and test log hashes. Push the candidate source branch for CI first; after it succeeds, publish both independent feeds and tags with one atomic operation:

```sh
python3 src/scripts/release_manifest.py --promote PRIVATE_CANDIDATE_DIR/candidate.json --receipts PRIVATE_RECEIPTS_DIR --execute
```

Candidate preparation uses isolated Git indexes without changing the checkout or published feed. Portable artifacts retain historical paths and the 100-file limit for existing updaters. The old `--distribution` command remains available for historical tooling; new paired releases use the gates above. Never point a feed at a local-only commit.
