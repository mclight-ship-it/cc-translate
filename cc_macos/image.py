"""Explicit bounded PNG capture with exact transient-file ownership; no provider or default I/O."""

import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import tempfile
import zlib

from .protocol import ProtocolError


MAX_IMAGE_BYTES = 80 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_BLOCK_BYTES = 64 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)


class ImageError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class ImageCancelled(Exception):
    pass


def validate_image_request(payload):
    if set(payload) != {"operation", "image_path", "image_bytes", "image_sha256",
                        "app_language", "record_history"}:
        raise ProtocolError("invalid_image_translation")
    path, size, digest = payload["image_path"], payload["image_bytes"], payload["image_sha256"]
    if (payload["operation"] != "translate_image" or type(path) is not str
            or not os.path.isabs(path) or "\0" in path
            or type(size) is not int or size <= 0
            or type(digest) is not str or not _SHA256.fullmatch(digest)
            or payload["app_language"] not in ("en_US", "zh_CN")
            or type(payload["record_history"]) is not bool):
        raise ProtocolError("invalid_image_translation")
    try:
        path.encode("utf-8")
    except UnicodeError:
        raise ProtocolError("invalid_image_translation") from None
    if size > MAX_IMAGE_BYTES:
        raise ProtocolError("image_too_large")


def _check_cancel(cancel):
    if cancel.is_set():
        raise ImageCancelled()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def _revision(info):
    # Compare change time within each stat API: Windows lstat/fstat expose different ctime clocks.
    return _identity(info), info.st_ctime_ns


def _validate_png(fd, size, cancel):
    """Check PNG framing/CRCs without decoding pixels or imposing a color-format whitelist."""
    position, first, image_data, ended = 0, True, False, False

    def read(count):
        nonlocal position
        _check_cancel(cancel)
        if count > size - position:
            raise ImageError("image_unavailable")
        data = bytearray()
        while len(data) < count:
            _check_cancel(cancel)
            chunk = os.read(fd, count - len(data))
            if not chunk:
                raise ImageError("image_unavailable")
            data.extend(chunk)
        position += count
        return bytes(data)

    if read(8) != PNG_SIGNATURE:
        raise ImageError("image_unavailable")
    while position < size:
        length, kind = struct.unpack(">I4s", read(8))
        if length > size - position - 4 or length > 0x7fffffff:
            raise ImageError("image_unavailable")
        if first:
            if kind != b"IHDR" or length != 13:
                raise ImageError("image_unavailable")
        elif kind == b"IHDR":
            raise ImageError("image_unavailable")
        crc, remaining, header = zlib.crc32(kind), length, b""
        while remaining:
            data = read(min(remaining, _BLOCK_BYTES))
            if first:
                header += data
            crc = zlib.crc32(data, crc)
            remaining -= len(data)
        if struct.unpack(">I", read(4))[0] != crc:
            raise ImageError("image_unavailable")
        if first:
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
            depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if (not 0 < width <= 0x7fffffff or not 0 < height <= 0x7fffffff
                    or depth not in depths.get(color, ()) or compression or filtering or interlace not in (0, 1)):
                raise ImageError("image_unavailable")
        first = False
        if kind == b"IDAT":
            image_data = True
        if kind == b"IEND":
            if length or position != size:
                raise ImageError("image_unavailable")
            ended = True
    if not ended or not image_data:
        raise ImageError("image_unavailable")


class OwnedPNG:
    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_absolute():
            raise ValueError("absolute_image_root_required")
        self.path = None
        self.directory = None
        self._directory_identity = None
        self._file_identity = None
        self._fds = set()
        self._descriptor_cleanup_failed = False

    def _close_fd(self, fd):
        self._fds.remove(fd)
        try:
            os.close(fd)
        except OSError:
            self._descriptor_cleanup_failed = True
            raise ImageError("image_cleanup_failed") from None

    def prepare(self, payload, cancel):
        validate_image_request(payload)
        _check_cancel(cancel)
        if self.directory is not None or self._fds:
            raise ImageError("image_unavailable")
        source, size = payload["image_path"], payload["image_bytes"]
        try:
            before = os.lstat(source)
            if not stat.S_ISREG(before.st_mode):
                raise ImageError("image_unavailable")
            if before.st_size > MAX_IMAGE_BYTES:
                raise ImageError("image_too_large")
            if before.st_size != size:
                raise ImageError("image_changed")
            flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            source_fd = os.open(source, flags)
            self._fds.add(source_fd)
            opened = os.fstat(source_fd)
            if _identity(opened) != _identity(before):
                raise ImageError("image_changed")
            if self.root.is_symlink():
                raise ImageError("image_unavailable")
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory = Path(tempfile.mkdtemp(prefix=".cc-image-", dir=self.root))
            info = self.directory.lstat()
            self._directory_identity = (info.st_dev, info.st_ino)
            self.path = str(self.directory / "region.png")
            target_fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR
                                | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0), 0o600)
            self._fds.add(target_fd)
            info = os.fstat(target_fd)
            self._file_identity = (info.st_dev, info.st_ino)
            digest, count = hashlib.sha256(), 0
            while True:
                _check_cancel(cancel)
                data = os.read(source_fd, min(_BLOCK_BYTES, size - count + 1))
                if not data:
                    break
                count += len(data)
                if count > size:
                    raise ImageError("image_changed")
                digest.update(data)
                view = memoryview(data)
                while view:
                    _check_cancel(cancel)
                    written = os.write(target_fd, view)
                    if written <= 0:
                        raise ImageError("image_unavailable")
                    view = view[written:]
            if (count != size or digest.hexdigest() != payload["image_sha256"]
                    or _revision(os.fstat(source_fd)) != _revision(opened)
                    or _revision(os.lstat(source)) != _revision(before)):
                raise ImageError("image_changed")
            os.lseek(target_fd, 0, os.SEEK_SET)
            _validate_png(target_fd, size, cancel)
            self._close_fd(source_fd)
            self._close_fd(target_fd)
            _check_cancel(cancel)
            return self.path
        except OSError:
            raise ImageError("image_unavailable") from None

    def close(self):
        failed = self._descriptor_cleanup_failed
        for fd in tuple(self._fds):
            try:
                self._close_fd(fd)
            except ImageError:
                failed = True
        try:
            if self.directory is not None:
                try:
                    info = self.directory.lstat()
                except FileNotFoundError:
                    self.directory = None
                else:
                    if (not stat.S_ISDIR(info.st_mode)
                            or (info.st_dev, info.st_ino) != self._directory_identity):
                        raise ImageError("image_cleanup_failed")
                    if self.path is not None:
                        try:
                            info = os.lstat(self.path)
                        except FileNotFoundError:
                            pass
                        else:
                            if (not stat.S_ISREG(info.st_mode)
                                    or (info.st_dev, info.st_ino) != self._file_identity):
                                raise ImageError("image_cleanup_failed")
                            os.unlink(self.path)
                    self.directory.rmdir()
                    self.directory = None
        except OSError:
            raise ImageError("image_cleanup_failed") from None
        if failed:
            raise ImageError("image_cleanup_failed")
