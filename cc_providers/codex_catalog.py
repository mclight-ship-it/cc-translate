"""App-owned, version-checked snapshots of Codex's effective model metadata."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import tomllib

from .codex_config import CODEX_CONFIG_OVERRIDES


SUPPORTED_CODEX_VERSIONS = {"0.146.0"}
_MAX_AGE_SECONDS = 24 * 60 * 60
_RETRY_SECONDS = 60
_MAX_BYTES = 8 * 1024 * 1024


class CatalogError(ValueError):
    pass


def _models(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise CatalogError("invalid_catalog_shape")
    entries = payload["models"]
    slugs = [m.get("slug") if isinstance(m, dict) else None for m in entries]
    if (not slugs or any(not isinstance(s, str) or not s for s in slugs)
            or len(set(slugs)) != len(slugs)):
        raise CatalogError("invalid_catalog_models")
    return set(slugs)


def _read(path):
    with path.open("rb") as source:
        content = source.read(_MAX_BYTES + 1)
    if len(content) > _MAX_BYTES:
        raise CatalogError("catalog_input_too_large")
    return content


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".catalog-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class CodexModelCatalog:
    def __init__(self, command, env=None, cache_dir=None, work_dir=None, *, log_error=None):
        if log_error is not None and not callable(log_error):
            raise TypeError("log_error must be callable")
        self.command = command
        self.env = env
        self.work_dir = work_dir
        self.cache_dir = Path(cache_dir or os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "CC Translate", "codex-catalogs"))
        self._lock = threading.Lock()
        self._failure_until = 0
        self._validated = None
        self.status = "not_checked"
        self._log_error = log_error

    def _warn(self, code):
        log_error = self._log_error
        if log_error is None:
            from cc_core import log_error

        if self.status != code:
            log_error("codex_catalog", CatalogError(
                code + "; using native Codex model discovery"))
        self.status = code

    def _run(self, args):
        args = list(args)
        for override in CODEX_CONFIG_OVERRIDES:
            args.extend(("-c", override))
        completed = subprocess.run(
            [self.command, *args], capture_output=True, timeout=8,
            env=self.env, cwd=self.work_dir,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode:
            # CLI errors can contain auth details or full response bodies.
            raise CatalogError("catalog_probe_failed")
        if len(completed.stdout) > _MAX_BYTES:
            raise CatalogError("catalog_output_too_large")
        return completed.stdout

    def overrides(self, model="auto", *, ignore_user_config=False, native_config=None):
        """Resolve only before process startup, never retry a submitted turn."""
        environment = self.env if self.env is not None else os.environ
        if environment.get("CC_TRANSLATE_CODEX_CATALOG", "").lower() == "off":
            self.status = "disabled"
            return ()
        if ignore_user_config or not self.command:
            self.status = "native"
            return ()
        if native_config is not None:
            routing = {"model", "model_provider", "model_providers",
                       "model_catalog_json", "profile", "profiles"}
            for layer in native_config.get("layers") or ():
                if (layer.get("name", {}).get("type") not in ("user", "sessionFlags")
                        and routing.intersection(layer.get("config") or {})):
                    self._warn("layered_config_not_managed")
                    return ()
        with self._lock:
            if time.monotonic() < self._failure_until:
                return ()
            try:
                return self._resolve(model, environment)
            except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired) as exc:
                code = str(exc) if isinstance(exc, CatalogError) else type(exc).__name__
                self._warn(code)
                self._failure_until = time.monotonic() + _RETRY_SECONDS
                return ()

    def _resolve(self, model, environment):
        home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
        config_path = home / "config.toml"
        if not config_path.is_file():
            self.status = "native"
            return ()
        raw_config = _read(config_path)
        config = tomllib.loads(raw_config.decode("utf-8"))
        if config.get("model_catalog_json"):
            self.status = "user_owned"
            return ()
        # Profiles/project layers can override the provider, model or catalog.
        # Leave their precedence entirely to Codex rather than guessing.
        projects = config.get("projects", {})
        trust_only = isinstance(projects, dict) and all(
            isinstance(value, dict) and set(value) == {"trust_level"}
            and value["trust_level"] in ("trusted", "untrusted")
            for value in projects.values())
        if any(config.get(k) for k in ("profile", "profiles")) or not trust_only:
            self._warn("layered_config_not_managed")
            return ()
        if config.get("model_provider", "openai") == "openai":
            self.status = "native"
            return ()
        cwd = Path(self.work_dir) if self.work_dir else Path.cwd()
        for directory in (cwd, *cwd.parents):
            if directory == Path.home():
                continue
            if (directory / ".codex" / "config.toml").is_file():
                self._warn("project_config_not_managed")
                return ()
        executable = Path(self.command)
        stat = executable.stat()
        identity = {
            "schema": 1, "binary": str(executable.resolve()),
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "home": str(home.resolve()),
            "config_sha256": hashlib.sha256(raw_config).hexdigest(),
            "native_cache_sha256": (
                hashlib.sha256(_read(home / "models_cache.json")).hexdigest()
                if (home / "models_cache.json").is_file() else None),
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        directory = self.cache_dir / key
        state_path = directory / "state.json"
        selected = config.get("model") if model in ("auto", "auto-fast", "") else model
        if selected is not None and not isinstance(selected, str):
            raise CatalogError("invalid_selected_model")
        now = time.time()
        payload = None
        if state_path.is_file():
            try:
                state = json.loads(_read(state_path))
                if not re.fullmatch(r"[0-9a-f]{64}", state["sha256"]):
                    raise CatalogError("invalid_catalog_digest")
                catalog_path = directory / ("models-" + state["sha256"] + ".json")
                content = _read(catalog_path)
                if (state["identity"] == identity
                        and state["version"] in SUPPORTED_CODEX_VERSIONS
                        and 0 <= now - state["created_at"] < _MAX_AGE_SECONDS
                        and hashlib.sha256(content).hexdigest() == state["sha256"]):
                    payload = json.loads(content)
                    _models(payload)
            except (ValueError, KeyError, TypeError, OSError):
                self._warn("invalid_cached_catalog")
        if payload is None:
            version_text = self._run(["--version"]).decode("utf-8").strip()
            version = version_text.removeprefix("codex-cli ")
            if version not in SUPPORTED_CODEX_VERSIONS:
                raise CatalogError("unsupported_catalog_version")
            # Export effective metadata, not an account entitlement list or a
            # hand-written capability table. This may refresh native discovery.
            payload = json.loads(self._run(["debug", "models"]))
            slugs = _models(payload)
            if selected and selected not in slugs:
                raise CatalogError("selected_model_not_in_catalog")
            content = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            digest = hashlib.sha256(content).hexdigest()
            catalog_path = directory / ("models-" + digest + ".json")
            _atomic_write(catalog_path, content)
            self._validate(catalog_path, payload)
            state = {
                "identity": identity, "version": version, "created_at": now,
                "sha256": digest,
            }
            _atomic_write(state_path, json.dumps(state).encode("utf-8"))
            self._validated = (key, state["sha256"])
        else:
            if selected and selected not in _models(payload):
                raise CatalogError("selected_model_not_in_catalog")
            if self._validated != (key, state["sha256"]):
                self._validate(catalog_path, payload)
                self._validated = (key, state["sha256"])
        self.status = "ready"
        return ("model_catalog_json=" + json.dumps(str(catalog_path.resolve())),)

    def _validate(self, path, expected):
        actual = json.loads(self._run([
            "debug", "models", "-c",
            "model_catalog_json=" + json.dumps(str(path.resolve())),
        ]))
        if actual != expected:
            raise CatalogError("catalog_roundtrip_mismatch")
