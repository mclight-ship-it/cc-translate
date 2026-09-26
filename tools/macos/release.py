"""Fail-closed Apple Silicon releases: ad-hoc code identity, authenticated Sparkle ZIPs."""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr
import zipfile

if __package__:
    from . import bundle
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.macos import bundle

ROOT = bundle.ROOT
REPOSITORY = "mclight-ship-it/cc-translate"
BUNDLE_ID = "dev.cc-translate.macos.probe"
CHANNEL_BRANCH = "macos-updates"
FEED_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/{CHANNEL_BRANCH}/appcast.xml"
DOWNLOADS_URL = f"https://github.com/{REPOSITORY}/releases/latest"
SPARKLE = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"
RELEASE = "{https://github.com/mclight-ship-it/cc-translate/ns/release}"
WORK = bundle.BUILD / "formal-release"
CHANNEL_CONFIG = bundle.HERE / "release-channel.json"
APP_NAME = "CC Translate.app"
CORE_MODULES = (
    "__init__.py", "__main__.py", "server.py", "protocol.py", "configuration.py",
    "config_owner.py", "file_owner.py", "history.py", "history_owner.py",
    "dictionary.py", "image.py", "translation.py", "translation_cache.py",
    # Diagnostics is an actual App operation, not an unused packaging fixture.
    "probes.py", "dictionary_probe.py", "config_fixture.py",
    "catalog_fixture.py", "catalog_process_fixture.py",
)
CORE_FILES = frozenset(
    ["launch.py", "cacert.pem", *bundle.SHARED_CORE_MODULES]
    + ["cc_macos/" + name for name in CORE_MODULES]
    + ["cc_providers/" + name for name in bundle.PROVIDER_CORE_FILES]
)
PLIST_KEYS = frozenset((
    "CFBundleIdentifier", "CFBundleExecutable", "CFBundleName", "CFBundleDisplayName",
    "CFBundleIconFile", "CFBundlePackageType", "CFBundleShortVersionString", "CFBundleVersion",
    "LSMinimumSystemVersion", "LSUIElement", "NSHighResolutionCapable", "NSPrincipalClass",
    "SUEnableAutomaticChecks", "SUAutomaticallyUpdate", "SUSendProfileInfo",
    "SUVerifyUpdateBeforeExtraction", "SUFeedURL", "SUPublicEDKey", "CCReleaseChannel",
))
BUILDER_PATH = re.compile(
    rb"(?<![A-Za-z0-9/.:_-])/(?:Users|home|private/(?:tmp|var/folders)"
    rb"|opt/hostedtoolcache|Volumes/(?:work|build)|install)(?:/|\\)"
)
SYS_CONFIG_KEYS = frozenset((
    "ABIFLAGS", "SOABI", "EXT_SUFFIX", "MULTIARCH", "VERSION", "LDVERSION",
    "Py_DEBUG", "Py_ENABLE_SHARED", "Py_GIL_DISABLED", "SIZEOF_VOID_P", "WITH_PYMALLOC",
    "MACOSX_DEPLOYMENT_TARGET", "SHLIB_SUFFIX", "HAVE_DYNAMIC_LOADING",
))


def need(condition, message):
    bundle.need(condition, message)


def release_version(value):
    need(isinstance(value, str) and
         re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value),
         "release version must be canonical MAJOR.MINOR.PATCH")
    parts = tuple(map(int, value.split(".")))
    need(all(part < 1_000_000 for part in parts), "release version component too large")
    need(parts > (0, 0, 0), "0.0.0 is not a release")
    return parts


def build_number(value):
    major, minor, patch = release_version(value)
    # Injective, monotonic and independent of a workflow's mutable run counter.
    return str(major * 1_000_000_000_000 + minor * 1_000_000 + patch + 1)


def public_key(value):
    need(isinstance(value, str), "MACOS_SPARKLE_PUBLIC_KEY is required")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise bundle.BundleError("invalid Sparkle public key") from error
    need(len(decoded) == 32 and any(decoded) and base64.b64encode(decoded).decode() == value,
         "Sparkle public key must be canonical base64 of 32 nonzero bytes")
    return value


