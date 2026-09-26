"""Publish validated assets, then atomically advance the dedicated Mac appcast Git ref."""

import argparse
import base64
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import zipfile

if __package__:
    from . import bundle, release
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.macos import bundle, release


def api(path, payload=None, method=None):
    command = ["gh", "api", f"repos/{release.REPOSITORY}" + ("/" + path if path else "")]
    if method:
        command += ["--method", method]
    if payload is not None:
        command += ["--input", "-"]
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def channel_head():
    refs = api("git/matching-refs/heads/" + release.CHANNEL_BRANCH)
    matches = [ref for ref in refs if ref["ref"] == "refs/heads/" + release.CHANNEL_BRANCH]
    release.need(len(matches) <= 1, "ambiguous update branch")
    return matches[0]["object"]["sha"] if matches else None


def validate_assets(directory, version, key, source_commit):
    state = json.loads((directory / "release-state.json").read_bytes())
    release.need(state["version"] == version and state["build"] == release.build_number(version) and
                 state["source_commit"] == source_commit and state["public_key"] == key and
                 state["signing_mode"] == "ad-hoc" and state["notarized"] is False and
                 state["upgrade_gate"] == "passed", "release provenance/gate mismatch")
    assets = directory / "assets"
    required = {release.asset_name(version), "appcast.xml", "SHA256SUMS"}
    release.need({p.name for p in assets.iterdir()} == required and
                 all(p.is_file() and not p.is_symlink() for p in assets.iterdir()),
                 "unexpected public asset set")
    archive = assets / release.asset_name(version)
    feed = release.read_appcast((assets / "appcast.xml").read_bytes(), key)
    release.need(feed["version"] == version and feed["signature"] == state["signature"] and
                 feed["length"] == archive.stat().st_size and
                 feed["sha256"] == bundle.digest(archive) == state["archive_sha256"],
                 "release archive/feed mismatch")
    expected = "".join(f"{bundle.digest(p)}  {p.name}\n" for p in (archive, assets / "appcast.xml"))
    release.need((assets / "SHA256SUMS").read_text(encoding="utf-8") == expected, "checksum file mismatch")
    release.verify_zip(archive)
    with zipfile.ZipFile(archive) as payload:
        info = plistlib.loads(payload.read(release.APP_NAME + "/Contents/Info.plist"))
    release.need(set(info) <= release.PLIST_KEYS and info == release.release_info(info, version, key),
                 "signed application version/channel differs from the release")
    return state, assets


