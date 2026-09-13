"""Explicit synthetic storage diagnostic; the caller owns the temporary home lifecycle."""

import json

from cc_storage import atomic_write_json, macos_user_paths


def probe_storage(home, application_id):
    paths = macos_user_paths(home, application_id)
    payloads = ({"synthetic": "\u4e2d # %", "items": [1, False, None]},
                {"synthetic": "replacement", "items": []})
    for directory in (paths.application_support, paths.caches):
        directory.mkdir(parents=True)
        destination = directory / "synthetic # %.json"
        for payload in payloads:
            atomic_write_json(destination, payload)
            with destination.open(encoding="utf-8") as stream:
                if json.load(stream) != payload:
                    raise ValueError("synthetic_storage_readback_failed")
        if set(directory.iterdir()) != {destination}:
            raise ValueError("synthetic_storage_temporary_leak")
