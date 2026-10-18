#!/usr/bin/env python3
"""Source-only DeepSeek model picker patch for macOS and Windows Codex."""
from __future__ import annotations

import argparse
import copy
import ctypes
import datetime as dt
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib

VERSION = "0.3.0-proxy.1"
EDITION = "proxy"
APP_VERSION = "26.1013.40934"
APP_BUILD = "20770"
ORIGINAL_ASAR = "5ddc4e48259aa6f979e04529b1a5a4ae54b0f5c4ad6db5c6e004924c10916ddb"
CENTRAL = "webview/assets/app-initial-90eb42395b7d.js"
MARKER = "CodexDesktopDeepSeekFlashPatch"
SENTINEL = b"AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A"
PROVIDER = "deepseek-desktop"
MODEL = "deepseek-flash"
KEYCHAIN_SERVICE = "Codex DeepSeek API Key"
ROOT = Path(__file__).resolve().parent
SERVICE_RUNTIME_FILES = ("patch.py", "backend.py", "monitor.py", "compatibility.py", "route.js", "proxy_support.py",
                         "model_sync.py", "updater.py", "update_worker.py", "edition.json",
                         "windows_support.py", "windows_install.py", "scripts/__init__.py",
                         "scripts/test_boot.py", "scripts/test_offline.py", "scripts/test_context.py", "scripts/check_compatibility.cjs")

# Short, exact interoperability anchors; no vendor bundle is distributed.
EDITS = (
    ("return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,t,e===`plugin/list`",
     "t=codexPatchAppServerParams(e,t,this.hostId);return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,t,e===`plugin/list`"),
    ("let n=t?.priority??`critical`,r=u1t(`thread/start`,t?.source)",
     "e=codexPatchAppServerParams(`thread/start`,e,this.hostId);let n=t?.priority??`critical`,r=u1t(`thread/start`,t?.source)"),
    ("f.canUsePrewarmedThread&&this.params.prewarmedThreadManager.hasPrewarmedThread(",
     "this.params.hostId!==`local`&&f.canUsePrewarmedThread&&this.params.prewarmedThreadManager.hasPrewarmedThread("),
)


class PatchError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise PatchError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def mac_only():
    require((sys.platform == "darwin" and platform.machine() == "arm64") or sys.platform == "win32",
            "This release supports Apple Silicon macOS and Windows.")


def app_layout(app):
    app = Path(app).resolve()
    if sys.platform == "win32":
        from windows_support import inspect_app
        return inspect_app(app)
    info = app_info(app)
    return {"app": app, "asar": app / "Contents/Resources/app.asar",
            "exe": app / "Contents/MacOS" / info["CFBundleExecutable"],
            "cli": app / "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
            "version": info["CFBundleShortVersionString"], "build": info["CFBundleVersion"]}


def app_info(app):
    return plistlib.loads((app / "Contents/Info.plist").read_bytes())


def archive_header(path):
    with Path(path).open("rb") as handle:
        prefix = handle.read(16)
        require(len(prefix) == 16, "Truncated ASAR prefix")
        magic, size, payload, length = struct.unpack("<4I", prefix)
        require(magic == 4 and 8 <= size <= 64 * 1024 * 1024,
                "Invalid ASAR header size")
        require(payload == size - 4 and length <= size - 8,
                "Invalid ASAR header pickle")
        raw = handle.read(length)
        require(len(raw) == length and 8 + size <= Path(path).stat().st_size,
                "Truncated ASAR header")
    return json.loads(raw), 8 + size, sha(raw)


def entries(header, prefix=""):
    for name, item in header.get("files", {}).items():
        require(name not in ("", ".", "..") and "/" not in name and "\\" not in name,
                "Unsafe ASAR member name")
        path = prefix + name
        if "files" in item:
            yield from entries(item, path + "/")
        else:
            yield path, item


def read_member(path, item, base):
    require("offset" in item and not item.get("unpacked"), "Expected a packed member")
    offset, size = int(item["offset"]), item["size"]
    require(offset >= 0 and size >= 0 and base + offset + size <= Path(path).stat().st_size,
            "ASAR member is outside the archive")
    with Path(path).open("rb") as handle:
        handle.seek(base + offset)
        return handle.read(size)


