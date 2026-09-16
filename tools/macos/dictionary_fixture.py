"""Explicit CI dictionary fixture acquisition; not the product's URLSession downloader."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cc_dictionary_artifact_core import (
    ARTIFACT_DATA_VERSION, ARTIFACT_SHA256, ARTIFACT_SIZE, ARTIFACT_URL,
)
from tools.macos import bundle


class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        bundle.need(urllib.parse.urlsplit(newurl).scheme == "https", "dictionary fixture redirect is not HTTPS")
        return super().redirect_request(request, response, code, message, headers, newurl)


def validate_asset(path):
    path = Path(path)
    bundle.verified_asset(path, {"size": ARTIFACT_SIZE, "sha256": ARTIFACT_SHA256})
    return {
        "asset": str(path.resolve()), "size": ARTIFACT_SIZE, "sha256": ARTIFACT_SHA256,
        "data_version": ARTIFACT_DATA_VERSION, "url": ARTIFACT_URL,
        "scope": "Pinned test input only; acquisition is not product URLSession or GUI verification.",
    }


def download_asset(destination):
    """Never overwrite an existing file or include this data in the app/artifact."""
    destination = Path(destination).absolute()
    bundle.need(not destination.is_symlink(), "dictionary fixture destination is a symlink")
    if destination.exists():
        return validate_asset(destination)
    bundle.need(destination.parent.is_dir(), "dictionary fixture parent directory is missing")
    descriptor, temporary = tempfile.mkstemp(prefix=".cc-dictionary-fixture-", dir=destination.parent)
    partial = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output:
            request = urllib.request.Request(
                ARTIFACT_URL, headers={"User-Agent": "cc-translate-native-dictionary-fixture/1",
                                       "Accept-Encoding": "identity"})
            opener = urllib.request.build_opener(HTTPSRedirectHandler())
            with opener.open(request, timeout=60) as response:
                bundle.need(response.status == 200, "dictionary fixture HTTP status is not 200")
                bundle.need(urllib.parse.urlsplit(response.url).scheme == "https",
                            "dictionary fixture response is not HTTPS")
                length = response.headers.get("Content-Length")
                if length is not None:
                    bundle.need(int(length) == ARTIFACT_SIZE, "dictionary fixture declared size mismatch")
                received = 0
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    bundle.need(received <= ARTIFACT_SIZE, "dictionary fixture exceeds pinned size")
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        validate_asset(partial)
        # A hard link publishes the verified file without overwriting an intervening writer.
        os.link(partial, destination)
        return validate_asset(destination)
    finally:
        partial.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--asset", type=Path, help="Validate an already-downloaded pinned fixture.")
    selection.add_argument("--download-to", type=Path, help="Explicit external-to-bundle CI fixture path.")
    parser.add_argument("--allow-download", action="store_true")
    args = parser.parse_args(argv)
    if args.download_to is not None and not args.allow_download:
        parser.error("--download-to requires explicit --allow-download")
    if args.allow_download and args.download_to is None:
        parser.error("--allow-download requires --download-to")
    try:
        record = validate_asset(args.asset) if args.asset is not None else download_asset(args.download_to)
        print(json.dumps(record, sort_keys=True))
    except (bundle.BundleError, OSError, ValueError, urllib.error.URLError) as error:
        print("BLOCKED: dictionary test fixture: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
