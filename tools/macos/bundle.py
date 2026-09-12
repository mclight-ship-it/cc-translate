"""Build/audit a local, unsigned macOS P0 bundle. No signing or release actions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STAGING = HERE / ".staging"
BUILD = HERE / ".build"
APP = BUILD / "CCTranslateMac-P0.app"
LOCK = HERE / "runtime-lock.json"
SHARED_CORE_MODULES = ("cc_classify.py", "cc_direction.py", "cc_prompts.py", "cc_dictionary_store.py")
PROVIDER_CONTRACT_FILES = ("__init__.py", "base.py", "registry.py")
PROVIDER_CONFIG_FILES = ("codex_config.py", "codex_config_darwin.py", "codex_instructions.txt")
PROVIDER_CORE_FILES = PROVIDER_CONTRACT_FILES + PROVIDER_CONFIG_FILES
XCODE = Path("/Applications/Xcode_16.4.app/Contents/Developer")
MAX_MEMBERS = 30000
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MACHO_MAGIC = {
    b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}


class BundleError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise BundleError(message)


def digest(path):
    with Path(path).open("rb") as handle:
        return stream_digest(handle)


def stream_digest(handle):
    result = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        result.update(chunk)
    return result.hexdigest()


def load_lock():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    need(lock["schema"] == 1, "unsupported lock schema")
    for asset in lock["assets"].values():
        archive_path(asset["filename"])
        need("/" not in asset["filename"], "cache filename must be a basename")
        need(urllib.parse.urlsplit(asset["url"]).scheme == "https", "non-HTTPS asset")
        need(re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]), "unpinned asset hash")
        need(isinstance(asset["size"], int) and asset["size"] > 0, "asset size missing")
    return lock


def archive_path(name):
    """Use one conservative POSIX namespace even on case-insensitive Windows/macOS."""
    need(isinstance(name, str) and name, "empty archive path")
    need(not any(ord(c) < 32 or ord(c) == 127 for c in name), "control in archive path")
    need("\\" not in name and ":" not in name, "non-POSIX archive path")
    need(not name.startswith("/"), "absolute archive path")
    parts = name.rstrip("/").split("/")
    need(all(p not in ("", ".", "..") for p in parts), "ambiguous/traversing archive path")
    need(all(not p.endswith((".", " ")) for p in parts), "aliased archive path")
    need(all(re.fullmatch(r"[A-Za-z0-9_.+@ -]+", p) for p in parts),
         "unsupported archive filename")
    need(all(p.split(".")[0].upper() not in
             {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
              *(f"LPT{i}" for i in range(1, 10))} for p in parts), "reserved filename")
    return PurePosixPath(*parts)


def link_destination(name, target, hard=False):
    need(target and not target.startswith("/") and "\\" not in target and ":" not in target,
         "absolute or invalid archive link")
    parts = [] if hard else list(archive_path(name).parent.parts)
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            need(parts, "archive link escapes root")
            parts.pop()
        else:
            archive_path(part)
            parts.append(part)
    need(parts and parts[0] == archive_path(name).parts[0], "archive link escapes package")
    return PurePosixPath(*parts)


def validate_members(members):
    """Reject aliases, special files and linked ancestors before writing any payload."""
    need(len(members) <= MAX_MEMBERS, "archive has too many entries")
    records, folded = {}, set()
    total = 0
    for member in members:
        path = archive_path(member.name)
        name = str(path)
        need(name not in records and name.casefold() not in folded, "duplicate archive path")
        need(member.isdir() or member.isfile() or member.issym() or member.islnk(),
             "special archive entry")
        need(not member.mode & 0o7000, "privileged archive mode")
        need(0 <= member.size <= MAX_MEMBER_BYTES, "oversized archive member")
        total += member.size
        need(total <= MAX_ARCHIVE_BYTES, "archive exceeds expanded size limit")
        records[name] = member
        folded.add(name.casefold())
    # Also reject a/B and A/c: explicit directory records need not exist in a tar.
    spellings = {}
    for name in records:
        path = PurePosixPath(name)
        for parent in (path, *path.parents):
            if str(parent) == ".":
                continue
            key = str(parent).casefold()
            need(key not in spellings or spellings[key] == str(parent), "case-aliased ancestor")
            spellings[key] = str(parent)
        for ancestor in path.parents:
            record = records.get(str(ancestor))
            need(record is None or record.isdir(), "non-directory archive ancestor")
    for name, member in records.items():
        if member.issym() or member.islnk():
            target = str(link_destination(name, member.linkname, member.islnk()))
            seen = {name}
            while True:
                need(target not in seen, "archive link cycle")
                seen.add(target)
                need(target in records, "dangling archive link")
                record = records[target]
                if record.issym() or record.islnk():
                    target = str(link_destination(target, record.linkname, record.islnk()))
                    continue
                need(record.isfile(), "archive links may only target regular files")
                break
    return records


def verified_asset(path, asset):
    need(path.is_file() and not path.is_symlink(), "missing asset: " + path.name)
    need(path.stat().st_size == asset["size"], "asset size mismatch: " + path.name)
    need(digest(path) == asset["sha256"], "asset SHA-256 mismatch: " + path.name)


def fetch_assets(lock, offline=False):
    STAGING.mkdir(parents=True, exist_ok=True)
    need(not STAGING.is_symlink(), "staging cannot be a symlink")
    result = {}
    for key, asset in lock["assets"].items():
        path = STAGING / asset["filename"]
        if not path.exists():
            need(not offline, "offline cache missing: " + path.name)
            partial = path.with_name(path.name + ".partial")
            need(not partial.exists() and not partial.is_symlink(), "stale partial download")
            try:
                request = urllib.request.Request(asset["url"], headers={"User-Agent": "cc-translate-p0"})
                with urllib.request.urlopen(request, timeout=60) as response, partial.open("xb") as out:
                    need(urllib.parse.urlsplit(response.url).scheme == "https", "insecure redirect")
                    length = 0
                    while chunk := response.read(1024 * 1024):
                        length += len(chunk)
                        need(length <= asset["size"], "download exceeds locked size")
                        out.write(chunk)
                verified_asset(partial, asset)
                partial.rename(path)
            finally:
                partial.unlink(missing_ok=True)
        verified_asset(path, asset)
        result[key] = path
    return result


@contextmanager
def full_tar(path):
    # bsdtar converts zstd to a tar STREAM, never extracts paths to disk. Python then
    # validates every header, including entries whose payload we do not retain.
    executable = "/usr/bin/tar" if sys.platform == "darwin" else shutil.which("tar")
    need(executable, "bsdtar with zstd support required for full-build license inspection")
    process = subprocess.Popen(
        [str(executable), "-cf", "-", "--format=pax", "@" + str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            yield archive
        process.stdout.close()
        need(process.wait(timeout=30) == 0, "bsdtar zstd conversion failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()


def license_coverage(metadata, licenses, lock):
    need(metadata["python_version"] == lock["python_version"], "full-build Python mismatch")
    need(metadata["target_triple"] == lock["python_target"], "full-build architecture mismatch")
    need(metadata["apple_sdk_platform"] == "macosx", "full-build platform mismatch")
    need(version(metadata["apple_sdk_deployment_target"]) <= version(lock["deployment_target"]),
         "runtime requires newer macOS")
    required = {metadata["license_path"]}
    exceptions = []
    nodes = [("core", metadata["build_info"]["core"])]
    for name, variants in metadata["build_info"]["extensions"].items():
        nodes.extend((name, entry) for entry in variants)
    for name, node in nodes:
        paths = node.get("license_paths", [])
        bundled = [link for link in node.get("links", [])
                   if link.get("path_static") or link.get("path_dynamic")]
        need(not bundled or paths, "unlicensed bundled dependency: " + name)
        for path in paths:
            archive_path(path)
            if path in licenses:
                required.add(path)
                continue
            expected = lock["license_metadata_exception"]["path"]
            links = node.get("links", [])
            zlinks = [link for link in links if link.get("name") in ("z", "zlib", "zlib-ng")]
            need(path == expected and zlinks and
                 all(link.get("system") is True and not link.get("path_static") and
                     not link.get("path_dynamic") and link.get("name") == "z" for link in zlinks),
                 "missing upstream dependency license: " + path)
            need("licenses/LICENSE.zlib.txt" in licenses, "missing system zlib notice")
            exceptions.append({"extension": name, "path": path, "reason": "system zlib, not zlib-ng"})
    required.update("licenses/" + name for name in lock["required_runtime_licenses"])
    need(all(path in licenses and licenses[path] for path in required), "incomplete runtime licenses")
    return {"required": sorted(required), "not_applicable": exceptions}


def validate_wheel_members(infos):
    records = []
    for info in infos:
        mode = info.external_attr >> 16
        need(not info.flag_bits & 1, "encrypted wheel entry")
        need(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR), "special wheel entry")
        record = tarfile.TarInfo(info.filename)
        record.mode = mode
        record.size = info.file_size
        record.type = tarfile.DIRTYPE if info.is_dir() else tarfile.REGTYPE
        records.append(record)
    return validate_members(records)


def inspect_assets(assets, lock):
    runtime_hashes = {}
    with tarfile.open(assets["runtime"], "r:gz") as archive:
        runtime = validate_members(archive.getmembers())
        need(all(name.startswith("python/") for name in runtime), "unexpected runtime root")
        for name, member in runtime.items():
            if member.isfile() and keep_runtime(name, lock):
                with archive.extractfile(member) as stream:
                    runtime_hashes[name] = stream_digest(stream)
    records, payloads, total = [], {}, 0
    full_hashes = {}
    with full_tar(assets["full_build"]) as archive:
        for member in archive:
            # Bound the stream before collecting its complete header inventory.
            archive_path(member.name)
            total += member.size
            need(len(records) < MAX_MEMBERS and total <= MAX_ARCHIVE_BYTES and
                 0 <= member.size <= MAX_MEMBER_BYTES, "full-build size limit")
            need(member.name.startswith("python/"), "unexpected full-build archive root")
            records.append(member)
            if member.name == "python/PYTHON.json" or member.name.startswith("python/licenses/"):
                need(member.isfile() and member.size <= 2 * 1024 * 1024, "invalid license member")
                with archive.extractfile(member) as stream:
                    payloads[member.name.removeprefix("python/")] = stream.read()
            if member.name.startswith("python/install/"):
                runtime_name = "python/" + member.name.removeprefix("python/install/")
                if member.isfile() and runtime_name in runtime_hashes:
                    with archive.extractfile(member) as stream:
                        full_hashes[runtime_name] = stream_digest(stream)
    validate_members(records)
    need(runtime_hashes and runtime_hashes == full_hashes,
         "retained runtime does not match full-build license/metadata source")
    need("PYTHON.json" in payloads, "full-build PYTHON.json missing")
    metadata = json.loads(payloads.pop("PYTHON.json"))
    coverage = license_coverage(metadata, payloads, lock)
    coverage["retained_files_matching_full_build"] = len(runtime_hashes)
    with zipfile.ZipFile(assets["certifi"]) as wheel:
        validate_wheel_members(wheel.infolist())
        ca = wheel.read(lock["certificate_member"])
        notice = wheel.read(lock["certificate_notice_member"])
        need(b"-----BEGIN CERTIFICATE-----" in ca, "certificate bundle missing")
        need(b"Mozilla Public License" in notice, "certificate notice missing")
    mpl = assets["mpl"].read_bytes()
    need(len(mpl) > 15000 and b"Exhibit A" in mpl and b"Exhibit B" in mpl, "full MPL text missing")
    return metadata, payloads, coverage, ca, notice, mpl


def keep_runtime(name, lock):
    if name in ("python/bin/python3", "python/bin/python3.12", "python/lib/libpython3.12.dylib"):
        return True
    prefix = "python/lib/python3.12/"
    if not name.startswith(prefix):
        return False
    relative = PurePosixPath(name[len(prefix):])
    return (bool(relative.parts) and relative.parts[0] not in lock["excluded_stdlib_roots"] and
            "__pycache__" not in relative.parts and
            not relative.name.endswith((".pyc", ".pyo", ".a")) and
            not relative.name.startswith("_tkinter."))


def extract_runtime(archive_pathname, destination, lock):
    need(not destination.exists(), "runtime destination already exists")
    with tarfile.open(archive_pathname, "r:gz") as archive:
        members = validate_members(archive.getmembers())
        selected = {name: member for name, member in members.items() if keep_runtime(name, lock)}
        need("python/bin/python3" in selected and "python/bin/python3.12" in selected,
             "runtime executable missing")
        destination.mkdir(parents=True)
        for name, member in selected.items():
            output = destination.joinpath(*PurePosixPath(name).parts[1:])
            output.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                output.mkdir(exist_ok=True)
            elif member.isfile():
                with archive.extractfile(member) as source, output.open("xb") as target:
                    shutil.copyfileobj(source, target)
                output.chmod(member.mode & 0o755)
        for name, member in selected.items():
            if member.issym() or member.islnk():
                target = str(link_destination(name, member.linkname, member.islnk()))
                need(target in selected and selected[target].isfile(), "pruned/chained runtime link")
                output = destination.joinpath(*PurePosixPath(name).parts[1:])
                if member.issym():
                    output.symlink_to(member.linkname)
                else:
                    os.link(destination.joinpath(*PurePosixPath(target).parts[1:]), output)
    return sorted(name for name in members if name not in selected)


def version(text):
    need(isinstance(text, str) and re.fullmatch(r"\d+(?:\.\d+){0,2}", text),
         "invalid deployment version")
    return tuple((list(map(int, text.split("."))) + [0, 0])[:3])


def parse_load_commands(text):
    result = {"minimums": [], "rpaths": [], "id": None}
    blocks = re.split(r"(?m)^Load command \d+\s*$", text)
    for block in blocks:
        match = re.search(r"(?m)^\s*cmd (LC_\w+)\s*$", block)
        if not match:
            continue
        command = match.group(1)
        if command in ("LC_VERSION_MIN_MACOSX", "LC_BUILD_VERSION"):
            if command == "LC_BUILD_VERSION":
                target = re.search(r"(?m)^\s*platform (\S+)\s*$", block)
                need(target and target.group(1).lower() in ("1", "macos"), "non-macOS Mach-O")
            key = "minos" if command == "LC_BUILD_VERSION" else "version"
            value = re.search(r"(?m)^\s*" + key + r" (\S+)\s*$", block)
            need(value, "missing Mach-O minimum OS")
            version(value.group(1))
            result["minimums"].append(value.group(1))
        elif command in ("LC_RPATH", "LC_ID_DYLIB"):
            key = "path" if command == "LC_RPATH" else "name"
            value = re.search(r"(?m)^\s*" + key + r" (.+) \(offset \d+\)\s*$", block)
            need(value, "malformed Mach-O load command")
            if command == "LC_RPATH":
                result["rpaths"].append(value.group(1))
            else:
                result["id"] = value.group(1)
        elif command.startswith("LC_VERSION_MIN_"):
            raise BundleError("non-macOS minimum deployment command")
    need(result["minimums"], "Mach-O missing deployment target")
    return result


def parse_dependencies(text):
    dependencies = []
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        match = re.fullmatch(r"\s+(.+) \(compatibility version [^,]+, current version [^)]+\)", line)
        need(match, "unrecognized otool dependency")
        dependencies.append(match.group(1))
    return dependencies


def system_path(path):
    return path.startswith("/usr/lib/") or path.startswith("/System/Library/")


@lru_cache(maxsize=512)
def system_library_exists(path):
    if Path(path).is_file():
        return True
    if sys.platform != "darwin":
        return False
    # Modern macOS removes many system dylibs from the filesystem. Do not treat an
    # arbitrary /usr/lib/... candidate as resolved merely because it has that prefix.
    try:
        contains = ctypes.CDLL(None)._dyld_shared_cache_contains_path
        contains.argtypes = [ctypes.c_char_p]
        contains.restype = ctypes.c_bool
        return bool(contains(path.encode("utf-8")))
    except (AttributeError, OSError) as error:
        raise BundleError("cannot verify system dyld shared-cache membership") from error


def contained(path, root):
    return path.resolve().is_relative_to(root.resolve())


def expand_dyld(path, binary, executable, bundle):
    need(".." not in PurePosixPath(path).parts or path.startswith(("@loader_path/", "@executable_path/")),
         "untrusted absolute dyld traversal")
    for token, base in (("@loader_path", binary.parent), ("@executable_path", executable.parent)):
        if path == token or path.startswith(token + "/"):
            candidate = (base / path[len(token):].lstrip("/")).resolve()
            need(contained(candidate, bundle), "dyld path escapes bundle")
            return candidate
    if system_path(path):
        need(not any(p in (".", "..") for p in PurePosixPath(path).parts), "system path traversal")
        return path
    raise BundleError("external or unsupported dyld path: " + path)


def resolve_dependency(name, binary, executable, bundle, rpaths):
    if name.startswith("@rpath/"):
        suffix = name[len("@rpath/"):]
        need(suffix and not any(p in ("", ".", "..") for p in suffix.split("/")),
             "invalid @rpath dependency")
        for base in rpaths:
            if isinstance(base, str):
                candidate = base.rstrip("/") + "/" + suffix
                if system_path(candidate) and system_library_exists(candidate):
                    return candidate
            else:
                candidate = (base / suffix).resolve()
                need(contained(candidate, bundle), "@rpath target escapes bundle")
                if candidate.is_file():
                    return candidate
        raise BundleError("unresolved bundle dependency: " + name)
    candidate = expand_dyld(name, binary, executable, bundle)
    exists = system_library_exists(candidate) if isinstance(candidate, str) else candidate.is_file()
    need(exists, "missing system/bundle dependency: " + name)
    return candidate


def run(args, env=None):
    return subprocess.check_output([str(arg) for arg in args], text=True, env=env).strip()


def require_macos():
    need(sys.platform == "darwin" and platform.machine() == "arm64",
         "BLOCKED: native build/audit requires an arm64 Mac; Windows is not macOS validation")
    need(XCODE.is_dir(), "BLOCKED: /Applications/Xcode_16.4.app is unavailable")
    environment = os.environ.copy()
    environment["DEVELOPER_DIR"] = str(XCODE)
    environment["MACOSX_DEPLOYMENT_TARGET"] = "14.0"
    xcode = run(["/usr/bin/xcodebuild", "-version"], environment)
    need(xcode.splitlines()[0] == "Xcode 16.4", "BLOCKED: Xcode version mismatch")
    return environment, {
        "xcode": xcode, "architecture": platform.machine(),
        "sdk": run(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"], environment),
    }


def validate_plist(info, lock):
    need(info.get("CFBundleIdentifier") == lock["bundle_identifier"], "unexpected bundle identifier")
    need(info.get("CFBundleExecutable") == "CCTranslateMac", "unexpected native executable")
    need(info.get("CFBundlePackageType") == "APPL", "invalid app package type")
    need(info.get("LSUIElement") is True, "P0 must be an LSUIElement app")
    need(info.get("LSMinimumSystemVersion") == lock["deployment_target"], "wrong bundle minimum OS")


def audit_bundle(app, lock, environment=None):
    need(app.is_dir() and not app.is_symlink(), "bundle missing or symlinked")
    contents = app / "Contents"
    validate_plist(plistlib.loads((contents / "Info.plist").read_bytes()), lock)
    required = [
        "MacOS/CCTranslateMac", "Helpers/python/bin/python3",
        "Helpers/python/lib/libCCProcessSupport.dylib",
        "Resources/Core/launch.py", "Resources/Core/cc_macos/__main__.py",
        "Resources/Core/cc_macos/dictionary_probe.py",
        "Resources/Core/cc_macos/config_fixture.py",
        "Resources/Core/cacert.pem", "Resources/Licenses/certifi/LICENSE",
        "Resources/Licenses/certifi/MPL-2.0.txt", "Resources/source-manifest.json",
        "Resources/Licenses/Python/PYTHON.json",
    ]
    required += ["Resources/Core/" + name for name in SHARED_CORE_MODULES]
    required += ["Resources/Core/cc_providers/" + name for name in PROVIDER_CORE_FILES]
    required += ["Resources/Licenses/Python/licenses/" + name for name in lock["required_runtime_licenses"]]
    need(all((contents / path).is_file() for path in required), "missing bundle resources/licenses")
    need(os.access(contents / "MacOS/CCTranslateMac", os.X_OK) and
         os.access(contents / "Helpers/python/bin/python3", os.X_OK), "non-executable bundle entry")
    provenance = json.loads((contents / "Resources/source-manifest.json").read_bytes())
    need(provenance["lock"] == lock, "bundle source lock mismatch")
    resource_hashes = provenance["resource_hashes"]
    need(all(path in resource_hashes for path in required if path.startswith("Resources/")
             and path != "Resources/source-manifest.json"), "incomplete resource source inventory")
    for name, expected in resource_hashes.items():
        safe = archive_path(name)
        need(safe.parts[0] == "Resources", "invalid resource inventory path")
        target = contents.joinpath(*safe.parts)
        need(contained(target, app) and target.is_file() and not target.is_symlink(),
             "missing/linked recorded resource")
        need(digest(target) == expected, "bundled source/license content changed")
    ca = contents / "Resources/Core/cacert.pem"
    need(digest(ca) == provenance["certificate_sha256"], "bundle CA changed")
    files, inventory = [], []
    for path in sorted(app.rglob("*")):
        relative = path.relative_to(app).as_posix()
        need(contained(path, app), "bundle symlink escapes root")
        if path.is_symlink():
            need(path.is_file(), "dangling/directory bundle symlink")
            inventory.append({"path": relative, "symlink": os.readlink(path)})
        elif path.is_file():
            need(path.suffix not in (".pyc", ".pyo") and "__pycache__" not in path.parts,
                 "bytecode cache in bundle")
            need(not path.stat().st_mode & 0o7000, "privileged bundle mode")
            inventory.append({"path": relative, "sha256": digest(path)})
            with path.open("rb") as stream:
                if stream.read(4) in MACHO_MAGIC:
                    files.append(path)
        else:
            need(path.is_dir(), "special bundle entry")
    need(files, "bundle has no Mach-O files")
    native = contents / "MacOS/CCTranslateMac"
    python = (contents / "Helpers/python/bin/python3").resolve()
    libpython = contents / "Helpers/python/lib/libpython3.12.dylib"
    bridge = contents / "Helpers/python/lib/libCCProcessSupport.dylib"
    need(all(path in files for path in (native, python, libpython, bridge)), "bundle runtime is not Mach-O")
    parsed, report = {}, []
    for binary in files:
        description = run(["/usr/bin/file", "-b", binary], environment)
        need("Mach-O" in description, "file did not confirm Mach-O")
        archs = run(["/usr/bin/lipo", "-archs", binary], environment).split()
        need(archs == ["arm64"], "non-arm64 or universal binary: " + binary.name)
        parsed[binary] = parse_load_commands(run(["/usr/bin/otool", "-l", binary], environment))
        need(all(version(v) <= version(lock["deployment_target"])
                 for v in parsed[binary]["minimums"]), "Mach-O requires newer macOS: " + binary.name)
        if binary in (native, bridge):
            need(all(version(v) == version(lock["deployment_target"])
                     for v in parsed[binary]["minimums"]), "native deployment target drift")
        parsed[binary]["dependencies"] = parse_dependencies(run(["/usr/bin/otool", "-L", binary], environment))
    for binary, commands in parsed.items():
        executable = python if binary.is_relative_to(contents / "Helpers") else native
        parents = [executable]
        if executable == python and binary not in (python, libpython):
            parents.append(libpython)
        rpaths = [expand_dyld(p, binary, executable, app) for p in commands["rpaths"]]
        for parent in parents:
            if parent != binary:
                rpaths.extend(expand_dyld(p, parent, executable, app)
                              for p in parsed[parent]["rpaths"])
        if commands["id"]:
            install_id = commands["id"]
            need(install_id.startswith(("@rpath/", "@loader_path/", "@executable_path/")),
                 "non-relocatable dylib install name")
            need(resolve_dependency(install_id, binary, executable, app, rpaths) == binary.resolve(),
                 "dylib install name does not resolve to itself")
        dependencies = [name for name in commands["dependencies"] if name != commands["id"]]
        for name in dependencies:
            target = resolve_dependency(name, binary, executable, app, rpaths)
            need(isinstance(target, str) or target in parsed, "dependency is not audited Mach-O")
        report.append({
            "path": binary.relative_to(app).as_posix(), "architecture": "arm64",
            "minimum_os": commands["minimums"], "rpaths": commands["rpaths"],
            "install_id": commands["id"], "dependencies": dependencies,
        })
    return {"schema": 1, "development_only": True, "release_gate": "NOT PASSED",
            "checks": report, "inventory": inventory,
            "not_tested": ["Developer ID", "Hardened Runtime", "notarization", "Gatekeeper",
                           "Finder", "GUI/TCC", "macOS 14 runtime", "Intel"]}


def copy_sources(source, destination):
    need(source.is_dir() and not source.is_symlink(), "helper package missing")
    for path in source.rglob("*"):
        need(not path.is_symlink(), "source helper symlink rejected")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def copy_core_sources(core):
    modules = [ROOT / name for name in SHARED_CORE_MODULES]
    contracts = [ROOT / "cc_providers" / name for name in PROVIDER_CORE_FILES]
    need(all(path.is_file() and not path.is_symlink() for path in modules + contracts),
         "shared core or provider contract missing or linked")
    need(not (ROOT / "cc_providers").is_symlink(), "linked provider source directory")
    copy_sources(ROOT / "cc_macos", core / "cc_macos")
    shutil.copy2(ROOT / "cc_macos/launch.py", core / "launch.py")
    for path in modules:
        shutil.copy2(path, core / path.name)
    (core / "cc_providers").mkdir()
    for path in contracts:
        shutil.copy2(path, core / "cc_providers" / path.name)


def build(lock, offline=False):
    environment, toolchain = require_macos()
    # Never update an existing (possibly signed) bundle, even on a second build.
    need(not APP.exists() and not APP.is_symlink(), "output exists; choose a clean development build")
    need(not BUILD.is_symlink(), "build directory cannot be a symlink")
    assets = fetch_assets(lock, offline)
    metadata, licenses, coverage, ca, notice, mpl = inspect_assets(assets, lock)
    args = ["/usr/bin/xcrun", "swift", "build", "--package-path", ROOT / "macos",
            "--configuration", "release", "--triple", "arm64-apple-macosx14.0",
            "--product", "CCTranslateMac"]
    subprocess.run([str(a) for a in args], env=environment, check=True)
    binary_directory = Path(run([*args, "--show-bin-path"], environment))
    binary = binary_directory / "CCTranslateMac"
    subprocess.run([str(a) for a in [*args[:-1], "CCProcessSupport"]], env=environment, check=True)
    info = (ROOT / "macos/Resources/Info.plist").read_bytes()
    validate_plist(plistlib.loads(info), lock)
    contents = APP / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    shutil.copy2(binary, contents / "MacOS/CCTranslateMac")
    (contents / "Info.plist").write_bytes(info)
    excluded = extract_runtime(assets["runtime"], contents / "Helpers/python", lock)
    shutil.copy2(binary_directory / "libCCProcessSupport.dylib",
                 contents / "Helpers/python/lib/libCCProcessSupport.dylib")
    core = contents / "Resources/Core"
    copy_core_sources(core)
    (core / "cacert.pem").write_bytes(ca)
    license_root = contents / "Resources/Licenses"
    for name, data in licenses.items():
        target = license_root / "Python" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    write_json(license_root / "Python/PYTHON.json", metadata)
    (license_root / "certifi").mkdir()
    (license_root / "certifi/LICENSE").write_bytes(notice)
    (license_root / "certifi/MPL-2.0.txt").write_bytes(mpl)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES", license_root / "THIRD_PARTY_NOTICES")
    dictionaries = ROOT / "data/dictionary/licenses"
    need(dictionaries.is_dir(), "repository dictionary licenses missing")
    copy_sources(dictionaries, license_root / "dictionary")
    resources = contents / "Resources"
    resource_hashes = {path.relative_to(contents).as_posix(): digest(path)
                       for path in sorted(resources.rglob("*")) if path.is_file()}
    write_json(contents / "Resources/source-manifest.json", {
        "schema": 1, "development_only": True, "signing": "NOT performed by these scripts",
        "release_gate": "NOT PASSED", "application_license": "requires separate confirmation",
        "lock": lock, "toolchain": toolchain, "runtime_license_coverage": coverage,
        "resource_hashes": resource_hashes,
        "excluded_runtime_members": excluded, "certificate_sha256": hashlib.sha256(ca).hexdigest(),
        "certificate_source": lock["assets"]["certifi"]["url"],
        "source_commit": run(["git", "-C", ROOT, "rev-parse", "HEAD"]),
        "source_tree_dirty": bool(run(["git", "-C", ROOT, "status", "--porcelain"])),
    })
    report = audit_bundle(APP, lock, environment)
    write_json(BUILD / "bundle-audit.json", report)
    print("Built and statically audited development bundle:", APP)
    print("NOT signed/notarized; native helper smoke and all manual/release gates remain separate.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "build", "verify"))
    parser.add_argument("--offline", action="store_true", help="require verified pre-downloaded assets")
    parser.add_argument("--development", action="store_true", help="acknowledge non-release build")
    args = parser.parse_args(argv)
    try:
        lock = load_lock()
        if args.command == "inspect":
            assets = fetch_assets(lock, args.offline)
            metadata, licenses, coverage, ca, _, _ = inspect_assets(assets, lock)
            report = {"python": metadata["python_version"], "licenses": sorted(licenses),
                      "coverage": coverage, "certificate_bytes": len(ca),
                      "runtime_execution": "NOT performed", "assets": lock["assets"]}
            write_json(STAGING / "inspection.json", report)
            print("Verified locked archives, complete retained-runtime licenses and CA/MPL.")
            print("No runtime installed/executed. Report: tools/macos/.staging/inspection.json")
        elif args.command == "build":
            need(args.development, "build requires --development; this is not a release tool")
            build(lock, args.offline)
        else:
            environment, _ = require_macos()
            need(not BUILD.is_symlink(), "build directory cannot be a symlink")
            report = audit_bundle(APP, lock, environment)
            BUILD.mkdir(parents=True, exist_ok=True)
            write_json(BUILD / "bundle-audit.json", report)
            print("Static Mach-O/resource audit passed; signing/manual gates NOT PASSED.")
        return 0
    except (BundleError, OSError, ValueError, KeyError, tarfile.TarError,
            zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print("BLOCKED:", error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