def pinned_public_key(value):
    public_key(value)
    need(CHANNEL_CONFIG.is_file() and not CHANNEL_CONFIG.is_symlink(), "tracked release channel config missing")
    config = json.loads(CHANNEL_CONFIG.read_text(encoding="utf-8"))
    need(isinstance(config, dict) and set(config) ==
         {"schema", "bundle_identifier", "feed_url", "public_ed25519_key"} and
         type(config["schema"]) is int and config["schema"] == 1,
         "unsupported tracked release channel configuration")
    need(config["bundle_identifier"] == BUNDLE_ID and config["feed_url"] == FEED_URL,
         "tracked release channel identity/feed mismatch")
    need(value == public_key(config["public_ed25519_key"]),
         "MACOS_SPARKLE_PUBLIC_KEY differs from the tracked release key pin; refusing implicit key rotation")
    return value


def asset_name(version):
    release_version(version)
    return f"CC-Translate-macos-arm64-{version}.zip"


def asset_url(version):
    return f"https://github.com/{REPOSITORY}/releases/download/macos-v{version}/{asset_name(version)}"


def release_info(template, version, key):
    info = {k: v for k, v in template.items() if k in PLIST_KEYS}
    info.update(CFBundleIdentifier=BUNDLE_ID, CFBundleShortVersionString=version,
                CFBundleVersion=build_number(version), SUFeedURL=FEED_URL,
                SUPublicEDKey=pinned_public_key(key), CCReleaseChannel="stable",
                SUEnableAutomaticChecks=False, SUAutomaticallyUpdate=False,
                SUSendProfileInfo=False, SUVerifyUpdateBeforeExtraction=True)
    bundle.validate_plist(info, {**bundle.load_lock(), "bundle_identifier": BUNDLE_ID})
    return info


def appcast(version, key, signature, length, sha256, *, url=None):
    pinned_public_key(key)
    need(len(base64.b64decode(signature, validate=True)) == 64, "invalid EdDSA signature length")
    need(type(length) is int and length > 0, "invalid archive length")
    need(re.fullmatch(r"[0-9a-f]{64}", sha256), "invalid archive checksum")
    target = url or asset_url(version)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" '
        'xmlns:cc="https://github.com/mclight-ship-it/cc-translate/ns/release">'
        '<channel><title>CC Translate for macOS</title>'
        f'<link>{escape(DOWNLOADS_URL)}</link><cc:publicKey>{key}</cc:publicKey>'
        f'<item><title>CC Translate {escape(version)}</title>'
        f'<sparkle:version>{build_number(version)}</sparkle:version>'
        f'<sparkle:shortVersionString>{version}</sparkle:shortVersionString>'
        '<sparkle:minimumSystemVersion>14.0</sparkle:minimumSystemVersion>'
        f'<cc:sha256>{sha256}</cc:sha256>'
        f'<enclosure url={quoteattr(target)} sparkle:edSignature={quoteattr(signature)} '
        f'length="{length}" type="application/octet-stream" /></item></channel></rss>\n'
    ).encode()


def read_appcast(data, key):
    need(len(data) <= 1_048_576 and b"<!DOCTYPE" not in data.upper() and
         b"<!ENTITY" not in data.upper(), "unsafe or oversized appcast")
    root = ET.fromstring(data)
    need(root.tag == "rss", "not an RSS appcast")
    channel = root.find("channel")
    need(channel is not None and channel.findtext(RELEASE + "publicKey") == pinned_public_key(key),
         "published channel key differs; automatic key rotation is forbidden")
    items = channel.findall("item")
    need(len(items) == 1, "stable feed must contain exactly one current release")
    item = items[0]
    version = item.findtext(SPARKLE + "shortVersionString")
    release_version(version)
    need(item.findtext(SPARKLE + "version") == build_number(version), "published build/version mismatch")
    need(item.findtext(SPARKLE + "minimumSystemVersion") == "14.0", "published minimum OS mismatch")
    enclosure = item.find("enclosure")
    need(enclosure is not None and enclosure.get("url") == asset_url(version),
         "appcast must reference its immutable HTTPS GitHub versioned ZIP")
    need(enclosure.get("type") == "application/octet-stream", "unexpected update archive type")
    signature = enclosure.get(SPARKLE + "edSignature", "")
    try:
        valid_signature = len(base64.b64decode(signature, validate=True)) == 64
    except (ValueError, binascii.Error):
        valid_signature = False
    need(valid_signature, "published signature missing or malformed")
    length = enclosure.get("length", "")
    need(re.fullmatch(r"[1-9][0-9]*", length), "published length invalid")
    sha256 = item.findtext(RELEASE + "sha256", "")
    need(re.fullmatch(r"[0-9a-f]{64}", sha256), "published checksum missing")
    return dict(version=version, build=build_number(version), signature=signature,
                length=int(length), sha256=sha256, url=enclosure.get("url"))


class HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        need(urllib.parse.urlsplit(newurl).scheme == "https", "HTTPS redirect downgrade refused")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(url, target, limit):
    need(urllib.parse.urlsplit(url).scheme == "https", "release download requires HTTPS")
    need(not target.exists() and not target.is_symlink(), "download destination already exists")
    opener = urllib.request.build_opener(HTTPSOnlyRedirect())
    with opener.open(url, timeout=120) as response, target.open("xb") as output:
        total = 0
        while data := response.read(1024 * 1024):
            total += len(data)
            need(total <= limit, "release download exceeds size limit")
            output.write(data)


def previous_release(version, key, work):
    """Treat only an authenticated 404 on a never-used channel as a first release."""
    pages = json.loads(bundle.run([
        "gh", "api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases?per_page=100"
    ]))
    published = []
    for page in pages:
        for release in page:
            tag = release["tag_name"]
            if tag.startswith("macos-v"):
                old = tag.removeprefix("macos-v")
                release_version(old)
                need(tag != "macos-v" + version, "release tag already exists; assets are immutable")
                if not release["draft"] and not release["prerelease"]:
                    published.append(old)
    feed = work / "previous-appcast.xml"
    try:
        download(FEED_URL, feed, 1_048_576)
    except urllib.error.HTTPError as error:
        need(error.code == 404 and not published, "existing release channel is unavailable")
        return None
    previous = read_appcast(feed.read_bytes(), key)
    need(published and previous["version"] == max(published, key=release_version),
         "feed is missing or behind the newest published Mac release; repair it before releasing")
    need(release_version(version) > release_version(previous["version"]) and
         int(build_number(version)) > int(previous["build"]), "release must increase version and build")
    previous["archive"] = work / "previous.zip"
    download(previous["url"], previous["archive"], bundle.MAX_ARCHIVE_BYTES)
    need(previous["archive"].stat().st_size == previous["length"] and
         bundle.digest(previous["archive"]) == previous["sha256"], "previous archive differs from feed")
    return previous


def keep_release_path(relative):
    """Only runtime code, product resources and required license notices are distributable."""
    path = PurePosixPath(relative)
    if relative in ("Contents/Info.plist", "Contents/MacOS/CCTranslateMac",
                    "Contents/_CodeSignature/CodeResources"):
        return True
    prefix = "Contents/Resources/"
    if relative.startswith(prefix):
        name = relative.removeprefix(prefix)
        if name in (bundle.ICON_NAME, bundle.SUPPORT_IMAGE_NAME, "source-manifest.json"):
            return True
        if name.startswith("Core/"):
            return name.removeprefix("Core/") in CORE_FILES
        if name.startswith("Licenses/"):
            return name != "Licenses/Python/PYTHON.json" and (
                name in ("Licenses/THIRD_PARTY_NOTICES", "Licenses/Sparkle/LICENSE",
                         "Licenses/certifi/LICENSE", "Licenses/certifi/MPL-2.0.txt")
                or name.startswith(("Licenses/Python/licenses/LICENSE.", "Licenses/dictionary/")))
        if name.startswith("python/"):
            if any(p in ("test", "tests", "__pycache__", "unittest", "lib2to3", "pydoc_data")
                   for p in path.parts):
                return False
            if path.name.startswith("_test") or path.suffix in (".pyc", ".pyo", ".a"):
                return False
            return name == "python/lib/libCCProcessSupport.dylib" or bundle.keep_runtime(name, bundle.load_lock())
    prefix = "Contents/Frameworks/Sparkle.framework/"
    if relative.startswith(prefix):
        return not any(p in ("Headers", "Modules") for p in PurePosixPath(relative[len(prefix):]).parts)
    return False


