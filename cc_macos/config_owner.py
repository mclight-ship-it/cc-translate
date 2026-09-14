"""Explicit Application Support configuration ownership; no default home or mkdir."""

import os
from pathlib import Path
import sys

from cc_config_store import ConfigRepository
from cc_storage import macos_user_paths
from cc_macos.file_owner import MacFileOwner


class ConfigInUseError(RuntimeError):
    pass


class ConfigForkError(RuntimeError):
    pass


class MacConfigOwner(MacFileOwner, ConfigRepository):
    def __init__(self, home, application_id):
        if sys.platform != "darwin":
            raise RuntimeError("config_owner_requires_darwin")
        paths = macos_user_paths(home, application_id)
        super().__init__(paths.application_support / "config.json", kind="config",
                         in_use_error=ConfigInUseError, fork_error=ConfigForkError,
                         os_api=os, platform=sys.platform, path_type=Path)

    # A fork child must fail before entering a possibly inherited locked RLock.
    def load(self):
        self._ensure_process()
        return super().load()

    def save(self, config):
        self._ensure_process()
        return super().save(config)
