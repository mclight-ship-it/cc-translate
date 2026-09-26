"""Explicit, cooperative macOS ownership of a single history repository."""

import os
from pathlib import Path
import sys

from cc_history import HistoryRepository
from cc_macos.file_owner import MacFileOwner


class HistoryInUseError(RuntimeError):
    pass


class HistoryForkError(RuntimeError):
    pass


class MacHistoryOwner(MacFileOwner, HistoryRepository):
    def __init__(self, path):
        super().__init__(path, kind="history", in_use_error=HistoryInUseError,
                         fork_error=HistoryForkError, os_api=os, platform=sys.platform, path_type=Path)

    # Check before acquiring an RLock that a vanished parent thread may have held.
    def load(self):
        self._ensure_process()
        return super().load()

    def add(self, input_text, output_text, is_dict, limit, is_code=False, kind=None, sig=None,
            *, preserve_null_input=False):
        self._ensure_process()
        return super().add(input_text, output_text, is_dict, limit, is_code, kind, sig,
                           **({"preserve_null_input": True} if preserve_null_input else {}))

    def find_cached(self, text, kind, sig):
        self._ensure_process()
        return super().find_cached(text, kind, sig)

    def clear(self):
        self._ensure_process()
        return super().clear()
