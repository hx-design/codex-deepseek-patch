"""Check tracked release contents for accidentally included local or vendor data."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    ".gitignore", ".github/workflows/tests.yml", "LICENSE", "NOTICE", "README.md", "README.en.md",
    "CONTRIBUTING.md", "patch.py", "route.js", "docs/ARCHITECTURE.md", "docs/VALIDATION.md",
    "scripts/__init__.py", "scripts/test_boot.py", "scripts/test_connection.py", "scripts/check_release.py",
    "tests/test_patch.py", "tests/route.test.cjs",
    "backend.py", "compatibility.py", "monitor.py", "windows_support.py", "windows_install.py",
    "scripts/test_offline.py", "scripts/check_compatibility.cjs",
    "tests/test_backend.py", "tests/test_compatibility.py", "tests/test_monitor.py",
    "tests/test_offline.py", "tests/test_windows.py", "tests/test_windows_install.py",
    "proxy_support.py", "tests/test_proxy.py",
}
files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
files = [name for name in files if name]
if set(files) != ALLOWED:
    raise SystemExit("Release file allowlist mismatch: " + repr(set(files) ^ ALLOWED))
patterns = [re.compile("/" + "Users" + r"/[^\s/]+/"), re.compile(r"gh[pousr]_[A-Za-z0-9]{25,}"),
            re.compile(r"sk-[A-Za-z0-9_-]{20,}"), re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")]
total = 0
for name in files:
    path = ROOT / name
    if path.is_symlink():
        raise SystemExit("Release cannot contain symlinks: " + name)
    data = path.read_bytes(); total += len(data)
    if len(data) > 100_000:
        raise SystemExit("Unexpected large release file: " + name)
    text = data.decode("utf-8")
    if any(pattern.search(text) for pattern in patterns):
        raise SystemExit("Possible private data in: " + name)
print(f"PASS: {len(files)} allowlisted source/documentation files, {total:,} bytes; no matched local paths or credential patterns")