def check_member(data, item):
    integrity = item.get("integrity", {})
    block = integrity.get("blockSize", 0)
    require(integrity.get("algorithm") == "SHA256" and isinstance(block, int) and block > 0,
            "Unsupported member integrity metadata")
    require(sha(data) == integrity.get("hash"), "Member hash mismatch")
    require([sha(data[i:i + block]) for i in range(0, len(data), block)] == integrity.get("blocks"),
            "Member block hash mismatch")


def patch_source(source):
    require(MARKER not in source, "Patch is already present; use an original app copy")
    result = source
    for old, new in EDITS:
        require(result.count(old) == 1, "Missing or ambiguous version anchor: " + old[:70])
        result = result.replace(old, new, 1)
    return (ROOT / "route.js").read_text() + "\n" + result


def repack(source, destination, replacements):
    """Keep the complete original data area, including shared ranges; append edits."""
    source, destination = Path(source), Path(destination)
    require(source.resolve() != destination.resolve(), "ASAR output must be a separate file")
    header, base, _ = archive_header(source)
    files = dict(entries(header))
    require(replacements.keys() <= files.keys(), "Replacement member missing")
    offset = source.stat().st_size - base
    for path, data in replacements.items():
        item = files[path]
        check_member(read_member(source, item, base), item)
        block = item["integrity"]["blockSize"]
        item["size"] = len(data)
        item["offset"] = str(offset)
        item["integrity"] = {**item["integrity"], "hash": sha(data),
                             "blocks": [sha(data[i:i + block]) for i in range(0, len(data), block)]}
        offset += len(data)
    raw = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode()
    payload = struct.pack("<I", len(raw)) + raw + b"\0" * (-(4 + len(raw)) % 4)
    header_pickle = struct.pack("<I", len(payload)) + payload
    with source.open("rb") as src, destination.open("wb") as dst:
        dst.write(struct.pack("<II", 4, len(header_pickle)))
        dst.write(header_pickle)
        src.seek(base)
        shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
        for data in replacements.values():
            dst.write(data)
    return sha(raw)


def compare_archives(original, patched, central=CENTRAL):
    oh, ob, _ = archive_header(original)
    ph, pb, _ = archive_header(patched)
    a, b = dict(entries(oh)), dict(entries(ph))
    require(a.keys() == b.keys(), "Archive member inventory changed")
    changed = []
    for path, item in a.items():
        other = b[path]
        if "offset" not in item or item.get("unpacked"):
            require(item == other, "Link/unpacked metadata changed: " + path)
            continue
        if path != central:
            require(item == other, "Unrelated member metadata changed: " + path)
        old = read_member(original, item, ob)
        new = read_member(patched, other, pb)
        if old != new:
            changed.append(path)
            check_member(new, other)
    require(changed == [central], "Unexpected changed members: " + repr(changed))
    return len(a)


def dictionary_digest(integrity):
    text = "".join(key + integrity[key]["algorithm"] + integrity[key]["hash"]
                   for key in sorted(integrity))
    return hashlib.sha256(text.encode()).digest()


def framework_path(app):
    base = app / "Contents/Frameworks/Codex Framework.framework/Versions"
    candidates = {p.resolve() for p in base.glob("*/Codex Framework")}
    require(len(candidates) == 1, "Cannot identify one Codex Framework")
    result = candidates.pop()
    require(result.is_relative_to(app.resolve()), "Framework points outside the app")
    return result


def patch_framework_bytes(blob, old_integrity, new_integrity):
    require(blob.count(SENTINEL) == 1, "Missing or ambiguous framework integrity slot")
    slot = blob.index(SENTINEL)
    require(blob[slot + 32:slot + 34] == b"\x01\x01", "Unknown framework integrity slot version")
    require(blob[slot + 34:slot + 66] == dictionary_digest(old_integrity),
            "Original framework integrity digest does not match Info.plist")
    return blob[:slot + 34] + dictionary_digest(new_integrity) + blob[slot + 66:]


