"""Explicit, cooperative macOS ownership of a single history repository."""

import errno
import os
from pathlib import Path
import stat
import sys

from cc_history import HistoryRepository


class HistoryInUseError(RuntimeError):
    pass


class HistoryForkError(RuntimeError):
    pass


def _outside_bundle(path):
    if any(part.lower().endswith(".app") for part in path.parts):
        raise ValueError("history_path_inside_app")


class MacHistoryOwner(HistoryRepository):
    def __init__(self, path):
        if sys.platform != "darwin":
            raise RuntimeError("history_owner_requires_darwin")
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("history_absolute_path_required")
        _outside_bundle(path)
        parent = path.parent.resolve(strict=True)
        path = parent / path.name
        _outside_bundle(path)
        if not parent.is_dir():
            raise NotADirectoryError(str(parent))
        if path.is_symlink():
            raise ValueError("history_symlink_forbidden")

        import fcntl

        super().__init__(path)
        self._pid = os.getpid()
        self._fcntl = fcntl
        self._fd = None
        self._lock_path = path.with_name(path.name + ".lock")
        transferred = False
        fd = os.open(self._lock_path,
                     os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("history_lock_regular_file_required")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise HistoryInUseError("history_in_use") from error
                raise
            if path.is_symlink():
                raise ValueError("history_symlink_forbidden")
            self._fd = fd
            transferred = True
        finally:
            if not transferred:
                os.close(fd)

    @property
    def lock_path(self):
        return self._lock_path

    def _ensure_process(self):
        if self._pid != os.getpid():
            raise HistoryForkError("history_owner_fork_inherited")

    def _ensure_open(self):
        super()._ensure_open()
        self._ensure_process()
        if self.path.is_symlink():
            raise ValueError("history_symlink_forbidden")

    # Check before acquiring an RLock that a vanished parent thread may have held.
    def load(self):
        self._ensure_process()
        return super().load()

    def add(self, input_text, output_text, is_dict, limit, is_code=False, kind=None, sig=None):
        self._ensure_process()
        return super().add(input_text, output_text, is_dict, limit, is_code, kind, sig)

    def find_cached(self, text, kind, sig):
        self._ensure_process()
        return super().find_cached(text, kind, sig)

    def clear(self):
        self._ensure_process()
        return super().clear()

    def __enter__(self):
        self._ensure_process()
        return super().__enter__()

    def close(self):
        if self._pid != os.getpid():
            # Drop only this child's descriptor; LOCK_UN would unlock the parent.
            fd, self._fd = self._fd, None
            self._closed = True
            if fd is not None:
                os.close(fd)
            raise HistoryForkError("history_owner_fork_inherited")
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fd, self._fd = self._fd, None
            # Relinquish the descriptor before syscalls: failed close must not be retried.
            try:
                self._fcntl.flock(fd, self._fcntl.LOCK_UN)
            finally:
                os.close(fd)
