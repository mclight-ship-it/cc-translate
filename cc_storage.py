"""Explicit paths and single-file atomic writes, not a configuration/history service."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile


@dataclass(frozen=True)
class MacUserPaths:
    application_support: Path
    caches: Path


def macos_user_paths(home, application_id):
    """Resolve lexically without home lookup, filesystem access or directory creation."""
    home = Path(home)
    if not home.is_absolute() or ".." in home.parts:
        raise ValueError("explicit absolute home required")
    if any(part.lower().endswith(".app") for part in home.parts):
        raise ValueError("home must not be inside an app bundle")
    if not isinstance(application_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9.-]*", application_id):
        raise ValueError("explicit application identifier required")
    return MacUserPaths(
        home / "Library" / "Application Support" / application_id,
        home / "Library" / "Caches" / application_id,
    )


def atomic_write_json(path, data):
    """Preserve legacy JSON bytes and failure cleanup; do not create parent directories."""
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        # Retain descriptor ownership even when fdopen fails before yielding a stream.
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)
        os.replace(temporary, path)
    except Exception:
        # Keep the existing Windows primitive's original-error/best-effort cleanup contract.
        try:
            os.remove(temporary)
        except Exception:
            pass
        raise
