import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch
from threading import Event

import cc_macos
from cc_macos.catalog_fixture import PAYLOAD, SyntheticCatalog, create_catalog, probe_catalog
from cc_macos.probes import ProbeError, runtime_probe
from cc_providers.codex_catalog import CodexModelCatalog


class CatalogStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "synthetic # % catalogs"
        self.manager, self.warnings = create_catalog(self.root)

    def state_path(self):
        paths = list(self.manager.cache_dir.rglob("state.json"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def test_real_write_reuse_and_reopen_preserve_metadata_and_identity(self):
        first = self.manager.overrides()
        self.assertEqual(len(self.manager.calls), 3)
        state = json.loads(self.state_path().read_bytes())
        binary = Path(self.manager.command)
        home = self.root / "home"
        self.assertEqual(state["identity"], {
            "schema": 1, "binary": str(binary.resolve()), "size": binary.stat().st_size,
            "mtime_ns": binary.stat().st_mtime_ns, "home": str(home.resolve()),
            "config_sha256": hashlib.sha256((home / "config.toml").read_bytes()).hexdigest(),
            "native_cache_sha256": None,
        })
        self.assertEqual(self.state_path().parent.name, hashlib.sha256(
            json.dumps(state["identity"], sort_keys=True).encode()).hexdigest())
        catalog = Path(json.loads(first[0].split("=", 1)[1]))
        self.assertEqual(json.loads(catalog.read_bytes()), PAYLOAD)
        self.assertEqual(self.manager.overrides("synthetic-small"), first)
        self.assertEqual(len(self.manager.calls), 3)
        other = SyntheticCatalog(
            self.manager.command, self.manager.env, self.manager.cache_dir,
            self.manager.work_dir, log_error=Mock())
        self.assertEqual(other.overrides(), first)
        self.assertEqual(len(other.calls), 1)
        self.assertEqual(other.calls[0][:3], ("debug", "models", "-c"))

    def test_expired_cache_rebuilds_and_config_and_native_cache_change_keys(self):
        with patch("cc_providers.codex_catalog.time.time", return_value=100_000):
            self.assertTrue(self.manager.overrides())
        with patch("cc_providers.codex_catalog.time.time", return_value=200_000):
            self.assertTrue(self.manager.overrides())
        self.assertEqual(len(self.manager.calls), 6)
        original = self.state_path().parent.name
        with (self.root / "home" / "config.toml").open("a", encoding="utf-8") as target:
            target.write("# synthetic identity change\n")
        self.assertTrue(self.manager.overrides())
        (self.root / "home" / "models_cache.json").write_bytes(b'{"synthetic":true}')
        self.assertTrue(self.manager.overrides())
        keys = {path.parent.name for path in self.manager.cache_dir.rglob("state.json")}
        self.assertEqual(len(keys), 3)
        self.assertIn(original, keys)
        self.assertEqual(len(self.manager.calls), 12)

    def test_roundtrip_failure_never_activates_or_retries_during_backoff(self):
        self.manager.mismatch = True
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.status, "catalog_roundtrip_mismatch")
        self.assertFalse(list(self.manager.cache_dir.rglob("state.json")))
        self.assertIsNone(self.manager._validated)
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(len(self.manager.calls), 3)
        self.assertEqual(self.warnings, [(
            "codex_catalog", "catalog_roundtrip_mismatch; using native Codex model discovery")])
        self.assertFalse(list(self.root.rglob(".catalog-*")))

    def test_project_config_guard_is_not_bypassed_by_explicit_cache(self):
        project = Path(self.manager.work_dir) / ".codex"
        project.mkdir()
        config = project / "config.toml"
        config.write_bytes(b'model_provider="synthetic-project"\n')
        before = config.read_bytes()
        self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(self.manager.status, "project_config_not_managed")
        self.assertEqual(self.manager.calls, [])
        self.assertFalse(self.manager.cache_dir.exists())
        self.assertEqual(config.read_bytes(), before)

    def test_failed_state_replace_preserves_previous_state_and_cleans_temporary(self):
        self.assertTrue(self.manager.overrides())
        state_path = self.state_path()
        previous = state_path.read_bytes()
        original_replace = os.replace

        def replace(source, destination):
            if Path(destination) == state_path:
                raise PermissionError("synthetic private detail")
            return original_replace(source, destination)

        with patch("cc_providers.codex_catalog.time.time", return_value=0), \
                patch("cc_providers.codex_catalog.os.replace", side_effect=replace):
            self.assertEqual(self.manager.overrides(), ())
        self.assertEqual(state_path.read_bytes(), previous)
        self.assertEqual(self.manager.status, "PermissionError")
        self.assertFalse(list(self.root.rglob(".catalog-*")))
        self.assertNotIn("synthetic private detail", repr(self.warnings))

    def test_explicit_logger_errors_propagate_and_invalid_callback_is_rejected(self):
        with self.assertRaises(TypeError):
            CodexModelCatalog("synthetic", log_error=False)
        manager = CodexModelCatalog("synthetic", cache_dir=self.root / "cache",
                                    log_error=Mock(side_effect=RuntimeError("synthetic log failure")))
        with self.assertRaisesRegex(RuntimeError, "synthetic log failure"):
            manager._warn("synthetic")

    def test_missing_platform_home_reports_fixed_failure(self):
        with patch("cc_macos.catalog_fixture.Path.home", side_effect=RuntimeError("synthetic private detail")):
            with self.assertRaises(ProbeError) as raised:
                runtime_probe(https=False, cancel=Event())
        self.assertEqual(str(raised.exception), "catalog_fixture_failed")

    def test_default_path_and_legacy_logger_still_work(self):
        for environment, expected in [
            ({"APPDATA": str(self.root / "appdata")}, self.root / "appdata"),
            ({}, self.root / "expanded-home"),
        ]:
            with self.subTest(environment=bool(environment)), \
                    patch.dict(os.environ, environment, clear=True), \
                    patch("os.path.expanduser", return_value=str(self.root / "expanded-home")):
                logger = Mock()
                manager = CodexModelCatalog("synthetic", env={"APPDATA": "ignored"})
                self.assertEqual(manager.cache_dir, expected / "CC Translate" / "codex-catalogs")
                with patch.dict(sys.modules, {"cc_core": Mock(log_error=logger)}):
                    manager._warn("synthetic")
                    manager._warn("synthetic")
                self.assertEqual(logger.call_count, 1)
                self.assertFalse(manager.cache_dir.exists())

    def test_explicit_probe_isolated_from_legacy_imports_processes_network_and_external_writes(self):
        core = Path(cc_macos.__file__).resolve().parent.parent
        script = textwrap.dedent(r"""
            import builtins, json, os, pathlib, sys
            sys.path.insert(0, sys.argv[1])
            root = pathlib.Path(sys.argv[2]).resolve()
            forbidden = root.parent / "forbidden-appdata"
            os.environ["APPDATA"] = str(forbidden)
            os.environ["LOCALAPPDATA"] = str(forbidden)
            original_import = builtins.__import__
            def guarded(name, *args, **kwargs):
                if name.split(".")[0] in {"cc_core", "tkinter", "win32api", "win32gui"}:
                    raise AssertionError("legacy import")
                return original_import(name, *args, **kwargs)
            builtins.__import__ = guarded
            def check(path):
                if not isinstance(path, int):
                    assert pathlib.Path(os.fsdecode(path)).resolve().is_relative_to(root), "external write"
            def audit(event, args):
                if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"}:
                    raise AssertionError("process or network activity")
                if event == "open" and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                    check(args[0])
                if event in {"os.mkdir", "os.remove", "os.rmdir"}:
                    check(args[0])
                if event == "os.rename":
                    check(args[0]); check(args[1])
            sys.addaudithook(audit)
            from cc_macos.catalog_fixture import probe_catalog
            report = probe_catalog(root)
            assert "cc_core" not in sys.modules
            assert not forbidden.exists()
            print(json.dumps(report))
        """)
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(core), str(self.root / "isolated")],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), probe_catalog(self.root / "local"))
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
