"""Session-bound offline lookup and staged installation; no provider or network path."""

import json
from pathlib import Path
import re
import sqlite3
import stat
import threading
import uuid

from cc_classify import is_single_word
from cc_config import CFG
from cc_dictionary_artifact_core import DictionaryArtifact, DictionaryArtifactError, DictionaryArtifactManager
from cc_dictionary_artifact_core import DictionaryDownloadCancelled
from cc_dictionary_lookup import LocalDictionary
from cc_dictionary_presentation import PLAIN_FORMATTER_VERSION, format_dictionary_plain
from cc_dictionary_store import DictionaryStoreError
from cc_result_rules import local_cache_signature
from .configuration import ConfigurationError
from .history import HistoryError, MAX_HISTORY_TEXT_BYTES
from .protocol import MAX_TEXT_BYTES, ProtocolError


DICTIONARY_OPERATIONS = (
    "dictionary_status", "dictionary_lookup", "dictionary_prepare_install",
    "dictionary_install", "dictionary_discard_install", "dictionary_delete",
)
DICTIONARY_FAILURE_CODES = {
    "invalid_dictionary", "invalid_dictionary_ticket", "dictionary_unavailable",
    "dictionary_io_failed", "dictionary_install_failed", "dictionary_output_limit", "dictionary_busy",
    "dictionary_cleanup_failed",
}
MAX_TICKETS = 8
_TICKET = re.compile(r"[0-9a-f]{32}", re.ASCII)


class DictionaryError(ConfigurationError):
    pass


class DictionaryCancelled(Exception):
    pass


def validate_dictionary_request(payload):
    operation = payload.get("operation")
    if operation not in DICTIONARY_OPERATIONS:
        raise ProtocolError("invalid_dictionary")
    fields = {"operation"}
    if operation == "dictionary_lookup":
        fields |= {"text", "app_language", "origin", "use_cache", "record_history"}
    elif operation in ("dictionary_install", "dictionary_discard_install"):
        fields.add("ticket")
    if set(payload) != fields:
        raise ProtocolError("invalid_dictionary")
    if operation == "dictionary_lookup":
        text = payload["text"]
        if (type(text) is not str or not text.strip() or payload["app_language"] not in ("en_US", "zh_CN")
                or payload["origin"] not in ("text", "selection") or type(payload["use_cache"]) is not bool
                or type(payload["record_history"]) is not bool):
            raise ProtocolError("invalid_dictionary")
        try:
            if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ProtocolError("invalid_dictionary")
        except UnicodeError:
            raise ProtocolError("invalid_dictionary") from None
    elif "ticket" in fields:
        if type(payload["ticket"]) is not str or not _TICKET.fullmatch(payload["ticket"]):
            raise ProtocolError("invalid_dictionary_ticket")


