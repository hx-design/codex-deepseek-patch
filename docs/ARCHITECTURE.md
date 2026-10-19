# Official-client architecture

## Ownership

`official-client-v1` uses the vendor-signed desktop app and bundled CLI without modifying either. macOS keeps the official signing identity. Windows starts the registered Store package, preserving package identity and the vendor updater. A first migration can restore the signed original Mac backup; subsequent installs never write app bytes.

The adapter owns a scoped native provider configuration, model catalog, small Python runtime, per-user service and update state. It does not own the original authentication store, tool runtime, permissions, personal history database, external model service or its subscription login.

## Request path

```text
Official desktop and bundled CLI
  └─ Native model catalog and native OpenAI-authenticated provider
      └─ Authenticated loopback adapter (HTTP / WebSocket)
          ├─ GPT → original OpenAI destination and original auth
          ├─ Registered Responses service → its own credential
          └─ Registered Chat Completions service → protocol bridge + its own credential
```

The native provider retains OpenAI authentication semantics and declares remote compaction unsupported. This selects Codex's native readable-summary compaction. Loopback request compression is disabled so the standard-library service needs no zstd dependency. GPT network handshakes preserve routing/sticky headers; external requests never receive the OpenAI credential.

HTTP CONNECT honors the existing system proxy for external destinations, while local destinations bypass it. HTTP redirects are not followed with credentials. The listener binds numeric loopback only, uses a private token path and validates its installation identity. User-supplied LAN endpoints are explicit connections, not network discovery.

## Model capabilities and tools

Native catalog entries record each model's maximum/effective context, compaction limit, input modalities and reasoning levels. User reductions live separately from provider specifications; sync retains reductions and hidden selections. Unsupported/unknown specifications are reported instead of replaced with a 128K default.

Responses interfaces can preserve native functions. Where required, `responses_compat.py` translates namespace/custom/tool-search items to compatible functions and restores native result shapes. Tool search remains lazy, and Codex performs execution and approval. `additional_tools` declarations are promoted to the applicable current tool definitions, not inserted as chat text.

`chat_completions_compat.py` translates standard messages, images, calls/results, parallel tool chunks, streaming lifecycle and usage. Reasoning effort is forwarded only when explicitly enabled for that interface. Unsupported modalities produce a capability error. This is not an Anthropic Messages client; Claude and GLM can use an existing compatible endpoint.

Models with declared tool support retain the original CLI tool capabilities. Chat-only models omit tools. Native services continue to control whether individual tools are available in a particular session.

## Same-chat switching and compression

All registered models use the same native provider identity, so switching does not unload and reconstruct a chat for each vendor. Original chat ID, working directory and approval/sandbox settings stay in the native session. Native catalog metadata applies the selected model's context and per-turn reasoning effort.

External reasoning items receive model provenance. A continuation on the same model restores its exact provider-specific state. A different model receives readable text and tool history, without another model's signature or incompatible reasoning-item fields. This rule uses model identity and protocol shapes, not a hardcoded list of vendors.

Older external signatures are identified by a bounded, read-only rollout scan: `turn_context.model` associates known external models with SHA-256 signature hashes. The private index stores hash → model, never ciphertext or chat content. Ambiguous origins are excluded.

Old GPT compaction blocks are handled lazily without calling GPT. The native request identifies its thread. A read-only standard-library loader verifies that chat's JSONL identity, ordinal continuity, exact compaction item and any bounded shared-history parent prefixes. It reads visible messages and tool results only; opaque reasoning and media bytes are excluded. No database writes, whole-library content index, decryption, or new resident service is involved.

The selected route summarizes the verified text through its own Responses or Chat Completions endpoint. Recovery uses the declared effective model context, bounded UTF-8 chunks and hierarchical reduction; it does not alter the native context window. Summary requests have no tools. An account/thread/model/endpoint/window-scoped private cache avoids repeat calls, and old official handoff caches are read locally and reduced if the chosen window is smaller.

Missing prefixes, ambiguous identity, invalid ordinal/byte boundaries, malformed records, unresolved rollback records and unsupported storage formats return an explicit recovery error before normal inference continues. The current reader supports uncompressed JSONL and native paginated fork prefixes. Oversized, nonreducing or interrupted summaries are not cached as complete. New native readable-summary compaction continues unchanged.


## Installation and recovery

Staging creates only runtime/config artifacts and immutable digests. Validation checks that staged bytes match the source tested by the background native CLI suite. Installation checks the official publisher, unchanged source, runtime/profile/catalog digests and the exact service command/health identity.

Under a state lock, activation re-reads current settings and changes only owned fields. Existing chats migrate through native metadata RPC, preserving model, effort, archive state, content and recency. No SQL writes or history copies occur. Archived chats use explicit unarchive/resume/rearchive phases; recovery records the actual phase because unarchive is not idempotent.

A durable activation journal covers app restoration, scoped configuration, hash index, metadata migration and service state. Each restoration is attempted independently so a service failure cannot skip app/config recovery. A failed or interrupted activation can recover and retry. Restore follows the ownership chain to the pre-adapter configuration, migrates newer adapter chats to the earlier default provider, and retains the official app.

Services use launchd on macOS and least-privilege Task Scheduler with pythonw on Windows. Windows task XML has matching UTF-16 declaration/encoding; numeric COM states distinguish Ready, Disabled and Running. Existing task state is restored precisely.

## Updates and source distribution

DS `main` and extension `codex/existing-proxy-models` have separate versions, increasing feed sequences and immutable source commits. Downloads validate channel, inventory, hashes and isolated tests before activation. A native install rejects a legacy-architecture downgrade.

The monitor is scheduled and exits after each check. It compares app/runtime/model identities and waits for normal app exit. It does not send inference requests. The adapter is a separate small per-user process, with no database, model engine or management UI.

The repository retains legacy source and tests for recovery and historical compatibility. New builds do not edit ASAR, sign binaries, copy a Windows app or use legacy GUI boot probes. Only source/documentation is published; catalogs containing vendor prompts, app bundles, user credentials and private test evidence remain local.

## Non-blocking update progress

The monitor publishes a small private progress document containing fixed display fields and measured work counts. Unknown-duration phases use an indeterminate bar. Native AppKit/WinForms viewers issue only fixed start/launch/dismiss actions bound to the current update ID; state JSON never supplies a command to execute.

Dismissing the window does not cancel the worker. Starting an update records the user's request; Codex is closed only after preparation and validation. Completion offers a launch button and does not reopen Codex automatically. The Mac panel uses [AppKit's non-activating panel behavior](https://developer.apple.com/documentation/appkit/nspanel/becomeskeyonlyifneeded) to display progress without taking keyboard focus.
