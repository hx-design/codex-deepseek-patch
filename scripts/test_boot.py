"""Boot the prepared bundle using disposable app/CLI data, then stop only that probe."""
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import patch


def main(directory):
    patch.mac_only()
    directory, manifest = patch.load_build(Path(directory))
    patch.require(manifest["status"] == "prepared", "Only prepared builds can be boot-tested")
    app = directory / "patched.app"
    patch.verify_app(app, manifest["patched_asar"])
    executable = app / "Contents/MacOS" / patch.app_info(app)["CFBundleExecutable"]
    with tempfile.TemporaryDirectory(prefix="codex-deepseek-boot-") as temp:
        base = Path(temp)
        (base / "home").mkdir()
        with (directory / "boot.log").open("w") as log:
            process = subprocess.Popen([str(executable), "--user-data-dir=" + str(base / "browser"), "--no-first-run"],
                                       env={**os.environ, "CODEX_HOME": str(base / "home")},
                                       stdout=log, stderr=log, start_new_session=True)
            try:
                for _ in range(12):
                    time.sleep(1)
                    patch.require(process.poll() is None, "Boot probe exited: " + str(process.returncode))
                processes = subprocess.check_output(["/bin/ps", "-axo", "command="], text=True)
                patch.require(any(str(base / "browser") in line and "--type=renderer" in line
                                  for line in processes.splitlines()), "No isolated renderer launched")
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    manifest["boot_probe"] = "passed"
    patch.save_json(directory / "manifest.json", manifest)
    print("PASS: isolated patched app and renderer startup. The installed app was not closed.")


if __name__ == "__main__":
    main(sys.argv[1])
