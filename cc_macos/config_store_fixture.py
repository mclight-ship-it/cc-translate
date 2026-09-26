"""Opt-in configuration service check using a caller-owned synthetic home."""

import json
from pathlib import Path
import sys

from cc_config import Config
from cc_storage import macos_user_paths
from cc_macos.config_owner import MacConfigOwner


def probe_config_store(home, application_id):
    if sys.platform != "darwin":
        raise RuntimeError("config_owner_requires_darwin")
    paths = macos_user_paths(home, application_id)
    macos_user_paths(Path(home).resolve(strict=True), application_id)
    directory = paths.application_support.resolve(strict=False)
    if any(part.lower().endswith(".app") for part in directory.parts):
        raise ValueError("config_path_inside_app")
    paths.application_support.mkdir(parents=True, exist_ok=False)
    raw = {"font_size": "16", "codex_streaming_experimental": False,
           "future": {"values": ["synthetic \u4e2d # %"]}}
    with MacConfigOwner(home, application_id) as owner:
        if owner.load() != Config() or owner.path.exists():
            raise ValueError("synthetic_config_missing_read_failed")
        owner.save(raw)
        raw["future"]["values"].append("caller change")
        saved = json.loads(owner.path.read_text(encoding="utf-8"))
        if saved["font_size"] != "16" or len(saved["future"]["values"]) != 1:
            raise ValueError("synthetic_config_save_snapshot_failed")
        loaded = owner.load()
        if loaded.font_size != 16 or loaded["codex_streaming_experimental"] is not True:
            raise ValueError("synthetic_config_normalization_failed")
        migrated = owner.path.read_bytes()
        inode = owner.path.stat().st_ino
        loaded["future"]["values"].append("returned view change")
        if len(owner.load()["future"]["values"]) != 1 or owner.path.stat().st_ino != inode:
            raise ValueError("synthetic_config_view_or_migration_failed")
        lock_path = owner.lock_path
        destination = owner.path
    with MacConfigOwner(home, application_id) as owner:
        if owner.load().font_size != 16 or owner.path.read_bytes() != migrated:
            raise ValueError("synthetic_config_reopen_failed")
        owner.save({"ui_v2_default_migrated": True, "ui_v2": False,
                    "labs_defaults_migrated": True, "summary_enabled": False})
        if owner.load()["ui_v2"] is not False or owner.load().summary_enabled is not False:
            raise ValueError("synthetic_config_opt_out_failed")
    if set(directory.iterdir()) != {destination, lock_path}:
        raise ValueError("synthetic_config_temporary_leak")
    return {"status": "passed", "fixture": True, "config_verified": True,
            "migration_verified": True, "snapshot_verified": True, "reopen_verified": True}
