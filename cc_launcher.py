"""Build the small branded Windows launcher used to host CC Translate.

CC Translate remains a source-based Python app.  The launcher is a local copy of
the active ``pythonw.exe`` with its VERSIONINFO and icon resources replaced, so
Task Manager shows the app identity instead of the interpreter's generic Python
name and icon.
"""

import ctypes
from ctypes import wintypes
import os
import shutil
import struct


FILE_DESCRIPTION = "CC Translate"
LAUNCHER_PREFIX = "CCTranslate-"
ORIGINAL_FILENAME = "CCTranslate.exe"
_RT_VERSION = 16
_RT_ICON = 3
_RT_GROUP_ICON = 14
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


def _set_resources(executable, resources):
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

    handle = begin(os.fspath(executable), False)
    if not handle:
        raise _win_error("BeginUpdateResourceW failed")
    buffers = []
    try:
        for resource_type, resource_id, language, payload in resources:
            buffer = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
            buffers.append(buffer)
            if not update(
                    handle, ctypes.c_void_p(resource_type),
                    ctypes.c_void_p(resource_id), language, buffer,
                    len(payload)):
                raise _win_error(
                    f"UpdateResourceW failed for {resource_type}:{resource_id}")
    except Exception:
        end(handle, True)
        raise
    if not end(handle, False):
        raise _win_error("EndUpdateResourceW failed")


def set_version_resource(executable, version):
    """Replace ``executable``'s RT_VERSION resource using only Win32 APIs."""
    _set_resources(executable, (
        (_RT_VERSION, 1, _LANG_EN_US, build_version_resource(version)),
    ))


def build_icon_resources(icon_path):
    """Return a group-icon payload and the RT_ICON images from an ICO file."""
    with open(icon_path, "rb") as source:
        data = source.read()
    if len(data) < 6:
        raise ValueError("ICO file is truncated")
    reserved, image_type, count = struct.unpack_from("<HHH", data)
    if reserved != 0 or image_type != 1 or not count:
        raise ValueError("ICO file has an invalid header")
    directory_end = 6 + count * 16
    if directory_end > len(data):
        raise ValueError("ICO file has a truncated image directory")

    group = bytearray(struct.pack("<HHH", reserved, image_type, count))
    images = []
    for index in range(count):
        entry = struct.unpack_from("<BBBBHHII", data, 6 + index * 16)
        width, height, colors, entry_reserved, planes, bit_count, size, offset = (
            entry)
        if not size or offset < directory_end or offset + size > len(data):
            raise ValueError(f"ICO image {index + 1} is out of bounds")
        resource_id = index + 1
        group.extend(struct.pack(
            "<BBBBHHIH", width, height, colors, entry_reserved, planes,
            bit_count, size, resource_id))
        images.append((resource_id, data[offset:offset + size]))
    return bytes(group), tuple(images)


def set_icon_resources(executable, icon_path):
    """Replace the executable's primary icon with every ICO image size."""
    group, images = build_icon_resources(icon_path)
    resources = [
        (_RT_ICON, resource_id, _LANG_EN_US, payload)
        for resource_id, payload in images
    ]
    resources.append((_RT_GROUP_ICON, 1, _LANG_EN_US, group))
    _set_resources(executable, resources)


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


def _read_resource(executable, resource_type, resource_id):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    load = kernel32.LoadLibraryExW
    load.argtypes = (wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD)
    load.restype = wintypes.HMODULE
    find = kernel32.FindResourceW
    find.argtypes = (wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p)
    find.restype = wintypes.HANDLE
    load_resource = kernel32.LoadResource
    load_resource.argtypes = (wintypes.HMODULE, wintypes.HANDLE)
    load_resource.restype = wintypes.HANDLE
    lock = kernel32.LockResource
    lock.argtypes = (wintypes.HANDLE,)
    lock.restype = ctypes.c_void_p
    size_of = kernel32.SizeofResource
    size_of.argtypes = (wintypes.HMODULE, wintypes.HANDLE)
    size_of.restype = wintypes.DWORD
    free = kernel32.FreeLibrary
    free.argtypes = (wintypes.HMODULE,)
    free.restype = wintypes.BOOL

    module = load(os.fspath(executable), None, 0x00000002)
    if not module:
        return None
    try:
        resource = find(
            module, ctypes.c_void_p(resource_id),
            ctypes.c_void_p(resource_type))
        if not resource:
            return None
        size = size_of(module, resource)
        loaded = load_resource(module, resource)
        pointer = lock(loaded) if loaded else None
        if not size or not pointer:
            return None
        return ctypes.string_at(pointer, size)
    finally:
        free(module)


def launcher_has_icon(executable, icon_path):
    """Return whether the launcher's primary icon exactly matches the ICO."""
    group, images = build_icon_resources(icon_path)
    if _read_resource(executable, _RT_GROUP_ICON, 1) != group:
        return False
    return all(
        _read_resource(executable, _RT_ICON, resource_id) == payload
        for resource_id, payload in images
    )


def _default_icon_path():
    app_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ("cc-dark.ico", "cc.ico"):
        path = os.path.join(app_dir, name)
        if os.path.isfile(path):
            return path
    return None


def launcher_filename():
    # Windows remembers tray-icon visibility against the executable path.
    # Keep this name stable across app releases so an upgrade does not appear
    # to be a brand-new tray app and return the icon to the overflow area.
    return ORIGINAL_FILENAME


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


def ensure_branded_launcher(pythonw, launcher_dir, version, icon_path=None):
    """Create the stable-path branded host and return its path.

    The launcher is an identity shim rather than the source app itself, so an
    already valid launcher is intentionally kept across app versions. Its
    VERSIONINFO reflects the release that first created it; the live app version
    remains available inside CC Translate.
    """
    if icon_path is None:
        icon_path = _default_icon_path()
    launcher_dir = os.path.abspath(launcher_dir)
    launcher = os.path.join(launcher_dir, launcher_filename())
    if (os.path.isfile(launcher)
            and read_file_description(launcher) == FILE_DESCRIPTION
            and (not icon_path or launcher_has_icon(launcher, icon_path))):
        return launcher

    os.makedirs(launcher_dir, exist_ok=True)
    temp_path = f"{launcher}.{os.getpid()}.tmp"
    try:
        shutil.copy2(pythonw, temp_path)
        set_version_resource(temp_path, version)
        if icon_path:
            set_icon_resources(temp_path, icon_path)
        if read_file_description(temp_path) != FILE_DESCRIPTION:
            raise OSError("branded launcher metadata verification failed")
        if icon_path and not launcher_has_icon(temp_path, icon_path):
            raise OSError("branded launcher icon verification failed")
        os.replace(temp_path, launcher)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
    return launcher
