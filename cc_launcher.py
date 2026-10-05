"""Build the small branded Windows launcher used to host CC Translate.

CC Translate remains a source-based Python app. A small native host loads the
real Python DLL, retaining the original interpreter/venv identity while Windows
sees the CC Translate executable. No DLL copies, artificial venv or PATH edits
are needed.
"""

import ctypes
from ctypes import wintypes
import hashlib
import os
import shutil
import struct
import sys
import tempfile


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


def _pe_identity(executable):
    """Read machine, imports and executable-code digest without loading a PE."""
    with open(executable, "rb") as source:
        data = source.read()

    def unpack(fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError("truncated Windows executable")
        return struct.unpack_from(fmt, data, offset)

    if data[:2] != b"MZ":
        raise ValueError("not a Windows executable")
    pe, = unpack("<I", 0x3C)
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("invalid Windows executable header")
    machine, section_count = unpack("<HH", pe + 4)
    optional_size, = unpack("<H", pe + 20)
    optional = pe + 24
    magic, = unpack("<H", optional)
    directories = {0x10B: 96, 0x20B: 112}.get(magic)
    if directories is None or optional_size < directories + 16:
        raise ValueError("unsupported Windows executable header")
    import_rva, import_size = unpack("<II", optional + directories + 8)
    sections = []
    code = hashlib.sha256()
    for index in range(section_count):
        section = optional + optional_size + index * 40
        _, rva, raw_size, raw_offset = unpack("<IIII", section + 8)
        flags, = unpack("<I", section + 36)
        if raw_offset + raw_size > len(data):
            raise ValueError("truncated Windows section")
        sections.append((rva, raw_size, raw_offset))
        if flags & 0x20:
            code.update(data[raw_offset:raw_offset + raw_size])

    def file_offset(rva):
        for start, size, offset in sections:
            if start <= rva < start + size:
                result = offset + rva - start
                if result < len(data):
                    return result
        raise ValueError("invalid Windows import address")

    imports = []
    for index in range(import_size // 20):
        descriptor = unpack("<IIIII", file_offset(import_rva + index * 20))
        if not any(descriptor):
            return machine, tuple(sorted(imports)), code.hexdigest()
        name_offset = file_offset(descriptor[3])
        end = data.find(b"\0", name_offset)
        if end < 0:
            raise ValueError("unterminated Windows import name")
        name = data[name_offset:end].decode("ascii").lower()
        if os.path.basename(name) != name or "/" in name or "\\" in name:
            raise ValueError("invalid Windows import name")
        imports.append(name)
    raise ValueError("unterminated Windows import table")


def _write_if_changed(path, data):
    try:
        with open(path, "rb") as current:
            if current.read() == data:
                return
    except FileNotFoundError:
        pass

    handle, temporary = tempfile.mkstemp(
        prefix=".cc-launcher-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(handle, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def current_pythonw():
    """Use the active interpreter, including Scripts/pythonw.exe in a real venv."""
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.isfile(candidate):
        return candidate
    # Migrate the old renamed-python host, which has no adjacent pythonw.exe.
    return os.path.join(sys.base_prefix, "pythonw.exe")


def _python_runtime(pythonw):
    identity = _pe_identity(pythonw)
    home = os.path.dirname(os.path.abspath(pythonw))
    python_dlls = [name for name in identity[1]
                   if name.startswith("python") and name.endswith(".dll")]
    if not python_dlls:
        configuration = os.path.join(os.path.dirname(home), "pyvenv.cfg")
        base = ""
        with open(configuration, encoding="utf-8") as source:
            for line in source:
                key, separator, value = line.partition("=")
                if separator and key.strip().lower() == "home":
                    base = value.strip()
                    break
        if not os.path.isabs(base):
            raise ValueError("virtual environment has no absolute Python home")
        base_identity = _pe_identity(os.path.join(base, "pythonw.exe"))
        if base_identity[0] != identity[0]:
            raise ValueError("virtual environment and base Python architectures differ")
        home = base
        python_dlls = [name for name in base_identity[1]
                       if name.startswith("python") and name.endswith(".dll")]
    if len(python_dlls) != 1:
        raise ValueError("cannot identify the Python runtime DLL")
    library = os.path.join(home, python_dlls[0])
    if not os.path.isfile(library):
        raise FileNotFoundError(f"Python runtime dependency is missing: {library}")
    return identity[0], library, os.path.join(home, "pythonw.exe")


def _retire_legacy_binding(launcher_dir, library):
    path = os.path.join(launcher_dir, "pyvenv.cfg")
    try:
        with open(path, encoding="utf-8") as source:
            content = source.read()
    except FileNotFoundError:
        return
    expected = (
        f"home = {os.path.dirname(library)}\n"
        "include-system-site-packages = true\n")
    if content == expected:
        # Keep a rollback copy, but remove old DLLs from Windows' app-directory
        # search path so a later Python upgrade cannot pick up stale runtimes.
        retired = tempfile.mkdtemp(prefix="retired-python-", dir=launcher_dir)
        for name in os.listdir(launcher_dir):
            lower = name.lower()
            if (lower.startswith(("python", "vcruntime"))
                    and lower.endswith(".dll")):
                os.replace(os.path.join(launcher_dir, name),
                           os.path.join(retired, name))
        os.replace(path, os.path.join(retired, "pyvenv.cfg"))


def ensure_branded_launcher(pythonw, launcher_dir, version, icon_path=None):
    """Create the stable-path branded host and return its path.

    The launcher is an identity shim rather than the source app itself, so an
    already valid launcher is intentionally kept across app versions. Its
    VERSIONINFO reflects the release that first created it; the live app version
    remains available inside CC Translate.
    """
    if icon_path is None:
        icon_path = _default_icon_path()
    pythonw = os.path.abspath(pythonw)
    launcher_dir = os.path.abspath(launcher_dir)
    launcher = os.path.join(launcher_dir, launcher_filename())
    machine, library, base_pythonw = _python_runtime(pythonw)
    architecture = {0x14C: "x86", 0x8664: "x64"}.get(machine)
    if architecture is None:
        raise OSError("no native branded host for this architecture; use the original Python")
    template = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "windows",
        f"cc-python-host-{architecture}.exe")
    identity = _pe_identity(template)
    if identity[0] != machine:
        raise ValueError("native host and Python architectures differ")
    if any(character in path for path in (pythonw, library, base_pythonw)
           for character in "\r\n"):
        raise ValueError("Python runtime paths cannot contain newlines")
    os.makedirs(launcher_dir, exist_ok=True)
    configuration = f"CC Translate Python host 1\n{pythonw}\n{library}\n{base_pythonw}\n"
    _write_if_changed(launcher + ".runtime", configuration.encode("utf-8"))
    if (os.path.isfile(launcher)
            and read_file_description(launcher) == FILE_DESCRIPTION
            and _pe_identity(launcher) == identity
            and (not icon_path or launcher_has_icon(launcher, icon_path))):
        _retire_legacy_binding(launcher_dir, library)
        return launcher

    os.makedirs(launcher_dir, exist_ok=True)
    temp_path = f"{launcher}.{os.getpid()}.tmp"
    try:
        shutil.copy2(template, temp_path)
        set_version_resource(temp_path, version)
        if icon_path:
            set_icon_resources(temp_path, icon_path)
        if read_file_description(temp_path) != FILE_DESCRIPTION:
            raise OSError("branded launcher metadata verification failed")
        if icon_path and not launcher_has_icon(temp_path, icon_path):
            raise OSError("branded launcher icon verification failed")
        os.replace(temp_path, launcher)
        _retire_legacy_binding(launcher_dir, library)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
    return launcher
