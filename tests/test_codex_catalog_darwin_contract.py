"""Host regressions for catalog dispatch, fatal failures and cancellation wiring."""

import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from cc_macos.catalog_fixture import create_catalog, PAYLOAD
from cc_providers.base import ProviderRequest
from cc_providers.codex_catalog import CatalogProbeError, CodexModelCatalog, _MAX_BYTES
from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES
from cc_providers.codex_cli import CodexCliProvider
from cc_providers.codex_appserver import CodexAppServerTransport
from cc_providers.darwin_process import ProcessError


class TestDarwinCatalogContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "synthetic"
        storage, self.warnings = create_catalog(self.root)
        self.manager = CodexModelCatalog(
            storage.command, storage.env, storage.cache_dir, storage.work_dir,
            log_error=storage._log_error)

    def test_darwin_dispatch_preserves_arguments_environment_and_budgets(self):
        event = threading.Event()
        self.manager._probe_cancel = event
        with patch("cc_providers.codex_catalog.sys.platform", "darwin"), \
                patch("cc_providers.darwin_process.capture_output", return_value=b"synthetic") as capture, \
                patch("cc_providers.codex_catalog.subprocess.run") as legacy:
            self.assertEqual(self.manager._run(["--version"]), b"synthetic")
        capture.assert_called_once_with(
            [self.manager.command, "--version",
             *[part for value in CODEX_CONFIG_OVERRIDES for part in ("-c", value)]],
            self.manager.env, self.manager.work_dir,
            cancel_event=event, timeout=8, max_bytes=_MAX_BYTES)
        legacy.assert_not_called()

    def test_legacy_path_retains_subprocess_and_never_loads_bridge(self):
        with patch("cc_providers.codex_catalog.sys.platform", "win32"), \
                patch("cc_providers.codex_catalog.subprocess.run",
                      return_value=subprocess.CompletedProcess([], 0, b"legacy", b"")) as legacy, \
                patch("cc_providers.darwin_process.load_supervision") as load:
            self.assertEqual(self.manager._run(["--version"]), b"legacy")
            with self.assertRaisesRegex(CatalogProbeError, "catalog_cancel_unsupported"):
                self.manager.overrides(cancel_event=threading.Event())
        self.assertEqual(legacy.call_args.kwargs["timeout"], 8)
        self.assertTrue(legacy.call_args.kwargs["capture_output"])
        load.assert_not_called()

    def test_supervision_failures_propagate_without_native_fallback_or_cache_activation(self):
        for code in ("runtime_unavailable", "probe_cancelled", "probe_timeout",
                     "probe_output_limit", "probe_cleanup_failed", "probe_failed"):
            with self.subTest(code=code), patch("cc_providers.codex_catalog.sys.platform", "darwin"), \
                    patch("cc_providers.darwin_process.capture_output",
                          side_effect=ProcessError(code)) as run:
                with self.assertRaisesRegex(CatalogProbeError, "^catalog_" + code + "$"):
                    self.manager.overrides(cancel_event=threading.Event())
                run.assert_called_once()
                self.assertIsNone(self.manager._probe_cancel)
                self.assertIsNone(self.manager._validated)
                self.assertEqual(self.manager._failure_until, 0)
                self.assertFalse(list(self.manager.cache_dir.rglob("state.json")))
                self.assertFalse(self.warnings)

    def test_precancel_and_cancel_waiting_for_lock_never_launch(self):
        event = threading.Event()
        event.set()
        with patch("cc_providers.codex_catalog.sys.platform", "darwin"), \
                patch("cc_providers.darwin_process.capture_output") as run:
            with self.assertRaisesRegex(CatalogProbeError, "catalog_probe_cancelled"):
                self.manager.overrides(cancel_event=event)
            event.clear()
            lock = Mock()
            lock.acquire.side_effect = lambda **_kw: (event.set(), False)[1]
            self.manager._lock = lock
            with self.assertRaisesRegex(CatalogProbeError, "catalog_probe_cancelled"):
                self.manager.overrides(cancel_event=event)
            lock.release.assert_not_called()
            run.assert_not_called()

    def test_cold_cache_reopen_and_per_request_event_do_not_change_resolution(self):
        first_event = threading.Event()
        second_event = threading.Event()
        def output(args, env, cwd, **kwargs):
            self.assertIs(kwargs["cancel_event"], first_event)
            return b"codex-cli 0.146.0" if args[1] == "--version" else json.dumps(PAYLOAD).encode()
        with patch("cc_providers.codex_catalog.sys.platform", "darwin"), \
                patch("cc_providers.darwin_process.capture_output", side_effect=output) as run:
            first = self.manager.overrides(cancel_event=first_event)
            self.assertTrue(first)
            self.assertEqual(run.call_count, 3)
            first_event.set()
            self.assertEqual(self.manager.overrides(cancel_event=second_event), first)
            self.assertEqual(run.call_count, 3)
            self.assertIsNone(self.manager._probe_cancel)

    def test_real_callers_forward_cancel_and_do_not_submit_on_fatal_catalog_failure(self):
        event = threading.Event()
        request = ProviderRequest("translate", "auto", "Synthetic.", "hello")
        provider = CodexCliProvider(self.manager.command, self.manager.work_dir)
        provider._catalog = self.manager
        transport = CodexAppServerTransport(
            self.manager.command, self.manager.work_dir, env={}, catalog=self.manager)
        with patch("cc_providers.codex_catalog.sys.platform", "darwin"), \
                patch("cc_providers.codex_cli.read_native_config", return_value={"config": {}}), \
                patch("cc_providers.codex_appserver.read_native_config", return_value={"config": {}}), \
                patch("cc_providers.codex_appserver._supported_appserver_version", return_value=True), \
                patch.object(self.manager, "overrides",
                             side_effect=CatalogProbeError("catalog_probe_cancelled")) as resolve, \
                patch("cc_providers.codex_cli.subprocess.Popen") as spawn:
            result = provider.complete(request, event)
            self.assertEqual(result.error_code, "catalog_probe_cancelled")
            self.assertIs(resolve.call_args.kwargs["cancel_event"], event)
            result = transport.stream(request, Mock(), event)
            self.assertEqual(result.error_code, "catalog_probe_cancelled")
            self.assertIs(resolve.call_args.kwargs["cancel_event"], event)
            self.assertFalse(dict(result.metrics).get("turn_submitted", False))
            result = transport.warm_up(request)
            self.assertEqual(result.error_code, "catalog_probe_cancelled")
            self.assertIs(resolve.call_args.kwargs["cancel_event"], transport._prewarm_cancel_event)
            spawn.assert_not_called()
        provider.shutdown()
        transport.shutdown()
