"""Explicit-path configuration I/O, without home lookup or process ownership."""

import json
import threading

from cc_config import Config, coerce_config, plan_config_migration
from cc_storage import atomic_write_json


class ConfigFormatError(ValueError):
    pass


class _StrictConfig(Config):
    def _coerce(self):
        coerce_config(self, strict=True)


def normalize_config(data=None):
    """Return a strict view without I/O, retaining Config's nested-value identity rules."""
    return _StrictConfig(data)


class ConfigRepository:
    """Serialize operations without caching values or excluding other processes.

    ``load`` returns a fresh Config subclass using the shared strict field
    conversions: failed conversions propagate instead of falling back to
    defaults. Optional decoder/validator callbacks run under the same lock,
    before any migration write. The default JSON read behavior is unchanged.
    Reading, planning and writing exceptions propagate.

    ``save`` writes a detached JSON snapshot of the supplied dict, not a
    normalized Config, and returns None. JSON-serializable tuples/keys follow
    Python's JSON conversion rules. Non-finite floats remain allowed, matching
    the shared JSON writer/reader; Config may reject them in integer fields on
    a subsequent load. Callers must not mutate input during snapshot creation.
    """

    def __init__(self, path, *, lock=None):
        self._path = path
        self._lock = threading.RLock() if lock is None else lock
        self._closed = False

    @property
    def path(self):
        return self._path

    def _ensure_open(self):
        if self._closed:
            raise RuntimeError("config_repository_closed")

    def load(self, *, validate=None, decode=None):
        with self._lock:
            self._ensure_open()
            try:
                stream = open(self.path, "r", encoding="utf-8")
            except FileNotFoundError:
                cfg = normalize_config()
                if validate is not None:
                    validate(cfg)
                return cfg
            with stream:
                raw = json.load(stream) if decode is None else decode(stream)
            if not isinstance(raw, dict):
                raise ConfigFormatError("config_object_required")
            cfg = normalize_config(raw)
            changed, payload = plan_config_migration(raw, cfg)
            if validate is not None:
                validate(cfg)
            if changed:
                atomic_write_json(self.path, payload)
            return cfg

    def save(self, config):
        with self._lock:
            self._ensure_open()
            if not isinstance(config, dict):
                raise ConfigFormatError("config_object_required")
            snapshot = json.loads(json.dumps(config, ensure_ascii=False, allow_nan=True))
            atomic_write_json(self.path, snapshot)

    def close(self):
        with self._lock:
            self._closed = True

    def __enter__(self):
        with self._lock:
            self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
