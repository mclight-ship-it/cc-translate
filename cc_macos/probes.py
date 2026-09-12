"""Explicit runtime probes: no UI, TCC, user configuration or model calls."""

from __future__ import annotations

import http.client
from pathlib import Path
import platform
import sqlite3
import ssl
import sys
import tempfile
from threading import Event


HTTPS_HOST = "www.python.org"
BUNDLE_CORE = Path(__file__).resolve().parent.parent
BUNDLE_CA = BUNDLE_CORE / "cacert.pem"


class ProbeError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ProbeCancelled(RuntimeError):
    pass


def _check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise ProbeCancelled()


def runtime_probe(*, https: bool, cancel: Event) -> dict:
    _check_cancel(cancel)
    try:
        with tempfile.TemporaryDirectory(prefix="cc-translate-probe-") as directory:
            database = Path(directory) / "probe.sqlite3"
            connection = sqlite3.connect(database)
            try:
                with connection:
                    connection.execute("CREATE TABLE probe (value TEXT NOT NULL)")
                    connection.execute("INSERT INTO probe VALUES (?)", ("fixture-\u4e2d",))
                row = connection.execute("SELECT value FROM probe").fetchone()
                if row != ("fixture-\u4e2d",):
                    raise ProbeError("sqlite_readback_failed")
            finally:
                connection.close()
    except (OSError, sqlite3.Error) as exc:
        raise ProbeError("sqlite_probe_failed") from exc
    _check_cancel(cancel)
    bundled = BUNDLE_CA.is_file()
    if https and not bundled:
        raise ProbeError("bundle_ca_missing")
    try:
        context = ssl.create_default_context(cafile=str(BUNDLE_CA) if bundled else None)
    except (OSError, ssl.SSLError) as exc:
        raise ProbeError("ssl_context_failed") from exc
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise ProbeError("ssl_validation_disabled")
    runtime_root = BUNDLE_CORE.parent.parent / "Helpers" / "python"
    report = {
        "python": {
            "version": platform.python_version(), "platform": sys.platform,
            "machine": platform.machine(), "isolated": bool(sys.flags.isolated),
            "bytecode_disabled": sys.dont_write_bytecode,
            "bundle_runtime": Path(sys.executable).resolve().is_relative_to(runtime_root.resolve()),
        },
        "sqlite": {"status": "passed", "read_write": True, "version": sqlite3.sqlite_version},
        "ssl": {"status": "passed", "version": ssl.OPENSSL_VERSION,
                "certificate_validation": True, "ca_source": "bundle" if bundled else "system"},
        "https": {"status": "not_run"},
    }
    if not https:
        return report
    _check_cancel(cancel)
    # No redirects or proxy environment: this sends no user content to one fixed host.
    connection = http.client.HTTPSConnection(HTTPS_HOST, timeout=8, context=context)
    try:
        connection.request("HEAD", "/", headers={"User-Agent": "CCTranslate-P0-runtime-probe"})
        response = connection.getresponse()
        if not 200 <= response.status < 400:
            raise ProbeError("https_status_failed")
    except ssl.SSLCertVerificationError as exc:
        raise ProbeError("https_certificate_failed") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ProbeError("https_probe_failed") from exc
    finally:
        connection.close()
    _check_cancel(cancel)
    report["https"] = {"status": "passed", "host": HTTPS_HOST, "certificate_verified": True}
    return report