class DictionaryService:
    def __init__(self, configuration, application_directory):
        self.configuration = configuration
        self.directory = Path(application_directory) / "dictionary"
        self.manager = DictionaryArtifactManager(str(self.directory), DictionaryArtifact())
        self._lock = threading.RLock()
        self._tickets = {}
        self._store = None
        self._identity = None
        self._closed = False

    @staticmethod
    def _finish(cancel, begin_finish):
        if cancel.is_set() or not begin_finish():
            raise DictionaryCancelled()

    def _paths(self, *, create=False):
        try:
            parent = self.directory.parent.resolve(strict=True)
        except RuntimeError:
            raise DictionaryError("dictionary_unavailable") from None
        if (parent != self.directory.parent
                or self.directory.is_symlink()
                or any(part.lower().endswith(".app") for part in self.directory.parts)):
            raise DictionaryError("dictionary_unavailable")
        if create:
            self.directory.mkdir(exist_ok=True, mode=0o700)
        if self.directory.exists() and not self.directory.is_dir():
            raise DictionaryError("dictionary_unavailable")

    def _file_identity(self):
        self._paths()
        try:
            info = Path(self.manager.path).lstat()
        except FileNotFoundError:
            return None
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)

    def _close_store_thread(self):
        if self._store is not None:
            try:
                self._store.close_thread()
            except (DictionaryStoreError, sqlite3.Error, OSError):
                raise DictionaryError("dictionary_cleanup_failed") from None

    def _invalidate(self):
        self._close_store_thread()
        self._store = None
        self._identity = None

    def _load_store(self):
        identity = self._file_identity()
        if identity != self._identity:
            self._invalidate()
            self._identity = identity
        if identity is None:
            return "not_installed"
        if not stat.S_ISREG(identity[-1]) or identity[2] != self.manager.artifact.size:
            return "invalid"
        if self._store is None:
            self._store = LocalDictionary(self.manager.path, self.manager.artifact.sha256)
        if self._file_identity() != identity:
            self._invalidate()
            raise DictionaryError("dictionary_unavailable")
        status = self._store.status
        return "ready" if status.available and status.data_version == self.manager.artifact.data_version else "invalid"

    def _status(self, config):
        state = self._load_store()
        pin = self.manager.artifact
        return {
            "state": state, "enabled": bool(config[CFG.LOCAL_DICTIONARY_ENABLED]),
            "size": pin.size, "sha256": pin.sha256, "data_version": pin.data_version,
            "download_url": pin.url, "entry_count": self._store.status.entry_count if state == "ready" else 0,
        }

    def _enable(self, enabled):
        with self.configuration._operations_lock:
            config = self.configuration.dictionary_config()
            config[CFG.LOCAL_DICTIONARY_ENABLED] = enabled
            self.configuration.perform({"operation": "config_save", "config": config})
            return config

    def _cleanup_ticket(self, ticket):
        path = self._tickets.get(ticket)
        if path is not None:
            try:
                self._paths()
                path.unlink(missing_ok=True)
            except (DictionaryError, OSError):
                raise DictionaryError("dictionary_cleanup_failed") from None
            del self._tickets[ticket]

    def _prepare(self, cancel, begin_finish):
        if len(self._tickets) >= MAX_TICKETS:
            raise DictionaryError("dictionary_busy")
        self._paths()
        ticket = uuid.uuid4().hex
        path = self.directory / (".dictionary-stage-" + ticket + ".sqlite3")
        if ticket in self._tickets:
            raise DictionaryError("dictionary_busy")
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise DictionaryError("dictionary_busy")
        self._finish(cancel, begin_finish)
        self._paths(create=True)
        # Reserve the absent path for the native producer to create exclusively.
        self._tickets[ticket] = path
        pin = self.manager.artifact
        return {"ticket": ticket, "path": str(path), "url": pin.url, "size": pin.size,
                "sha256": pin.sha256, "data_version": pin.data_version}

    def _install(self, ticket, cancel, begin_finish):
        path = self._tickets.get(ticket)
        if path is None:
            raise DictionaryError("invalid_dictionary_ticket")
        self._paths()
        committing = False
        def reserve_commit():
            nonlocal committing
            committing = begin_finish()
            return committing
        try:
            self.manager.install_staged(str(path), cancel, begin_commit=reserve_commit)
        except DictionaryDownloadCancelled:
            raise
        except DictionaryArtifactError:
            raise DictionaryError("dictionary_io_failed" if committing else "dictionary_install_failed") from None
        self._invalidate()
        config = self._enable(True)
        return self._status(config)

    def _lookup(self, payload, cancel, begin_finish):
        config = self.configuration.dictionary_config()
        text = payload["text"]
        language = config.get(CFG.LANGUAGE) or payload["app_language"]
        if (config[CFG.MAX_CHARS] < 1 or len(text) > config[CFG.MAX_CHARS]
                or language not in ("en_US", "zh_CN")):
            raise DictionaryError("invalid_dictionary")
        outcome = ("ineligible" if not is_single_word(text)
                   else "disabled" if not config[CFG.LOCAL_DICTIONARY_ENABLED] else None)
        if outcome is None:
            if self._load_store() != "ready":
                outcome = "unavailable"
            else:
                result = self._store.lookup(text)
                if self._file_identity() != self._identity:
                    self._invalidate()
                    raise DictionaryError("dictionary_unavailable")
                if result is None or not result.is_high_confidence:
                    outcome = "miss"
        if outcome is not None:
            self._finish(cancel, begin_finish)
            return {"status": outcome, "result": None}
        signature = local_cache_signature(self._store.cache_version, PLAIN_FORMATTER_VERSION + ":" + language)
        cached = None
        history_error = None
        if payload["use_cache"] and config[CFG.HISTORY_ENABLED]:
            with self.configuration._operations_lock:
                if self.configuration.dictionary_config()[CFG.HISTORY_ENABLED]:
                    try:
                        cached = self.configuration._history.find_cached(text, "dict", signature)
                    except HistoryError as error:
                        history_error = error.code
        output = cached if cached is not None else format_dictionary_plain(result, language)
        if (type(output) is not str or not output.strip()
                or len(json.dumps(output, ensure_ascii=False).encode("utf-8")) > MAX_HISTORY_TEXT_BYTES):
            raise DictionaryError("dictionary_output_limit")
        self._finish(cancel, begin_finish)
        history = "failed" if history_error is not None else "unchanged" if cached is not None else "disabled"
        if cached is None and history_error is None and payload["record_history"]:
            with self.configuration._operations_lock:
                current = self.configuration.dictionary_config()
                if current[CFG.HISTORY_ENABLED]:
                    try:
                        self.configuration.perform_history({
                            "operation": "history_add", "input": text, "output": output,
                            "is_dict": True, "is_code": False, "kind": "dict", "sig": signature,
                            "limit": current[CFG.HISTORY_LIMIT],
                        }, "dictionary-record", 2)
                    except ConfigurationError as error:
                        history, history_error = "failed", error.code
                    else:
                        history = "recorded"
        return {"status": "hit", "result": {
            "text": output, "submitted": False, "cached": cached is not None, "kind": "dict",
            "target_lang": None, "summarize": False, "history": history, "history_error": history_error,
        }}

    def perform(self, payload, cancel, begin_finish):
        with self._lock:
            if self._closed:
                raise DictionaryError("dictionary_unavailable")
            validate_dictionary_request(payload)
            operation = payload["operation"]
            try:
                try:
                    if cancel.is_set():
                        raise DictionaryCancelled()
                    if operation == "dictionary_lookup":
                        return self._lookup(payload, cancel, begin_finish)
                    if operation == "dictionary_status":
                        result = self._status(self.configuration.dictionary_config())
                        self._finish(cancel, begin_finish)
                        return result
                    if operation == "dictionary_prepare_install":
                        return self._prepare(cancel, begin_finish)
                    if operation == "dictionary_install":
                        return self._install(payload["ticket"], cancel, begin_finish)
                    if operation == "dictionary_discard_install":
                        if payload["ticket"] not in self._tickets:
                            raise DictionaryError("invalid_dictionary_ticket")
                        self._finish(cancel, begin_finish)
                        self._cleanup_ticket(payload["ticket"])
                        return {"discarded": True}
                    self._paths()
                    self._finish(cancel, begin_finish)
                    self._invalidate()
                    deleted = self.manager.delete()
                    self._enable(False)
                    return {"deleted": deleted, "enabled": False}
                finally:
                    if operation == "dictionary_install":
                        try:
                            self._invalidate()
                        finally:
                            self._cleanup_ticket(payload["ticket"])
                    self._close_store_thread()
            except DictionaryDownloadCancelled:
                raise DictionaryCancelled() from None
            except DictionaryArtifactError:
                code = "dictionary_install_failed" if operation == "dictionary_install" else "dictionary_io_failed"
                raise DictionaryError(code) from None
            except (DictionaryStoreError, sqlite3.Error):
                raise DictionaryError("dictionary_unavailable") from None
            except (OSError, UnicodeError):
                raise DictionaryError("dictionary_io_failed") from None

    def close(self):
        with self._lock:
            self._closed = True
            error_code = None
            try:
                self._invalidate()
            except DictionaryError as error:
                error_code = error.code
            for ticket in tuple(self._tickets):
                try:
                    self._cleanup_ticket(ticket)
                except DictionaryError as error:
                    error_code = error.code
            if error_code is not None:
                raise DictionaryError(error_code)