def validate_core_imports(core):
    """Fail if a future product import depends on a module the release profile prunes."""
    available = {str(PurePosixPath(p).with_suffix("")).replace("/", ".")
                 for p in CORE_FILES if p.endswith(".py")}
    for relative in CORE_FILES:
        if not relative.endswith(".py"):
            continue
        source = core / relative
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=relative)
        package = relative.rpartition("/")[0].replace("/", ".")
        def runtime_nodes(node):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                for child in node.orelse:
                    yield from runtime_nodes(child)
                return
            yield node
            for child in ast.iter_child_nodes(node):
                yield from runtime_nodes(child)

        for node in runtime_nodes(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split(".")
                    need(node.level <= len(base), "relative import escapes bundled package")
                    prefix = ".".join(base[:len(base) - node.level + 1])
                    names = [prefix + "." + node.module] if node.module else [
                        prefix + "." + alias.name for alias in node.names]
                else:
                    names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith(("cc_macos.", "cc_providers.")):
                    need(name in available or name + ".__init__" in available,
                         f"release prunes product dependency: {relative} imports {name}")


def sanitize_sysconfig(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "build_time_vars" for t in node.targets)]
    need(len(assignments) == 1, "unexpected pinned Python sysconfig format")
    values = ast.literal_eval(assignments[0].value)
    minimal = {key: value for key, value in values.items() if key in SYS_CONFIG_KEYS}
    need("SOABI" in minimal and "EXT_SUFFIX" in minimal, "Python ABI metadata missing")
    path.write_text("build_time_vars = " + repr(minimal) + "\n", encoding="utf-8")


def minimize(app, version, key, source_commit):
    contents = app / "Contents"
    development = json.loads((contents / "Resources/source-manifest.json").read_bytes())
    info = release_info(plistlib.loads((contents / "Info.plist").read_bytes()), version, key)
    for path in sorted(app.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        relative = path.relative_to(app).as_posix()
        if (path.is_file() or path.is_symlink()) and not keep_release_path(relative):
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    validate_core_imports(contents / "Resources/Core")
    for path in (contents / "Resources/python/lib/python3.12").glob("_sysconfigdata_*.py"):
        sanitize_sysconfig(path)
    hashes = {path.relative_to(contents).as_posix(): bundle.digest(path)
              for path in (contents / "Resources").rglob("*")
              if path.is_file() and not path.is_symlink() and
              path.relative_to(contents).parts[1] != "python" and path.name != "source-manifest.json"}
    bundle.write_json(contents / "Resources/source-manifest.json", {
        "schema": 1, "development_only": False, "source_commit": source_commit,
        "source_tree_dirty": False,
        "signing": "Ad-hoc code signature; Sparkle Ed25519-authenticated updates",
        "release_gate": "Not Developer ID signed or notarized; Gatekeeper and TCC continuity are not guaranteed",
        "lock": {"python_version": bundle.load_lock()["python_version"]},
        "toolchain": {key: development["toolchain"][key] for key in ("xcode", "sdk", "architecture")},
        "resource_hashes": hashes,
    })


def macho_files(app):
    result = []
    for path in sorted(app.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in bundle.MACHO_MAGIC:
                    result.append(path)
    return result


def seal(app, environment):
    framework = app / "Contents/Frameworks/Sparkle.framework"
    binaries = macho_files(app)
    for path in binaries:
        if not path.is_relative_to(framework):
            bundle.run(["/usr/bin/strip", "-S", path], environment)
            bundle.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", path], environment)
    # Pruned SDK headers require resealing the framework, not rewriting its nested helpers.
    bundle.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", framework], environment)
    bundle.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", app], environment)
    for path in [*binaries, framework, app]:
        bundle.run(["/usr/bin/codesign", "--verify", "--strict", "--deep", path], environment)


def audit_payload(app, version, key, allowed, environment):
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    need(set(info) <= PLIST_KEYS and info == release_info(info, version, key),
         "release identity/channel/settings differ from the release contract")
    inventory = set()
    forbidden = [str(ROOT).encode(), str(ROOT).replace("\\", "/").encode()]
    for path in app.rglob("*"):
        relative = path.relative_to(app).as_posix()
        need(bundle.contained(path, app), "release link escapes bundle")
        if path.is_symlink():
            need(path.exists() and not os.path.isabs(os.readlink(path)), "invalid release link")
        if path.is_file() or path.is_symlink():
            inventory.add(relative)
            need(relative in allowed and keep_release_path(relative), "unexpected release payload: " + relative)
            if not path.is_symlink():
                data = path.read_bytes()
                need(not any(token in data for token in forbidden) and not BUILDER_PATH.search(data),
                     "absolute builder path in release payload: " + relative)
    need(inventory == allowed, "release payload inventory changed")
    for relative in CORE_FILES:
        need((app / "Contents/Resources/Core" / relative).is_file(), "required product module missing")
    bundle.validate_icon(app / "Contents/Resources" / bundle.ICON_NAME)
    validate_core_imports(app / "Contents/Resources/Core")
    report = bundle.audit_macho(app, bundle.load_lock(), environment)
    report.update(development_only=False,
                  release_gate="Payload audited; Ed25519 and actual installation are separate mandatory gates")
    bundle.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app], environment)
    return report


