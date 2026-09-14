"""Cooperative stable-side-file ownership shared by explicit macOS repositories."""

import errno
import stat


def _outside_bundle(path, kind):
    if any(part.lower().endswith(".app") for part in path.parts):
        raise ValueError(kind + "_path_inside_app")


class MacFileOwner:
    """Mixin for repositories with a path, operation RLock and closed state."""

    def __init__(self, path, *, kind, in_use_error, fork_error, os_api, platform, path_type):
        if platform != "darwin":
            raise RuntimeError(kind + "_owner_requires_darwin")
        path = path_type(path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(kind + "_absolute_path_required")
        _outside_bundle(path, kind)
        parent = path.parent.resolve(strict=True)
        path = parent / path.name
        _outside_bundle(path, kind)
        if not parent.is_dir():
            raise NotADirectoryError(str(parent))
        if path.is_symlink():
            raise ValueError(kind + "_symlink_forbidden")

        import fcntl

        super().__init__(path)
        self._owner_kind = kind
        self._fork_error = fork_error
        self._os = os_api
        self._pid = os_api.getpid()
        self._fcntl = fcntl
        self._fd = None
        self._lock_path = path.with_name(path.name + ".lock")
        transferred = False
        fd = os_api.open(self._lock_path,
                         os_api.O_CREAT | os_api.O_RDWR | os_api.O_CLOEXEC | os_api.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os_api.fstat(fd).st_mode):
                raise ValueError(kind + "_lock_regular_file_required")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise in_use_error(kind + "_in_use") from error
                raise
            if path.is_symlink():
                raise ValueError(kind + "_symlink_forbidden")
            self._fd = fd
            transferred = True
        finally:
            if not transferred:
                os_api.close(fd)

    @property
    def lock_path(self):
        return self._lock_path

    def _ensure_process(self):
        if self._pid != self._os.getpid():
            raise self._fork_error(self._owner_kind + "_owner_fork_inherited")

    def _ensure_open(self):
        super()._ensure_open()
        self._ensure_process()
        if self.path.is_symlink():
            raise ValueError(self._owner_kind + "_symlink_forbidden")

    def __enter__(self):
        self._ensure_process()
        return super().__enter__()

    def close(self):
        if self._pid != self._os.getpid():
            # Drop only this child's descriptor; LOCK_UN would unlock the parent.
            fd, self._fd = self._fd, None
            self._closed = True
            if fd is not None:
                self._os.close(fd)
            raise self._fork_error(self._owner_kind + "_owner_fork_inherited")
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fd, self._fd = self._fd, None
            # Relinquish the descriptor before syscalls: failed close must not be retried.
            try:
                self._fcntl.flock(fd, self._fcntl.LOCK_UN)
            finally:
                self._os.close(fd)