def verify_app(app, expected_asar=None):
    if sys.platform == "win32":
        from windows_support import verify_integrity
        return verify_integrity(app, expected_asar)
    info = app_info(app)
    require(info.get("CFBundleIdentifier") == "com.openai.codex", "Wrong application identity")
    archive = app / "Contents/Resources/app.asar"
    if expected_asar:
        require(file_sha(archive) == expected_asar, "App archive changed")
    integrity = info["ElectronAsarIntegrity"]
    require(archive_header(archive)[2] == integrity["Resources/app.asar"]["hash"],
            "Info.plist ASAR header hash mismatch")
    blob = framework_path(app).read_bytes()
    patch_framework_bytes(blob, integrity, integrity)
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app])


def catalog_template(catalog):
    models=[m for m in catalog['models'] if m.get('slug','').startswith('gpt-') and m.get('visibility','list')=='list']
    require(models,'The native catalog has no usable GPT metadata template')
    return next((m for m in models if m['slug']=='gpt-6.1-sol'),models[0])


def make_catalog(original):
    catalog = copy.deepcopy(original)
    flash = copy.deepcopy(catalog_template(catalog))
    # Prompt text remains local: inherit it from the user's installed catalog.
    flash.update(
        slug=MODEL, display_name="DeepSeek 4.1 Flash", description="DeepSeek 4.1 Flash · DeepSeek API",
        default_reasoning_level="high", supported_reasoning_levels=[
            {"effort": "low", "description": "Fast responses with lighter reasoning"},
            {"effort": "high", "description": "Greater reasoning depth"},
            {"effort": "max", "description": "Maximum reasoning depth"}],
        shell_type="shell_command", visibility="list", supported_in_api=True, priority=90,
        additional_speed_tiers=[], service_tiers=[], default_service_tier=None,
        available_access_programs=None, availability_nux=None, upgrade=None,
        context_window=1048576, max_context_window=1048576, effective_context_window_percent=100,
        prefer_websockets=False, use_responses_lite=False, tool_mode=None,
        experimental_supported_tools=[], multi_agent_reasoning_effort="high",
        web_search_tool_type="text", supports_search_tool=True,
        input_modalities=["text", "image"], supports_image_detail_original=True,
    )
    catalog["models"] = [m for m in catalog["models"] if not m["slug"].startswith("deepseek-")] + [flash]
    return catalog


def provider_config(account, service, helper=None):
    args = ["find-generic-password", "-a", account, "-s", service, "-w"]
    command = "/usr/bin/security"
    if sys.platform == "win32":
        command = sys.executable
        if Path(command).name.lower() == "pythonw.exe":
            console = Path(command).with_name("python.exe")
            require(console.is_file(), "A console Python executable is required for the credential helper")
            command = str(console)
        args = [str(helper or (ROOT / "windows_support.py")), "credential-get", "--target", service]
    return (f'\n[model_providers.{PROVIDER}]\nname = "DeepSeek"\n'
            'base_url = "https://api.deepseek.com/"\nwire_api = "responses"\n'
            f'requires_openai_auth = false\n\n[model_providers.{PROVIDER}.auth]\n'
            'command = ' + json.dumps(command) + '\nargs = ' + json.dumps(args) + '\nrefresh_interval_ms = 0\n')


def prepared_config(current, catalog, account, service, managed=None, helper=None, proxy=None):
    parsed = tomllib.loads(current)
    if managed:
        from backend import refresh_config
        updated = refresh_config(current, catalog, managed)
    else:
        require(parsed.get("model_provider", "openai") == "openai", "A different default provider is configured")
        require("model_catalog_json" not in parsed and PROVIDER not in parsed.get("model_providers", {}),
                "A custom catalog or desktop DeepSeek provider already exists; review it before installing")
        updated = ('model_catalog_json = ' + json.dumps(str(catalog)) + '\n' + current
                   + ("" if current.endswith("\n") else "\n") + provider_config(account, service, helper))
    expected = copy.deepcopy(parsed)
    expected["model_catalog_json"] = str(catalog)
    if not managed:
        expected.setdefault("model_providers", {})[PROVIDER] = tomllib.loads(provider_config(account, service, helper))["model_providers"][PROVIDER]
    if proxy:
        import proxy_support as ps
        existing = parsed.get("model_providers", {}).get(ps.PROVIDER)
        if existing is not None:
            require(managed and managed.get("proxy_provider") == existing,
                    "An unmanaged proxy provider exists; review the configuration")
            # A baseline keeps the helper in its retained build directory.
            require({k:v for k,v in managed.get("proxy",{}).items() if k!='models'} == {k:v for k,v in proxy.items() if k!='models'},
                    "The managed proxy connection changed; review before migrating")
        else:
            addition = ps.provider_config(proxy, Path(catalog).parent / "proxy_support.py")
            updated += ("" if updated.endswith("\n") else "\n") + addition
            expected.setdefault("model_providers", {})[ps.PROVIDER] = tomllib.loads(addition)["model_providers"][ps.PROVIDER]
    require(tomllib.loads(updated) == expected, "Unrelated configuration changed")
    return updated


