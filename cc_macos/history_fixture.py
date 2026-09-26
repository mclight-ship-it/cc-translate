"""Opt-in synthetic history check; the caller owns the supplied temporary home."""

from pathlib import Path
import sys

from cc_storage import macos_user_paths
from cc_macos.history_owner import MacHistoryOwner


def probe_history(home, application_id):
    if sys.platform != "darwin":
        raise RuntimeError("history_owner_requires_darwin")
    paths = macos_user_paths(home, application_id)
    # Resolve existing directory aliases before creating anything.
    macos_user_paths(Path(home).resolve(strict=True), application_id)
    directory = paths.application_support.resolve(strict=False)
    if any(part.lower().endswith(".app") for part in directory.parts):
        raise ValueError("history_path_inside_app")
    paths.application_support.mkdir(parents=True, exist_ok=False)
    destination = directory / "synthetic history \u4e2d # %.json"
    with MacHistoryOwner(destination) as owner:
        owner.add("synthetic input", "synthetic output \u4e2d", False, 5,
                  kind="text", sig="synthetic-v1")
        entries = owner.load()
        if len(entries) != 1 or entries[0]["output"] != "synthetic output \u4e2d":
            raise ValueError("synthetic_history_readback_failed")
        if owner.find_cached(" synthetic input ", "text", "synthetic-v1") != "synthetic output \u4e2d":
            raise ValueError("synthetic_history_cache_failed")
    with MacHistoryOwner(destination) as owner:
        if owner.load() != entries:
            raise ValueError("synthetic_history_reopen_failed")
        owner.clear()
        if owner.load() != [] or owner.find_cached("synthetic input", "text", "synthetic-v1") is not None:
            raise ValueError("synthetic_history_clear_failed")
        lock_path = owner.lock_path
    with MacHistoryOwner(destination) as owner:
        if owner.load() != []:
            raise ValueError("synthetic_history_clear_reopen_failed")
    if set(directory.iterdir()) != {lock_path}:
        raise ValueError("synthetic_history_temporary_leak")
    return {"status": "passed", "fixture": True, "history_verified": True,
            "cache_verified": True, "clear_verified": True, "reopen_verified": True}
