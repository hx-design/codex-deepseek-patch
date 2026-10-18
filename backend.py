"""Validated update builds and the native monitor's installation adapter."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

import patch as p


def refresh_config(current, catalog, managed):
    """Replace only a known root catalog assignment, preserving user edits verbatim."""
    parsed = p.tomllib.loads(current)
    p.require(parsed.get("model_catalog_json") == managed["catalog"], "The managed model catalog was changed; review the configuration")
    p.require(parsed.get("model_providers", {}).get(p.PROVIDER) == managed["provider"],
              "The DeepSeek provider was changed; review the configuration")
    if managed.get("proxy_provider") is not None:
        import proxy_support as ps
        p.require(parsed.get("model_providers", {}).get(ps.PROVIDER) == managed["proxy_provider"],
                  "The managed proxy provider was changed; review the configuration")
    lines = current.splitlines(keepends=True)
    matches = []
    for i, line in enumerate(lines):
        if re.match(r"\s*\[", line):
            break
        if re.match(r'\s*(?:model_catalog_json|"model_catalog_json"|\'model_catalog_json\')\s*=', line):
            matches.append(i)
    p.require(len(matches) == 1, "Cannot safely identify the managed root catalog assignment")
    lines[matches[0]] = "model_catalog_json = " + json.dumps(str(catalog)) + "\n"
    result = "".join(lines)
    expected = copy.deepcopy(parsed)
    expected["model_catalog_json"] = str(catalog)
    p.require(p.tomllib.loads(result) == expected, "Unrelated configuration changed")
    return result


def managed_baseline(directory, account, service):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    installed = directory / "config-installed.toml"
    if installed.is_file():
        parsed = p.tomllib.loads(installed.read_text(encoding="utf-8"))
        catalog = parsed["model_catalog_json"]
        provider = parsed["model_providers"][p.PROVIDER]
    else:
        # Compatibility with the original local installer, before public v0.1.0.
        p.require(sys.platform == "darwin" and manifest.get("patched_asar"), "Baseline configuration record is missing")
        catalog = str(directory / "desktop-models.json")
        provider = p.tomllib.loads(p.provider_config(account, service))["model_providers"][p.PROVIDER]
    p.require(Path(catalog).is_file(), "The previous managed catalog is missing")
    result = {"catalog": catalog, "provider": provider, "baseline": str(directory)}
    if manifest.get("proxy"):
        import proxy_support as ps
        result["proxy"] = ps.validate(manifest["proxy"])
        result["proxy_provider"] = parsed["model_providers"][ps.PROVIDER]
    return result


def verify_official(app):
    p.verify_app(app)
    if sys.platform == "win32":
        from windows_support import verify_publisher
        return verify_publisher(app)
    result = subprocess.run(["/usr/bin/codesign", "-d", "--verbose=4", str(app)],
                            check=True, capture_output=True, text=True)
    p.require("TeamIdentifier=2DC432GLL2" in result.stderr.splitlines(),
              "Expected an original OpenAI-signed app; the selected source has a different signature")
    return {"team": "2DC432GLL2"}


def inspect_source(app, node=None):
    from compatibility import locate_bundle, apply_source
    p.mac_only()
    layout = p.app_layout(app)
    publisher = verify_official(layout["app"])
    source_hash = p.file_sha(layout["asar"])
    central, source, _ = locate_bundle(layout["asar"])
    patched, evidence = apply_source(source, (p.ROOT / "route.js").read_text(encoding="utf-8"))
    node = node or shutil.which("node")
    p.require(node, "Node.js is required for offline compatibility checks")
    with tempfile.TemporaryDirectory(prefix="codex-ds-contract-") as temp:
        path = Path(temp) / "contracts.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        result = p.run([node, p.ROOT / "scripts/check_compatibility.cjs", path], capture_output=True, text=True, timeout=60)
        probe = json.loads(result.stdout)
        p.require(probe.get("status") == "passed", "App-server request contract probe failed")
    p.require(p.file_sha(layout["asar"]) == source_hash, "App changed during compatibility checks")
    return {"central": central, "patched": patched, "evidence": evidence, "probe": probe,
            "publisher": publisher, "source_asar": source_hash}


def build_compatible(args):
    p.mac_only()
    app = args.app
    if app is None and sys.platform == "win32":
        from windows_support import discover_app
        app = discover_app()
    app = p.app_layout(app)["app"]
    home, output = args.codex_home.resolve(), args.output.resolve()
    p.require(not output.exists(), "Choose a new output directory; builds are never overwritten")
    p.require(not output.is_relative_to(app) and not app.is_relative_to(output) and output != home,
              "Build directory overlaps the app or configuration root")
    p.require(home.is_dir(), "Codex home must exist")
    managed = getattr(args, "managed", None)
    if getattr(args, "baseline", None):
        managed = managed_baseline(args.baseline, args.keychain_account, args.keychain_service)
    proxy = managed.get("proxy") if managed else None
    if getattr(args, "proxy_config", None):
        import proxy_support as ps
        proxy = ps.validate({"config": str(args.proxy_config.resolve()), "base_url": args.proxy_base_url,
                             "models": args.proxy_models or list(ps.MODELS)})
        ps.read_api_key(proxy["config"])
    install_target = getattr(args, "install_target", None)
    replaced_asar = None
    if install_target:
        p.require(sys.platform == "darwin" and managed, "Refreshing an installed app requires a macOS managed baseline")
        install_target = install_target.resolve()
        baseline_manifest = json.loads((Path(managed["baseline"]) / "manifest.json").read_text(encoding="utf-8"))
        p.require(install_target != app and not output.is_relative_to(install_target) and not install_target.is_relative_to(output),
                  "Install target overlaps the source or output")
        if baseline_manifest.get("target"):
            p.require(install_target == Path(baseline_manifest["target"]).resolve(), "Install target differs from the baseline")
        replaced_asar = baseline_manifest["patched_asar"]
        p.verify_app(install_target, replaced_asar)
    current = p.read_config(home / "config.toml")
    p.prepared_config(current, output / "desktop-models.json", args.keychain_account, args.keychain_service,
                      managed=managed, helper=output / "windows_support.py", proxy=proxy)
    prepared = getattr(args, "inspection", None) or inspect_source(app, getattr(args, "node_path", None))
    layout = p.app_layout(app)
    original_hash = p.file_sha(layout["asar"])
    p.require(original_hash == prepared["source_asar"], "App changed after compatibility checks")
    # A disposable CLI home avoids inheriting the catalog from the previous patch.
    with tempfile.TemporaryDirectory(prefix="codex-ds-catalog-") as temp:
        raw = subprocess.check_output([str(p.cli_path(app)), "debug", "models", "--bundled"],
                                      env={**os.environ, "CODEX_HOME": temp}, timeout=90)
    catalog = p.make_catalog(json.loads(raw))
    if proxy:
        import proxy_support as ps
        catalog = ps.add_catalog(catalog, proxy["models"])
    output.mkdir(parents=True, mode=0o700)
    patched = prepared["patched"].encode()
    source = output / "patched-client.mjs"
    source.write_bytes(patched)
    p.run([getattr(args, "node_path", None) or shutil.which("node"), "--check", source], capture_output=True, timeout=60)
    p.save_json(output / "desktop-models.json", catalog)
    if proxy:
        shutil.copy2(p.ROOT / "proxy_support.py", output / "proxy_support.py")
    p.save_json(output / "compatibility.json", {k: v for k, v in prepared.items() if k not in ("patched",)})
    if sys.platform == "win32":
        shutil.copy2(p.ROOT / "windows_support.py", output / "windows_support.py")
    original, stage = output / "original.app", output / "patched.app"
    p.clone_app(app, original)
    p.clone_app(app, stage)
    p.verify_app(original, original_hash)
    archive = p.app_layout(original)["asar"]
    stage_archive = p.app_layout(stage)["asar"]
    new_hash = p.repack(archive, output / "patched.asar", {prepared["central"]: patched})
    os.replace(output / "patched.asar", stage_archive)
    if sys.platform == "win32":
        from windows_support import update_integrity
        update_integrity(stage, new_hash)
    else:
        info = p.app_info(stage)
        old = copy.deepcopy(info["ElectronAsarIntegrity"])
        info["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = new_hash
        plist = stage / "Contents/Info.plist"
        fmt = plistlib.FMT_BINARY if plist.read_bytes().startswith(b"bplist") else plistlib.FMT_XML
        plist.write_bytes(plistlib.dumps(info, fmt=fmt, sort_keys=False))
        framework = p.framework_path(stage)
        framework.write_bytes(p.patch_framework_bytes(framework.read_bytes(), old, info["ElectronAsarIntegrity"]))
        p.run(["/usr/bin/codesign", "--deep", "--force", "--sign", "-", stage], capture_output=True)
    patched_hash = p.file_sha(stage_archive)
    p.verify_app(stage, patched_hash)
    count = p.compare_archives(archive, stage_archive, prepared["central"])
    manifest = {"format": 1, "adaptive": True, "tool_version": p.VERSION,
                "app_version": layout["version"], "app_build": layout["build"],
                "target": str(app), "codex_home": str(home), "status": "prepared",
                "original_asar": original_hash, "patched_asar": patched_hash,
                "catalog_sha256": p.file_sha(output / "desktop-models.json"),
                "keychain_account": args.keychain_account, "keychain_service": args.keychain_service,
                "changed_members": [prepared["central"]], "archive_members": count,
                "managed": managed, "contract_assertions": prepared["probe"].get("assertions"),
                "built_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if sys.platform == "win32":
        manifest["deployment_target"] = str(Path(os.environ["LOCALAPPDATA"]) / "CodexDeepSeekPatch/app")
    if proxy:
        manifest["proxy"] = proxy
    if install_target:
        manifest.update(source_target=str(app), target=str(install_target), replaced_asar=replaced_asar)
    p.save_json(output / "manifest.json", manifest)
    print("Prepared and verified:", output, flush=True)
    return manifest


def credential_available(manifest):
    if sys.platform == "win32":
        from windows_support import credential_get
        try:
            return bool(credential_get(manifest["keychain_service"]))
        except OSError:
            return False
    result = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", manifest["keychain_account"],
                             "-s", manifest["keychain_service"], "-w"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def install_compatible(directory, manifest):
    source = Path(manifest.get("source_target", manifest["target"]))
    app = Path(manifest.get("deployment_target", manifest["target"]))
    config = Path(manifest["codex_home"]) / "config.toml"
    p.require(manifest["status"] == "prepared", "Build is not prepared")
    p.require_stopped(source, read_only_source=sys.platform == "win32")
    if app != source and app.exists():
        p.require_stopped(app)
    p.verify_app(source, manifest["original_asar"])
    if manifest.get("replaced_asar"):
        p.verify_app(app, manifest["replaced_asar"])
    p.verify_app(directory / "patched.app", manifest["patched_asar"])
    p.require(p.file_sha(directory / "desktop-models.json") == manifest["catalog_sha256"], "Catalog changed")
    p.require(manifest.get("boot_probe") == "passed" and manifest.get("offline_probe") == "passed",
              "Both isolated boot and offline app-server probes must pass before installation")
    p.require(credential_available(manifest), "DeepSeek credential is unavailable; run setup-key")
    if manifest.get("proxy"):
        import proxy_support as ps
        ps.read_api_key(manifest["proxy"]["config"])
    current = p.read_config(config)
    updated = p.prepared_config(current, directory / "desktop-models.json", manifest["keychain_account"],
                                manifest["keychain_service"], managed=manifest.get("managed"),
                                helper=directory / "windows_support.py", proxy=manifest.get("proxy"))
    existed = config.exists()
    p.atomic_text(directory / "config-before.toml", current)
    p.atomic_text(directory / "config-installed.toml", updated)
    def commit():
        p.verify_app(app, manifest["patched_asar"])
        p.require(p.read_config(config) == updated, "Configuration verification failed")
        manifest.update(status="installed", config_existed=existed, installed_config_hash=p.sha(updated.encode()),
                        installed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        p.save_json(directory / "manifest.json", manifest)
    if sys.platform == "win32":
        from windows_install import install as install_windows
        install_windows(manifest, directory, config, current, updated, commit)
    else:
        p.install_transaction(app, directory / "patched.app", config, current, updated, commit)
    print("Installed verified DeepSeek patch.", flush=True)
    return manifest


def restore_windows(directory, manifest):
    from windows_install import restore
    return restore(manifest, directory)


def snapshot_app(app):
    layout = p.app_layout(app)
    files = [layout["asar"], layout["exe"], layout["cli"]]
    if sys.platform == "darwin":
        files.extend([app / "Contents/Info.plist", p.framework_path(app)])
    else:
        carrier = app / "chrome.dll"
        if carrier.is_file():
            files.append(carrier)
    hashes = {str(path.relative_to(app)): p.file_sha(path) for path in files}
    identity = {"path": str(app), "version": layout["version"], "build": layout["build"], "hashes": hashes}
    return {**identity, "fingerprint": p.sha(json.dumps(identity, sort_keys=True).encode())}


class MonitorBackend:
    def __init__(self, settings):
        self.settings = settings
        self.app = Path(settings["app"])
        self.inspection = None
        self.baseline = Path(settings["baseline"]) if settings.get("baseline") else None
        self.store = False
        if sys.platform == "win32":
            from windows_support import is_store_app
            self.store = is_store_app(self.app)

    def snapshot(self):
        if self.store:
            from windows_support import discover_app
            self.app = discover_app()
        return snapshot_app(self.app)

    def inspect(self, snapshot):
        try:
            self.inspection = inspect_source(self.app, self.settings.get("node_path"))
            return {"compatible": True, "assertions": self.inspection["probe"].get("assertions")}
        except Exception as error:
            return {"compatible": False, "reason": str(error)}

    def build(self, snapshot, output):
        p.require(self.snapshot()["fingerprint"] == snapshot["fingerprint"], "App changed before build")
        args = argparse.Namespace(app=self.app, codex_home=Path(self.settings["codex_home"]), output=output,
                                  compatible=True, bundled_catalog=True, baseline=self.baseline,
                                  keychain_account=self.settings["keychain_account"],
                                  keychain_service=self.settings["keychain_service"],
                                  node_path=self.settings.get("node_path"), inspection=self.inspection)
        build_compatible(args)
        from scripts.test_offline import main as offline
        from scripts.test_boot import main as boot
        offline(output)
        boot(output)
        return {"directory": str(output)}

    def is_running(self):
        try:
            p.require_stopped(self.app, read_only_source=sys.platform == "win32")
            if sys.platform == "win32":
                target = Path(os.environ["LOCALAPPDATA"]) / "CodexDeepSeekPatch/app"
                if target.exists():
                    p.require_stopped(target)
            return False
        except (p.PatchError, ValueError):
            return True

    def install(self, build, snapshot):
        p.require(self.snapshot()["fingerprint"] == snapshot["fingerprint"], "App changed before installation")
        directory, manifest = p.load_build(Path(build["directory"]))
        install_compatible(directory, manifest)
        self.baseline = directory
        return {"installed_fingerprint": self.snapshot()["fingerprint"], "baseline": str(directory)}

    def adopt(self):
        recovery = self.settings.get("recovery_build")
        if recovery:
            candidate = Path(recovery)
            record = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
            if record.get("status") == "installed":
                self.baseline = candidate
        if not self.baseline:
            return None
        manifest = json.loads((self.baseline / "manifest.json").read_text(encoding="utf-8"))
        app = Path(manifest.get("deployment_target", str(self.app)))
        if not app.exists():
            return None
        if p.file_sha(p.app_layout(app)["asar"]) != manifest.get("patched_asar"):
            return None
        p.verify_app(app, manifest["patched_asar"])
        if manifest.get("deployment_target"):
            if p.file_sha(p.app_layout(self.app)["asar"]) != manifest["original_asar"]:
                return None
        managed = managed_baseline(self.baseline, self.settings["keychain_account"], self.settings["keychain_service"])
        current = p.read_config(Path(self.settings["codex_home"]) / "config.toml")
        refresh_config(current, managed["catalog"], managed)
        return {"fingerprint": self.snapshot()["fingerprint"], "baseline": str(self.baseline),
                "build": {"directory": str(self.baseline)}}
