"""Host-testable Darwin config dispatch and single-owner cleanup contracts."""

import errno
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cc_providers.codex_config import CODEX_CONFIG_OVERRIDES, CodexConfigError, read_native_config
from cc_providers import codex_config_darwin as native


class TestDarwinConfigContract(unittest.TestCase):
    def setUp(self):
        signals = patch.object(native, "signal", SimpleNamespace(SIGTERM=15, SIGKILL=9))
        signals.start()
        self.addCleanup(signals.stop)

    def test_dispatch_preserves_native_environment_arguments_and_result(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {"CODEX_HOME": directory, "SYNTHETIC_NATIVE_SETTING": "opaque"}
            result = {"config": {"model_provider": "synthetic"}, "layers": ["synthetic"]}
            with patch("cc_providers.codex_config.sys.platform", "darwin"), \
                    patch.object(native, "read_config", return_value=result) as read, \
                    patch("cc_providers.codex_config.subprocess.Popen") as popen:
                self.assertIs(read_native_config("synthetic-codex", environment, directory), result)
            args, passed_env, cwd = read.call_args.args
            self.assertEqual(args[:3], ["synthetic-codex", "app-server", "--strict-config"])
            self.assertEqual(args[3:], [part for item in CODEX_CONFIG_OVERRIDES for part in ("-c", item)])
            self.assertIs(passed_env, environment)
            self.assertEqual(cwd, directory)
            self.assertEqual(read.call_args.kwargs, {"cancel_event": None})
            popen.assert_not_called()

    def test_no_host_library_fallback(self):
        with patch("ctypes.CDLL") as load:
            with self.assertRaisesRegex(CodexConfigError, "config_runtime_unavailable"):
                native._load_supervision()
            load.assert_not_called()

    def test_setup_os_error_has_only_a_fixed_diagnostic(self):
        with patch.object(native, "_ConfigSession", side_effect=OSError("SYNTHETIC_PRIVATE_SETUP")):
            with self.assertRaises(CodexConfigError) as error:
                native.read_config(["synthetic"], {}, "unused")
        self.assertEqual(str(error.exception), "config_probe_unavailable")

    def test_wrong_bridge_abi_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            contents = Path(directory) / "Synthetic.app/Contents"
            core = contents / "Resources/Core/cc_providers"
            core.mkdir(parents=True)
            library = contents / "Helpers/python/lib/libCCProcessSupport.dylib"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"synthetic-not-a-library")
            bridge = Mock()
            bridge.cc_process_support_abi.return_value = 2
            with patch.object(native, "__file__", str(core / "codex_config_darwin.py")), \
                    patch("ctypes.CDLL", return_value=bridge):
                with self.assertRaisesRegex(CodexConfigError, "config_runtime_unavailable"):
                    native._load_supervision()

    def session(self, signal_error=0, wait_error=None):
        session = native._ConfigSession.__new__(native._ConfigSession)
        session.closed = False
        session.selector = Mock()
        session.bridge = Mock()
        session.process = Mock(pid=12345)
        calls = []
        session.bridge.cc_cli_signal_group.side_effect = (
            lambda pid, sig: (calls.append((pid, sig)), signal_error)[1])
        def wait(timeout):
            calls.append("wait")
            if wait_error is not None:
                raise wait_error
            return -9
        session.process.wait.side_effect = wait
        return session, calls

    def test_cleanup_signals_before_reap_and_is_idempotent(self):
        session, calls = self.session()
        with patch.object(native.time, "sleep"):
            session.close()
            session.close()
        self.assertEqual(calls, [(12345, 15), (12345, 9), "wait"])
        session.process.poll.assert_not_called()
        session.process.stdin.close.assert_called_once()
        session.process.stdout.close.assert_called_once()
        session.selector.close.assert_called_once()

    def test_permission_failure_is_not_suppressed(self):
        session, _ = self.session(signal_error=errno.EPERM)
        with patch.object(native.time, "sleep"):
            with self.assertRaisesRegex(CodexConfigError, "config_probe_cleanup_failed"):
                session.close()
        session.process.stdin.close.assert_called_once()
        session.process.stdout.close.assert_called_once()

    def test_wait_failure_still_closes_both_pipes(self):
        session, _ = self.session(wait_error=subprocess.TimeoutExpired("synthetic", 2))
        with patch.object(native.time, "sleep"):
            with self.assertRaisesRegex(CodexConfigError, "config_probe_cleanup_failed"):
                session.close()
        session.process.stdin.close.assert_called_once()
        session.process.stdout.close.assert_called_once()

    def test_lost_child_ownership_never_signals_or_reaps_again(self):
        session, calls = self.session(signal_error=errno.ECHILD)
        with patch.object(native.time, "sleep"):
            with self.assertRaisesRegex(CodexConfigError, "config_probe_cleanup_failed"):
                session.close()
        self.assertEqual(calls, [(12345, 15)])
        session.process.wait.assert_not_called()
        self.assertEqual(session.process.returncode, -1)

    def test_pre_cancelled_darwin_config_does_not_launch(self):
        cancel = threading.Event()
        cancel.set()
        with patch("cc_providers.codex_config.sys.platform", "darwin"), \
                patch.object(native, "read_config") as read, \
                patch("cc_providers.codex_config.os.makedirs") as create:
            with self.assertRaisesRegex(CodexConfigError, "config_probe_cancelled"):
                read_native_config("synthetic", {}, "unused", cancel_event=cancel)
            read.assert_not_called()
            create.assert_not_called()

    def test_legacy_platform_does_not_silently_ignore_new_cancel_option(self):
        with patch("cc_providers.codex_config.sys.platform", "win32"):
            with self.assertRaisesRegex(CodexConfigError, "config_cancel_unsupported"):
                read_native_config("synthetic", {}, "unused", cancel_event=threading.Event())
