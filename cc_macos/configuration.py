"""Explicit config/history connection ownership; never initialized by diagnostic hello."""

from pathlib import Path
from copy import deepcopy
import json
import os
import sys
import threading

from cc_config import DEFAULT_CONFIG, plan_config_migration
from cc_config_store import normalize_config
from cc_macos.config_owner import ConfigForkError, ConfigInUseError, MacConfigOwner
from cc_macos.history import HistoryError, HistoryService
from cc_macos.protocol import MAX_FRAME_BYTES, ProtocolError, decode_frame, validate_config
from cc_storage import macos_user_paths


MAX_CONFIG_FILE_BYTES = MAX_FRAME_BYTES - 1


class ConfigurationError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.submitted = False


def _resolve(path, *, strict):
    try:
        return Path(path).resolve(strict=strict)
    except RuntimeError as error:
        # Python 3.12 reports symlink loops as RuntimeError, including the private path.
        raise ConfigurationError("config_unavailable") from error


def validate_stored_config(config):
    """Keep the unchanged indent=2 writer's bytes within the decoder's LF-framed budget."""
    validate_config(config)
    encoded = json.dumps(config, ensure_ascii=False, indent=2).replace("\n", os.linesep).encode("utf-8")
    if len(encoded) > MAX_CONFIG_FILE_BYTES:
        raise ProtocolError("invalid_config")


def validate_save(config):
    """Preflight raw storage, the exact future migration payload and the returned view."""
    validate_stored_config(config)
    try:
        view = normalize_config(config)
        changed, payload = plan_config_migration(config, view)
        validate_config(view)
        if changed:
            validate_stored_config(payload)
    except (ValueError, TypeError, OverflowError) as error:
        raise ProtocolError("invalid_config") from error


def decode_config_file(stream):
    raw = stream.read(MAX_FRAME_BYTES + 1).encode("utf-8") + b"\n"
    config = decode_frame(raw)
    validate_config(config)
    return config


class ConfigurationSession:
    dictionary_enabled = True

    def __init__(self, home, application_id):
        self.home = home
        self.application_id = application_id
        self._owner = None
        self._history = None
        self._dictionary = None
        self._closed = False
        self._operations_lock = threading.RLock()

    def open(self):
        if self._closed or self._owner is not None:
            raise ConfigurationError("config_unavailable")
        opened = False
        try:
            if sys.platform != "darwin":
                raise ConfigurationError("config_unavailable")
            paths = macos_user_paths(self.home, self.application_id)
            home = _resolve(self.home, strict=True)
            if not home.is_dir():
                raise ConfigurationError("config_unavailable")
            macos_user_paths(home, self.application_id)
            directory = _resolve(paths.application_support, strict=False)
            if any(part.lower().endswith(".app") for part in directory.parts):
                raise ConfigurationError("config_unavailable")
            directory.mkdir(parents=True, exist_ok=True)
            self._owner = MacConfigOwner(self.home, self.application_id)
            self._history = HistoryService(directory)
            from .dictionary import DictionaryService
            self._dictionary = DictionaryService(self, directory)
            opened = True
        except ConfigInUseError as error:
            raise ConfigurationError("config_in_use") from error
        except HistoryError as error:
            raise ConfigurationError(error.code) from error
        except (OSError, ValueError, TypeError) as error:
            raise ConfigurationError("config_unavailable") from error
        finally:
            if not opened:
                try:
                    self.close()
                except OSError as error:
                    raise ConfigurationError("state_io_failed") from error

    def perform_history(self, payload, request_id, sequence):
        with self._operations_lock:
            return self._perform_history(payload, request_id, sequence)

    def _perform_history(self, payload, request_id, sequence):
        if self._closed or self._history is None:
            raise ConfigurationError("history_unavailable")
        try:
            return self._history.perform(payload, request_id, sequence)
        except HistoryError as error:
            raise ConfigurationError(error.code) from error

    def perform(self, payload):
        with self._operations_lock:
            return self._perform(payload)

    def dictionary_config(self):
        """Capture the normalized view without committing a migration before cancellation."""
        with self._operations_lock:
            if self._closed or self._owner is None:
                raise ConfigurationError("config_unavailable")
            try:
                self._owner._ensure_open()
                try:
                    stream = open(self._owner.path, "r", encoding="utf-8")
                except FileNotFoundError:
                    raw = {}
                else:
                    with stream:
                        raw = decode_config_file(stream)
                config = normalize_config(raw)
                plan_config_migration(raw, config)
                validate_config(config)
                return config
            except OSError:
                raise ConfigurationError("config_io_failed") from None
            except (ValueError, TypeError, OverflowError):
                raise ConfigurationError("invalid_config") from None
            except ConfigForkError:
                raise ConfigurationError("config_unavailable") from None

    def perform_dictionary(self, payload, cancel, begin_finish):
        if self._dictionary is None:
            raise ConfigurationError("dictionary_unavailable")
        return self._dictionary.perform(payload, cancel, begin_finish)

    def _perform(self, payload):
        if self._closed or self._owner is None:
            raise ConfigurationError("config_unavailable")
        try:
            if payload["operation"] == "config_load":
                if payload.get("defaults") is True:
                    self._owner._ensure_open()
                    defaults = deepcopy(DEFAULT_CONFIG)
                    validate_config(defaults)
                    return {"config": defaults}
                return {"config": self._owner.load(
                    validate=validate_config, decode=decode_config_file,
                    validate_migration=validate_stored_config)}
            if payload["operation"] == "config_save":
                validate_save(payload["config"])
                self._owner.save(payload["config"])
                return {"saved": True}
            raise ConfigurationError("invalid_config")
        except OSError as error:
            raise ConfigurationError("config_io_failed") from error
        except (ValueError, TypeError, OverflowError) as error:
            raise ConfigurationError("invalid_config") from error
        except ConfigForkError as error:
            raise ConfigurationError("config_unavailable") from error

    def close(self):
        try:
            if self._dictionary is not None:
                self._dictionary.close()
        finally:
            with self._operations_lock:
                self._close()

    def _close(self):
        self._closed = True
        owner, self._owner = self._owner, None
        history, self._history = self._history, None
        try:
            if history is not None:
                history.close()
        finally:
            if owner is not None:
                owner.close()


def startup_configuration(arguments, *, environment=None):
    if not arguments:
        return None
    if (len(arguments) not in (4, 6) or arguments[0] != "--config-home"
            or arguments[2] != "--application-id"):
        raise ProtocolError("invalid_startup")
    try:
        macos_user_paths(arguments[1], arguments[3])
    except (ValueError, TypeError) as error:
        raise ProtocolError("invalid_startup") from error
    if len(arguments) == 6:
        providers = {"--codex-command": "codex_cli", "--claude-command": "claude_cli"}
        if arguments[4] not in providers:
            raise ProtocolError("invalid_startup")
        from .translation import TranslationSession, parse_cli_environment

        provider_id = providers[arguments[4]]
        cli_environment = parse_cli_environment(environment, arguments[1], provider_id)
        command = arguments[5]
        if (not isinstance(command, str) or not os.path.isabs(command) or "\0" in command
                or ".." in Path(command).parts):
            raise ProtocolError("invalid_startup")
        return TranslationSession(arguments[1], arguments[3], command, cli_environment, provider_id=provider_id)
    return ConfigurationSession(arguments[1], arguments[3])
