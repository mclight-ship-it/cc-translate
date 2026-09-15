"""Explicit CI-only official CLI --version smoke; never login or invoke a model."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


ASSETS = (
    ("0.146.0", "2750132d300e64f1dbffb95e3d913fd9c9dc7812bc8e1bce5c61357248b7929e"),
    ("0.154.0", "344310a0a591c1b192e04feff304321a69907c9498baaac331ca7e16ebcef9d7"),
)
BINARY_NAME = "codex-aarch64-apple-darwin"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_BINARY_BYTES = 512 * 1024 * 1024
PROBE = r"""
import json, os, sys
from pathlib import Path
core, command, home, expected = sys.argv[1:]
sys.path.insert(0, core)
from cc_providers import codex_catalog, darwin_process
for module in (codex_catalog, darwin_process):
    if Path(module.__file__).resolve().parent != Path(core) / "cc_providers":
        raise RuntimeError("version_smoke_source_mismatch")
environment = {"HOME": home, "CODEX_HOME": str(Path(home) / ".codex"),
               "TMPDIR": home, "PATH": "/usr/bin:/bin"}
output = darwin_process.capture_output(
    [command, "--version"], environment, home,
    cancel_event=None, timeout=5, max_bytes=8192)
version = codex_catalog.parse_codex_version(output)
if version is None or version.text != expected or not version.supported:
    raise RuntimeError("official_version_smoke_failed")
print(json.dumps({"version": version.text, "meets_minimum": version.supported}))
"""


def unpack_binary(archive, destination):
    with tarfile.open(archive, "r:gz") as bundle:
        members = [member for member in bundle.getmembers()
                   if member.name in (BINARY_NAME, "./" + BINARY_NAME)]
        if (len(members) != 1 or not members[0].isfile()
                or not 0 < members[0].size <= MAX_BINARY_BYTES):
            raise ValueError("invalid_official_cli_archive")
        with bundle.extractfile(members[0]) as source, destination.open("xb") as target:
            shutil.copyfileobj(source, target)
    destination.chmod(0o700)


def download(version, expected_hash, destination):
    url = ("https://github.com/openai/codex/releases/download/rust-v" + version
           + "/" + BINARY_NAME + ".tar.gz")
    digest = hashlib.sha256()
    total = 0
    request = urllib.request.Request(url, headers={"User-Agent": "cc-translate-version-smoke"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("xb") as target:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("official_cli_archive_too_large")
            digest.update(chunk)
            target.write(chunk)
    if digest.hexdigest() != expected_hash:
        raise ValueError("official_cli_archive_hash_mismatch")


def verify(app):
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("official_cli_smoke_requires_darwin_arm64")
    app = Path(app).resolve(strict=True)
    python = app / "Contents" / "Helpers" / "python" / "bin" / "python3"
    core = app / "Contents" / "Resources" / "Core"
    if not python.is_file() or not core.is_dir():
        raise ValueError("explicit_bundled_runtime_required")
    results = []
    with tempfile.TemporaryDirectory(prefix="cc-official-version-") as temporary:
        root = Path(temporary)
        for version, digest in ASSETS:
            archive, binary = root / (version + ".tar.gz"), root / ("codex-" + version)
            home = root / ("home-" + version)
            home.mkdir()
            download(version, digest, archive)
            unpack_binary(archive, binary)
            environment = {"HOME": str(home), "PATH": "/usr/bin:/bin", "TMPDIR": str(home)}
            completed = subprocess.run(
                [str(python), "-I", "-B", "-c", PROBE, str(core), str(binary), str(home), version],
                env=environment, cwd=home, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            if completed.returncode or completed.stderr:
                raise ValueError("official_version_smoke_process_failed")
            result = json.loads(completed.stdout)
            if result != {"version": version, "meets_minimum": True}:
                raise ValueError("official_version_smoke_report_invalid")
            results.append(dict(result, archive_sha256=digest))
    return {"status": "passed", "operation": "--version",
            "account_or_model_called": False, "temporary_files_removed": True,
            "versions": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = verify(args.app)
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
