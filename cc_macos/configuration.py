"""Explicit connection-owned configuration service; never initialized by diagnostic hello."""

from pathlib import Path
import sys

from cc_config import plan_config_migration
from cc_config_store import normalize_config
from cc_macos.config_owner import ConfigForkError, ConfigInUseError, MacConfigOwner
from cc_macos.protocol import MAX_FRAME_BYTES, ProtocolError, decode_frame, validate_config
from cc_storage import macos_user_paths


class ConfigurationError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def validate_save(config):
    """Validate both raw transport data and its eventual load view before any write."""
    validate_config(config)
    try:
        view = normalize_config(config)
        plan_config_migration(config, view)
        validate_config(view)
    except (ValueError, TypeError, OverflowError) as error:
        raise ProtocolError("invalid_config") from error


def decode_config_file(stream):
    raw = stream.read(MAX_FRAME_BYTES + 1).encode("utf-8") + b"\n"
    config = decode_frame(raw)
    validate_config(config)
    return config


class ConfigurationSession:
    def __init__(self, home, application_id):
        self.home = home
        self.application_id = application_id
        self._owner = None
        self._closed = False

    def open(self):
        if self._closed or self._owner is not None:
            raise ConfigurationError("config_unavailable")
        try:
            if sys.platform != "darwin":
                raise ConfigurationError("config_unavailable")
            paths = macos_user_paths(self.home, self.application_id)
            home = Path(self.home).resolve(strict=True)
            if not home.is_dir():
                raise ConfigurationError("config_unavailable")
            macos_user_paths(home, self.application_id)
            directory = paths.application_support.resolve(strict=False)
            if any(part.lower().endswith(".app") for part in directory.parts):
                raise ConfigurationError("config_unavailable")
            directory.mkdir(parents=True, exist_ok=True)
            self._owner = MacConfigOwner(self.home, self.application_id)
        except ConfigInUseError as error:
            raise ConfigurationError("config_in_use") from error
        except (OSError, ValueError, TypeError) as error:
            raise ConfigurationError("config_unavailable") from error

    def perform(self, payload):
        if self._closed or self._owner is None:
            raise ConfigurationError("config_unavailable")
        try:
            if payload["operation"] == "config_load":
                return {"config": self._owner.load(validate=validate_config, decode=decode_config_file)}
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
        self._closed = True
        owner, self._owner = self._owner, None
        if owner is not None:
            owner.close()


def startup_configuration(arguments):
    if not arguments:
        return None
    if (len(arguments) != 4 or arguments[0] != "--config-home"
            or arguments[2] != "--application-id"):
        raise ProtocolError("invalid_startup")
    try:
        macos_user_paths(arguments[1], arguments[3])
    except (ValueError, TypeError) as error:
        raise ProtocolError("invalid_startup") from error
    return ConfigurationSession(arguments[1], arguments[3])