def verify_zip(archive):
    with zipfile.ZipFile(archive) as source:
        need(source.testzip() is None, "archive CRC verification failed")
        names, records, spellings = set(), {}, {}
        total = 0
        for item in source.infolist():
            path = bundle.archive_path(item.filename)
            need(path.parts[0] == APP_NAME, "archive contains files outside the application")
            need(item.filename.casefold() not in names, "case-aliased release ZIP member")
            names.add(item.filename.casefold())
            name = str(path)
            need(name not in records, "duplicate release ZIP member")
            records[name] = item
            for ancestor in (path, *path.parents):
                if str(ancestor) != ".":
                    folded = str(ancestor).casefold()
                    need(folded not in spellings or spellings[folded] == str(ancestor),
                         "case-aliased release ZIP ancestor")
                    spellings[folded] = str(ancestor)
            need(item.file_size <= bundle.MAX_MEMBER_BYTES, "oversized release ZIP member")
            total += item.file_size
            need(total <= bundle.MAX_ARCHIVE_BYTES, "oversized expanded release ZIP")
            mode = item.external_attr >> 16
            need(not mode & 0o7000, "privileged release ZIP entry")
            need((mode & 0o170000) in (0, 0o040000, 0o100000, 0o120000),
                 "special release ZIP entry")
            if (mode & 0o170000) == 0o120000:
                bundle.link_destination(item.filename, source.read(item).decode("utf-8"))
        need(f"{APP_NAME}/Contents/Info.plist".casefold() in names, "archive has no application")
        for name in records:
            for ancestor in PurePosixPath(name).parents:
                if str(ancestor) in records:
                    need(records[str(ancestor)].is_dir(), "linked/non-directory release ZIP ancestor")