def atomic_text(path, text):
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_json(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def clone_app(source, target):
    if sys.platform == "win32":
        from windows_support import clone_app as clone
        return clone(source, target)
    require(not target.exists(), "Refusing to overwrite " + str(target))
    # APFS clones avoid duplicating gigabytes. Fail rather than partially overwrite.
    run(["/bin/cp", "-cR", source, target])


def exchange(a, b):
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    swap = libc.renamex_np
    swap.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    swap.restype = ctypes.c_int
    if swap(os.fsencode(a), os.fsencode(b), 2):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def require_stopped(app, *, read_only_source=False):
    if sys.platform == "win32":
        from windows_support import require_stopped as stopped
        return stopped(app, read_only_source=read_only_source)
    commands = subprocess.check_output(["/bin/ps", "-axo", "command="], text=True).splitlines()
    roots = {str(app), str(app.resolve())}
    require(not any(c.strip().startswith(root + "/Contents/") for c in commands for root in roots),
            "Quit the target app normally, then run this command from Terminal. No process was stopped.")


def read_config(path):
    require(not path.is_symlink(), "Config symlink detected; resolve it explicitly before installation")
    return path.read_text(encoding="utf-8") if path.exists() else ""


def cli_path(app):
    path = app_layout(app)["cli"]
    require(path.is_file(), "Bundled Codex CLI is missing")
    return path


def build(args):
    require(not (getattr(args, "proxy_config", None) or getattr(args, "install_target", None)) or getattr(args, "compatible", False),
            "Proxy integration and installed-app refresh require --compatible")
    if getattr(args, "compatible", False) or sys.platform == "win32":
        from backend import build_compatible
        return build_compatible(args)
    mac_only()
    app, home, output = args.app.resolve(), args.codex_home.resolve(), args.output.resolve()
    require(not output.exists(), "Choose a new output directory; existing builds are never overwritten")
    require(not output.is_relative_to(app) and not app.is_relative_to(output) and output != home,
            "Build directory overlaps the app or configuration root")
    require(home.is_dir(), "Codex home must exist")
    config = home / "config.toml"
    current = read_config(config)
    prepared_config(current, output / "desktop-models.json", args.keychain_account, args.keychain_service)
    info = app_info(app)
    require(info.get("CFBundleShortVersionString") == APP_VERSION and info.get("CFBundleVersion") == APP_BUILD,
            "Unsupported app version/build; supported: " + APP_VERSION + " (" + APP_BUILD + ")")
    verify_app(app, ORIGINAL_ASAR)
    require(shutil.which("node"), "Node.js is required for JavaScript syntax validation")
    archive = app / "Contents/Resources/app.asar"
    header, base, _ = archive_header(archive)
    member = dict(entries(header)).get(CENTRAL)
    require(member is not None, "Supported JavaScript bundle is missing")
    source = read_member(archive, member, base)
    check_member(source, member)
    patched = patch_source(source.decode()).encode()
    command = [cli_path(app), "debug", "models"] + (["--bundled"] if args.bundled_catalog else [])
    catalog = make_catalog(json.loads(subprocess.check_output(command, env={**os.environ, "CODEX_HOME": str(home)}, timeout=90)))
    output.mkdir(parents=True, mode=0o700)
    os.chmod(output, 0o700)
    temp_source = output / "patched-client.mjs"
    temp_source.write_bytes(patched)
    run(["node", "--check", temp_source])
    save_json(output / "desktop-models.json", catalog)
    original, stage = output / "original.app", output / "patched.app"
    clone_app(app, original)
    clone_app(app, stage)
    new_hash = repack(archive, output / "patched.asar", {CENTRAL: patched})
    os.replace(output / "patched.asar", stage / "Contents/Resources/app.asar")
    old_integrity = copy.deepcopy(info["ElectronAsarIntegrity"])
    info["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = new_hash
    fmt = plistlib.FMT_BINARY if (app / "Contents/Info.plist").read_bytes().startswith(b"bplist") else plistlib.FMT_XML
    (stage / "Contents/Info.plist").write_bytes(plistlib.dumps(info, fmt=fmt, sort_keys=False))
    framework = framework_path(stage)
    framework.write_bytes(patch_framework_bytes(framework.read_bytes(), old_integrity, info["ElectronAsarIntegrity"]))
    run(["/usr/bin/codesign", "--deep", "--force", "--sign", "-", stage])
    patched_hash = file_sha(stage / "Contents/Resources/app.asar")
    verify_app(stage, patched_hash)
    count = compare_archives(original / "Contents/Resources/app.asar", stage / "Contents/Resources/app.asar")
    manifest = {"format": 1, "tool_version": VERSION, "app_version": APP_VERSION, "app_build": APP_BUILD,
                "target": str(app), "codex_home": str(home), "status": "prepared",
                "original_asar": ORIGINAL_ASAR, "patched_asar": patched_hash,
                "catalog_sha256": file_sha(output / "desktop-models.json"),
                "keychain_account": args.keychain_account, "keychain_service": args.keychain_service,
                "changed_members": [CENTRAL], "archive_members": count,
                "built_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    save_json(output / "manifest.json", manifest)
    print(f"Prepared and verified: {output}\nRun test-boot, then quit the app and run install.")


def load_build(directory):
    directory = directory.resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("format") == 1, "Unknown manifest format")
    return directory, manifest


def install_transaction(app, staged, config, before, after, commit):
    """Exchange both directories atomically; roll back on any following error."""
    exists = config.exists()
    require(read_config(config) == before, "Configuration changed during preflight")
    exchange(app, staged)
    try:
        atomic_text(config, after)
        commit()
    except BaseException:
        exchange(app, staged)
        if exists:
            atomic_text(config, before)
        elif config.exists():
            config.unlink()
        raise


def install(args):
    mac_only()
    directory, manifest = load_build(args.directory)
    if manifest.get("adaptive"):
        from backend import install_compatible
        return install_compatible(directory, manifest)
    app = Path(manifest["target"])
    config = Path(manifest["codex_home"]) / "config.toml"
    require(manifest["status"] == "prepared", "Build is not in prepared state; inspect status first")
    require_stopped(app)
    verify_app(app, manifest["original_asar"])
    verify_app(directory / "patched.app", manifest["patched_asar"])
    require(file_sha(directory / "desktop-models.json") == manifest["catalog_sha256"], "Catalog changed")
    require(manifest.get("boot_probe") == "passed", "Run test-boot on this build before installing")
    result = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", manifest["keychain_account"],
                             "-s", manifest["keychain_service"], "-w"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    require(result.returncode == 0, "DeepSeek Keychain entry is unavailable; run setup-key first")
    current = read_config(config)
    updated = prepared_config(current, directory / "desktop-models.json", manifest["keychain_account"], manifest["keychain_service"])
    existed = config.exists()
    atomic_text(directory / "config-before.toml", current)
    atomic_text(directory / "config-installed.toml", updated)
    def commit():
        verify_app(app, manifest["patched_asar"])
        require(read_config(config) == updated, "Configuration verification failed")
        manifest.update(status="installed", config_existed=existed, installed_config_hash=sha(updated.encode()),
                        installed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        save_json(directory / "manifest.json", manifest)
    install_transaction(app, directory / "patched.app", config, current, updated, commit)
    print("Installed. Open the app, create a new local chat, and select DeepSeek 4.1 Flash.")


def restore(args):
    mac_only()
    directory, manifest = load_build(args.directory)
    if manifest.get("deployment_target"):
        from backend import restore_windows
        return restore_windows(directory, manifest)
    require(manifest["status"] == "installed", "Build is not installed")
    app, config = Path(manifest["target"]), Path(manifest["codex_home"]) / "config.toml"
    require_stopped(app)
    current = read_config(config)
    require(sha(current.encode()) == manifest["installed_config_hash"],
            "Configuration changed after installation. Review config-before.toml and the current file before restoring.")
    verify_app(app, manifest["patched_asar"])
    # After installation, patched.app contains the exact original target.
    previous_asar = manifest.get("replaced_asar", manifest["original_asar"])
    verify_app(directory / "patched.app", previous_asar)
    before = (directory / "config-before.toml").read_text(encoding="utf-8")
    def commit():
        verify_app(app, previous_asar)
        if not manifest["config_existed"]:
            config.unlink()
        manifest["status"] = "restored"
        save_json(directory / "manifest.json", manifest)
    install_transaction(app, directory / "patched.app", config, current, before, commit)
    print("Restored the previous app and pre-install configuration. Open the app normally.")


def setup_key(args):
    mac_only()
    if sys.platform == "win32":
        from windows_support import credential_set
        credential_set(args.keychain_service, args.keychain_account, getpass.getpass("DeepSeek API key: "))
        print("Saved in Windows Credential Manager.")
        return
    print("Enter the DeepSeek API key at the macOS security tool's password prompt.")
    # No key is placed in the process arguments, a repository file or shell history.
    run(["/usr/bin/security", "add-generic-password", "-U", "-a", args.keychain_account,
         "-s", args.keychain_service, "-w"])


def status(args):
    directory, manifest = load_build(args.directory)
    print(json.dumps({key: manifest.get(key) for key in ("tool_version", "app_version", "status", "boot_probe", "target")}, indent=2))
    if sys.platform in ("darwin", "win32"):
        expected = (manifest["patched_asar"] if manifest["status"] == "installed" else
                    manifest.get("replaced_asar", manifest["original_asar"]))
        target = manifest.get("deployment_target", manifest["target"]) if manifest["status"] == "installed" else manifest["target"]
        verify_app(Path(target), expected)
        print("Target signature and resource integrity verified.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    subs = parser.add_subparsers(dest="action", required=True)
    p = subs.add_parser("build", help="Build a patched copy without changing the installed app")
    default_app = Path("/Applications/ChatGPT.app")
    if not default_app.exists():
        default_app = Path("/Applications/Codex.app")
    if sys.platform == "win32":
        default_app = None
    p.add_argument("--app", type=Path, default=default_app)
    p.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--bundled-catalog", action="store_true", help="Use the offline bundled GPT catalog instead of refreshing")
    p.add_argument("--compatible", action="store_true", help="Check signed source and supported request contracts across app updates")
    p.add_argument("--baseline", type=Path, help="Existing patch build whose managed catalog should be refreshed")
    p.add_argument("--install-target", type=Path, help="Refresh a known installed Mac baseline using an original signed --app backup")
    p.add_argument("--proxy-config", type=Path, help="Read the client API key from an existing EasyCLIProxyAPI configuration")
    p.add_argument("--proxy-base-url", default="http://127.0.0.1:8317/v1")
    p.add_argument("--proxy-models", nargs="+", choices=__import__("proxy_support").MODELS,
                   help="Explicit model IDs to expose through the existing proxy")
    p.add_argument("--model-snapshot", type=Path, help="Validated discovery snapshot for reproducible automatic updates")
    p.add_argument("--keychain-account", default=getpass.getuser())
    p.add_argument("--keychain-service", default=KEYCHAIN_SERVICE)
    p.set_defaults(func=build)
    for name, function in (("install", install), ("restore", restore), ("status", status)):
        p = subs.add_parser(name)
        p.add_argument("directory", type=Path)
        p.set_defaults(func=function)
    p = subs.add_parser("setup-key")
    p.add_argument("--keychain-account", default=getpass.getuser())
    p.add_argument("--keychain-service", default=KEYCHAIN_SERVICE)
    p.set_defaults(func=setup_key)
    p = subs.add_parser("test-boot", help="Launch only the prepared copy with temporary user data")
    p.add_argument("directory", type=Path)
    p.set_defaults(func=lambda args: __import__("scripts.test_boot", fromlist=["main"]).main(args.directory))
    args = parser.parse_args()
    try:
        args.func(args)
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("Error:", error, file=sys.stderr)
        return 1
    return 0


def MonitorBackend(settings):
    from backend import MonitorBackend as Backend
    return Backend(settings)


if __name__ == "__main__":
    raise SystemExit(main())
