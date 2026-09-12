"""Real catalog storage with a synthetic CLI-output boundary, never a real CLI."""

import json
from pathlib import Path

from cc_providers.codex_catalog import CatalogError, CodexModelCatalog


PAYLOAD = {"models": [
    {"slug": "synthetic", "priority": 2, "base_instructions": "Synthetic metadata only"},
    {"slug": "synthetic-small", "priority": 9, "visibility": "hide"},
]}


class SyntheticCatalog(CodexModelCatalog):
    def __init__(self, command, env, cache_dir, work_dir, *, log_error, mismatch=False):
        super().__init__(command, env, cache_dir, work_dir, log_error=log_error)
        self.calls = []
        self.mismatch = mismatch

    def _run(self, args):
        self.calls.append(tuple(args))
        if args == ["--version"]:
            return b"codex-cli 0.146.0"
        if args == ["debug", "models"]:
            return json.dumps(PAYLOAD).encode("utf-8")
        if len(args) == 4 and args[:3] == ["debug", "models", "-c"]:
            key, separator, value = args[3].partition("=")
            if key != "model_catalog_json" or not separator:
                raise CatalogError("synthetic_catalog_arguments")
            path = Path(json.loads(value))
            if not path.resolve().is_relative_to(self.cache_dir.resolve()):
                raise CatalogError("synthetic_catalog_path")
            return b'{"models":[]}' if self.mismatch else path.read_bytes()
        raise CatalogError("synthetic_catalog_arguments")


def create_catalog(root):
    try:
        Path.home()
    except RuntimeError as exc:
        raise CatalogError("synthetic_home_unavailable") from exc
    root.mkdir(parents=True)
    home = root / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        'model="synthetic"\nmodel_provider="synthetic-provider"\n', encoding="utf-8")
    binary = root / "synthetic-cli"
    binary.write_bytes(b"synthetic non-executable identity")
    work = root / "work"
    work.mkdir()
    warnings = []
    manager = SyntheticCatalog(
        str(binary), {"CODEX_HOME": str(home)}, root / "cache", str(work),
        log_error=lambda where, error: warnings.append((where, str(error))))
    return manager, warnings


def probe_catalog(root):
    manager, warnings = create_catalog(root)
    config = Path(manager.env["CODEX_HOME"]) / "config.toml"
    before = config.read_bytes()
    first = manager.overrides(native_config={"config": {}, "layers": []})
    if not first or len(manager.calls) != 3:
        raise CatalogError("synthetic_catalog_cold_read")
    catalog = Path(json.loads(first[0].split("=", 1)[1]))
    if json.loads(catalog.read_bytes()) != PAYLOAD or manager.overrides("synthetic-small") != first:
        raise CatalogError("synthetic_catalog_metadata")
    if len(manager.calls) != 3 or warnings:
        raise CatalogError("synthetic_catalog_reuse")
    other = SyntheticCatalog(
        manager.command, manager.env, manager.cache_dir, manager.work_dir, log_error=manager._log_error)
    if other.overrides() != first or len(other.calls) != 1:
        raise CatalogError("synthetic_catalog_reopen")
    invalid = SyntheticCatalog(
        manager.command, manager.env, root / "invalid", manager.work_dir,
        log_error=manager._log_error, mismatch=True)
    if invalid.overrides() or invalid.status != "catalog_roundtrip_mismatch":
        raise CatalogError("synthetic_catalog_false_activation")
    if list((root / "invalid").rglob("state.json")) or list(root.rglob(".catalog-*")):
        raise CatalogError("synthetic_catalog_transaction")
    if len(warnings) != 1 or config.read_bytes() != before:
        raise CatalogError("synthetic_catalog_config_changed")
    return {"status": "passed", "cli_simulated": True, "cache_verified": True, "reopen_verified": True}