def build_release(version, key, private_seed, acknowledge):
    need(acknowledge, "explicit --acknowledge-ad-hoc is required; this is not notarized")
    release_version(version)
    pinned_public_key(key)
    need(private_seed, "MACOS_SPARKLE_PRIVATE_KEY is missing; release cannot proceed")
    environment, _ = bundle.require_macos()
    environment.pop("MACOS_SPARKLE_PRIVATE_KEY", None)
    need(not bundle.run(["git", "-C", ROOT, "status", "--porcelain"]), "release requires a clean source tree")
    need(not WORK.exists() and not WORK.is_symlink(), "release output exists; use a clean checkout")
    need(not (ROOT / "macos/.build").exists(), "release requires a clean Swift build")
    WORK.mkdir(parents=True)
    signer = WORK / "release-sign"
    bundle.run(["/usr/bin/xcrun", "swiftc", bundle.HERE / "ReleaseSign.swift", "-o", signer], environment)
    challenge = WORK / "signing-check"
    challenge.write_bytes(b"CC Translate release key consistency check\n")

    def sign(path):
        result = subprocess.run([str(signer), "sign", key, str(path)], input=private_seed + "\n",
                                capture_output=True, text=True, check=True, env=environment)
        signature = result.stdout.strip()
        need(bundle.run([signer, "verify", key, path, signature], environment) == "verified",
             "independent Ed25519 verification failed")
        return signature

    sign(challenge)
    previous = previous_release(version, key, WORK)
    if previous:
        need(bundle.run([signer, "verify", key, previous["archive"], previous["signature"]],
                        environment) == "verified", "previous release authenticity check failed")
    source_commit = bundle.run(["git", "-C", ROOT, "rev-parse", "HEAD"])
    bundle.build(bundle.load_lock(), build_number=build_number(version), release_source_maps=True)
    app = WORK / APP_NAME
    shutil.copytree(bundle.APP, app, symlinks=True)
    original_inventory = {path.relative_to(app).as_posix() for path in app.rglob("*")
                          if path.is_file() or path.is_symlink()}
    allowed = {p for p in original_inventory if keep_release_path(p)}
    allowed.add("Contents/_CodeSignature/CodeResources")
    minimize(app, version, key, source_commit)
    seal(app, environment)
    report = audit_payload(app, version, key, allowed, environment)
    bundle.write_json(WORK / "payload-audit.json", report)
    assets = WORK / "assets"
    assets.mkdir()
    archive = assets / asset_name(version)
    bundle.run(["/usr/bin/ditto", "-c", "-k", "--norsrc", "--noextattr", "--keepParent", app, archive], environment)
    verify_zip(archive)
    signature = sign(archive)
    checksum = bundle.digest(archive)
    (assets / "appcast.xml").write_bytes(appcast(version, key, signature, archive.stat().st_size, checksum))
    read_appcast((assets / "appcast.xml").read_bytes(), key)
    # This gate consumes the exact signed archive, never a re-zipped fixture.
    if __package__:
        from .release_update_test import verify_release_upgrade
    else:
        from tools.macos.release_update_test import verify_release_upgrade
    verify_release_upgrade(app, archive, version, key, signature, WORK / "upgrade-test", previous)
    need(bundle.digest(archive) == checksum, "upgrade test changed the signed release archive")
    audit_payload(app, version, key, allowed, environment)
    (assets / "SHA256SUMS").write_text("".join(
        f"{bundle.digest(path)}  {path.name}\n" for path in (archive, assets / "appcast.xml")
    ), encoding="utf-8")
    bundle.write_json(WORK / "release-state.json", {
        "version": version, "build": build_number(version), "source_commit": source_commit,
        "previous_version": previous["version"] if previous else None,
        "archive_sha256": checksum, "public_key": key, "signature": signature,
        "signing_mode": "ad-hoc", "notarized": False, "upgrade_gate": "passed",
    })
    need({p.name for p in assets.iterdir()} == {archive.name, "appcast.xml", "SHA256SUMS"},
         "unexpected public release assets")
    print(f"Validated macos-v{version}: {assets}")
    print("Ad-hoc identity only. Not Developer ID signed/notarized; TCC continuity is not guaranteed.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--acknowledge-ad-hoc", action="store_true")
    args = parser.parse_args(argv)
    # Remove the private seed from every child environment, including the application under test.
    private_seed = os.environ.pop("MACOS_SPARKLE_PRIVATE_KEY", None)
    build_release(args.version, os.environ.get("MACOS_SPARKLE_PUBLIC_KEY"), private_seed,
                  args.acknowledge_ad_hoc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