def publish(directory, version, key):
    release.pinned_public_key(key)
    release.need(os.environ.get("GITHUB_ACTIONS") == "true", "publishing requires the gated Actions job")
    source = bundle.run(["git", "-C", bundle.ROOT, "rev-parse", "HEAD"])
    repo = api("")
    release.need(os.environ.get("GITHUB_REF") == "refs/heads/" + repo["default_branch"],
                 "publishing is restricted to the repository default branch")
    state, assets = validate_assets(directory, version, key, source)
    # Verify EdDSA again after Actions artifact transport, independently of SHA-256.
    signer = directory / "release-sign"
    bundle.run(["/usr/bin/xcrun", "swiftc", bundle.HERE / "ReleaseSign.swift", "-o", signer])
    release.need(bundle.run([signer, "verify", key, assets / release.asset_name(version),
                             state["signature"]]) == "verified", "publication EdDSA verification failed")
    head = channel_head()
    if head:
        previous_file = api("contents/appcast.xml?ref=" + head)
        previous = release.read_appcast(base64.b64decode(previous_file["content"]), key)
        release.need(previous["version"] == state["previous_version"] and
                     release.release_version(version) > release.release_version(previous["version"]),
                     "channel advanced since validation; rebuild instead of overwriting it")
    else:
        release.need(state["previous_version"] is None, "published channel unexpectedly disappeared")
    tag = "macos-v" + version
    refs = api("git/matching-refs/tags/" + tag)
    release.need(not any(ref["ref"] == "refs/tags/" + tag for ref in refs),
                 "release tag already exists; never overwrite a published version")
    notes = (
        f"CC Translate for macOS {version}\n\n"
        "Apple Silicon, macOS 14 or later. Download the application ZIP, extract it, "
        "and move CC Translate.app to Applications. Updates are checked only when requested "
        "and authenticated with the persistent Sparkle Ed25519 key.\n\n"
        "This release has an ad-hoc code signature, NOT an Apple Developer ID signature, "
        "and is NOT notarized. Gatekeeper may block first launch; use macOS Privacy & Security "
        "to review the app if you trust this source. Do not disable Gatekeeper globally. "
        "Accessibility and Screen Recording permissions may need to be re-granted after updates; "
        "TCC identity continuity is not guaranteed.\n\n"
        "SHA256SUMS covers the ZIP and appcast. No Developer ID, notarization, or TCC approval is claimed."
    )
    created = api("releases", {
        "tag_name": tag, "target_commitish": source, "name": f"CC Translate for macOS {version}",
        "body": notes, "draft": True, "prerelease": False, "make_latest": "false",
    })
    bundle.run(["gh", "release", "upload", tag, "--repo", release.REPOSITORY,
                *[assets / name for name in sorted(p.name for p in assets.iterdir())]])
    uploaded = api(f"releases/{created['id']}")
    release.need({item["name"] for item in uploaded["assets"]} ==
                 {release.asset_name(version), "appcast.xml", "SHA256SUMS"},
                 "draft assets incomplete; leave the draft unpublished")
    api(f"releases/{created['id']}", {"draft": False, "make_latest": "true"}, method="PATCH")
    # Public versioned URLs must serve exactly the signed bytes before the feed can reference them.
    check = directory / "published-verification"
    check.mkdir()
    for name in (release.asset_name(version), "appcast.xml", "SHA256SUMS"):
        target = check / name
        release.download(f"https://github.com/{release.REPOSITORY}/releases/download/{tag}/{name}",
                         target, bundle.MAX_ARCHIVE_BYTES)
        release.need(bundle.digest(target) == bundle.digest(assets / name), "published asset verification failed")
    blob = api("git/blobs", {
        "content": base64.b64encode((assets / "appcast.xml").read_bytes()).decode(), "encoding": "base64",
    })
    tree = api("git/trees", {
        "tree": [{"path": "appcast.xml", "mode": "100644", "type": "blob", "sha": blob["sha"]}],
    })
    commit = api("git/commits", {
        "message": f"Publish macOS {version} appcast",
        "tree": tree["sha"], "parents": [head] if head else [],
    })
    if head:
        # A non-fast-forward response is fatal; force updates could roll users backward.
        result = subprocess.run([
            "gh", "api", "--method", "PATCH",
            f"repos/{release.REPOSITORY}/git/refs/heads/{release.CHANNEL_BRANCH}", "--input", "-"
        ], input=json.dumps({"sha": commit["sha"], "force": False}), text=True, capture_output=True, check=True)
        advanced = json.loads(result.stdout)
    else:
        advanced = api("git/refs", {"ref": "refs/heads/" + release.CHANNEL_BRANCH, "sha": commit["sha"]})
    release.need(advanced["object"]["sha"] == commit["sha"], "atomic appcast promotion failed")
    final = api("contents/appcast.xml?ref=" + commit["sha"])
    release.need(base64.b64decode(final["content"]) == (assets / "appcast.xml").read_bytes(),
                 "promoted appcast readback failed")
    print(f"Published https://github.com/{release.REPOSITORY}/releases/tag/{tag}")
    print("Atomically advanced " + release.FEED_URL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    publish(args.directory, args.version, os.environ.get("MACOS_SPARKLE_PUBLIC_KEY"))


if __name__ == "__main__":
    main()
