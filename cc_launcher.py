"""Build the small branded Windows launcher used to host CC Translate.

CC Translate remains a source-based Python app.  The launcher is a local copy of
the active ``pythonw.exe`` with only its VERSIONINFO resource replaced, so Task
Manager shows the product name instead of the interpreter's generic "Python".
"""

import ctypes
from ctypes import wintypes
import hashlib
import os
import re
import shutil
import struct


FILE_DESCRIPTION = "CC Translate"
LAUNCHER_PREFIX = "CCTranslate-"
ORIGINAL_FILENAME = "CCTranslate.exe"
_RT_VERSION = 16
_LANG_EN_US = 0x0409
_UNICODE_CODEPAGE = 1200


def _align4(data):
    data.extend(b"\0" * ((-len(data)) % 4))


def _utf16z(value):
    return (str(value) + "\0").encode("utf-16le")


def _resource_block(key, *, value=b"", value_length=0, value_type=0,
                    children=()):
    data = bytearray(struct.pack("<HHH", 0, value_length, value_type))
    data.extend(_utf16z(key))
    _align4(data)
    data.extend(value)
    _align4(data)
    for child in children:
        data.extend(child)
        _align4(data)
    if len(data) > 0xFFFF:
        raise ValueError("VERSIONINFO resource is too large")
    struct.pack_into("<H", data, 0, len(data))
    return bytes(data)


def _version_parts(version):
    parts = []
    for item in str(version).split("."):
        if len(parts) == 4:
            break
        try:
            value = int(item)
        except ValueError:
            value = 0
        parts.append(max(0, min(value, 0xFFFF)))
    return tuple((parts + [0, 0, 0, 0])[:4])


def build_version_resource(version):
    """Return a complete RT_VERSION payload for a branded GUI executable."""
    major, minor, build, revision = _version_parts(version)
    version_ms = (major << 16) | minor
    version_ls = (build << 16) | revision
    fixed_info = struct.pack(
        "<13I",
        0xFEEF04BD, 0x00010000,
        version_ms, version_ls,
        version_ms, version_ls,
        0x0000003F, 0,
        0x00040004, 0x00000001, 0,
        0, 0,
    )

    strings = {
        "CompanyName": FILE_DESCRIPTION,
        "FileDescription": FILE_DESCRIPTION,
        "FileVersion": version,
        "InternalName": "CCTranslate",
        "OriginalFilename": ORIGINAL_FILENAME,
        "ProductName": FILE_DESCRIPTION,
        "ProductVersion": version,
    }
    string_blocks = []
    for key, value in strings.items():
        encoded = _utf16z(value)
        string_blocks.append(_resource_block(
            key, value=encoded, value_length=len(encoded) // 2, value_type=1))
    string_table = _resource_block(
        "040904B0", value_type=1, children=string_blocks)
    string_file_info = _resource_block(
        "StringFileInfo", value_type=1, children=(string_table,))

    translation = struct.pack("<HH", _LANG_EN_US, _UNICODE_CODEPAGE)
    translation_block = _resource_block(
        "Translation", value=translation, value_length=len(translation))
    var_file_info = _resource_block(
        "VarFileInfo", value_type=1, children=(translation_block,))

    return _resource_block(
        "VS_VERSION_INFO",
        value=fixed_info,
        value_length=len(fixed_info),
        children=(string_file_info, var_file_info),
    )


def _win_error(message):
    code = ctypes.get_last_error()
    return OSError(code, f"{message}: {ctypes.FormatError(code)}")


def set_version_resource(executable, version):
    """Replace ``executable``'s RT_VERSION resource using only Win32 APIs."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    begin = kernel32.BeginUpdateResourceW
    begin.argtypes = (wintypes.LPCWSTR, wintypes.BOOL)
    begin.restype = wintypes.HANDLE
    update = kernel32.UpdateResourceW
    update.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
        ctypes.c_void_p, wintypes.DWORD,
    )
    update.restype = wintypes.BOOL
    end = kernel32.EndUpdateResourceW
    end.argtypes = (wintypes.HANDLE, wintypes.BOOL)
    end.restype = wintypes.BOOL

    payload = build_version_resource(version)
    buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    handle = begin(os.fspath(executable), False)
    if not handle:
        raise _win_error("BeginUpdateResourceW failed")
    if not update(
            handle, ctypes.c_void_p(_RT_VERSION), ctypes.c_void_p(1),
            _LANG_EN_US, buffer, len(payload)):
        error = _win_error("UpdateResourceW failed")
        end(handle, True)
        raise error
    if not end(handle, False):
        raise _win_error("EndUpdateResourceW failed")


def read_version_string(executable, key):
    """Read one English StringFileInfo value from a Windows executable."""
    version_dll = ctypes.WinDLL("version", use_last_error=True)
    get_size = version_dll.GetFileVersionInfoSizeW
    get_size.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    get_size.restype = wintypes.DWORD
    get_info = version_dll.GetFileVersionInfoW
    get_info.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
    get_info.restype = wintypes.BOOL
    query = version_dll.VerQueryValueW
    query.argtypes = (
        ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT))
    query.restype = wintypes.BOOL

    ignored = wintypes.DWORD()
    size = get_size(os.fspath(executable), ctypes.byref(ignored))
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not get_info(os.fspath(executable), 0, size, data):
        return None
    value = ctypes.c_void_p()
    length = wintypes.UINT()
    path = rf"\StringFileInfo\040904B0\{key}"
    if not query(data, path, ctypes.byref(value), ctypes.byref(length)):
        return None
    if not value.value or not length.value:
        return None
    return ctypes.wstring_at(value.value, length.value).rstrip("\0")


def read_file_description(executable):
    return read_version_string(executable, "FileDescription")


def _pythonw_fingerprint(pythonw):
    digest = hashlib.sha256()
    digest.update(os.path.normcase(os.path.abspath(pythonw)).encode("utf-8"))
    with open(pythonw, "rb") as source:
        for chunk in iter(lambda: source.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def launcher_filename(pythonw, version):
    safe_version = re.sub(r"[^0-9A-Za-z._-]+", "-", str(version)).strip("-")
    if not safe_version:
        safe_version = "0"
    return f"{LAUNCHER_PREFIX}{safe_version}-{_pythonw_fingerprint(pythonw)}.exe"


def cleanup_old_launchers(launcher_dir, current):
    try:
        names = os.listdir(launcher_dir)
    except OSError:
        return
    for name in names:
        if not (name.startswith(LAUNCHER_PREFIX) and name.endswith(".exe")):
            continue
        path = os.path.join(launcher_dir, name)
        if os.path.normcase(path) == os.path.normcase(current):
            continue
        try:
            os.remove(path)
        except OSError:
            # A previous launcher can remain locked until its process exits.
            pass


def ensure_branded_launcher(pythonw, launcher_dir, version):
    """Create the version/interpreter-specific branded host and return its path."""
    launcher_dir = os.path.abspath(launcher_dir)
    launcher = os.path.join(
        launcher_dir, launcher_filename(pythonw, version))
    if (os.path.isfile(launcher)
            and read_file_description(launcher) == FILE_DESCRIPTION
            and read_version_string(launcher, "ProductVersion") == str(version)):
        return launcher

    os.makedirs(launcher_dir, exist_ok=True)
    temp_path = f"{launcher}.{os.getpid()}.tmp"
    try:
        shutil.copy2(pythonw, temp_path)
        set_version_resource(temp_path, version)
        if read_file_description(temp_path) != FILE_DESCRIPTION:
            raise OSError("branded launcher metadata verification failed")
        os.replace(temp_path, launcher)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
    return launcher
