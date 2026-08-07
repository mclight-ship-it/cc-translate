"""Win32 / ctypes helpers for CC Translate.

All the raw native calls the app needs — DPI awareness, multi-monitor work-area
geometry, rounded-window regions, the single-instance mutex — live here instead
of being scattered through translator.pyw. Keeping them in one dependency-free
module (it imports only standard-library modules) makes the native surface easy
to find, reason
about, and stub in tests, and shrinks the main file.

Every function degrades gracefully: if a native API is missing or fails, it
returns a safe default rather than raising, because these are best-effort
platform niceties, not core logic.

Public API used by translator.pyw:
    enable_dpi_awareness()
    get_monitor_rect(point=None) -> (left, top, right, bottom) | None
    round_apply_region(hwnd, radius)
    prefer_dwm_rounded(hwnd)
    set_taskbar_presence(hwnd, present)
    get_toplevel_hwnd(hwnd) -> hwnd
    exclude_window_from_capture(hwnd) -> bool
    acrylic_capability() -> (available: bool, reason: str)
    apply_acrylic(hwnd, tint_rgb, tint_opacity=0.4) -> bool
    remove_acrylic(hwnd) -> bool
    set_window_color_key(hwnd, rgb) -> int | None
    restore_window_exstyle(hwnd, exstyle) -> bool
    activate_foreground(hwnd) -> bool
    acquire_single_instance_mutex(name) -> handle | None
"""

import ctypes
from ctypes import wintypes
import sys
import winreg


ACRYLIC_TINT_OPACITY = 0.40

ACRYLIC_AVAILABLE = "available"
ACRYLIC_REMOTE_SESSION = "remote_session"
ACRYLIC_HIGH_CONTRAST = "high_contrast"
ACRYLIC_TRANSPARENCY_DISABLED = "transparency_disabled"
ACRYLIC_COMPOSITION_DISABLED = "composition_disabled"
ACRYLIC_UNSUPPORTED_WINDOWS = "unsupported_windows"
ACRYLIC_API_UNAVAILABLE = "api_unavailable"


class _HIGHCONTRASTW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwFlags", wintypes.DWORD),
        ("lpszDefaultScheme", wintypes.LPWSTR),
    ]


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", wintypes.DWORD),
        ("AnimationId", ctypes.c_int),
    ]


class _WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def _windows_build():
    try:
        return int(sys.getwindowsversion().build)
    except Exception:
        return 0


def _high_contrast_enabled():
    try:
        info = _HIGHCONTRASTW()
        info.cbSize = ctypes.sizeof(info)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x0042, info.cbSize, ctypes.byref(info), 0)  # SPI_GETHIGHCONTRAST
        return bool(ok and (info.dwFlags & 0x00000001))
    except Exception:
        return False


def _transparency_enabled():
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "EnableTransparency")
        return bool(value)
    except FileNotFoundError:
        return True
    except OSError:
        return True


def _composition_enabled():
    try:
        enabled = wintypes.BOOL()
        result = ctypes.windll.dwmapi.DwmIsCompositionEnabled(
            ctypes.byref(enabled))
        return result == 0 and bool(enabled.value)
    except Exception:
        return False


def acrylic_capability():
    """Return whether native real-time Acrylic can be offered on this session.

    The result is policy-aware: remote sessions, High Contrast, disabled system
    transparency, disabled DWM composition, and unsupported Windows builds are
    rejected before a window is styled. The reason is a stable code for i18n.
    """
    try:
        if ctypes.windll.user32.GetSystemMetrics(0x1000):  # SM_REMOTESESSION
            return False, ACRYLIC_REMOTE_SESSION
    except Exception:
        return False, ACRYLIC_API_UNAVAILABLE
    if _high_contrast_enabled():
        return False, ACRYLIC_HIGH_CONTRAST
    if not _transparency_enabled():
        return False, ACRYLIC_TRANSPARENCY_DISABLED
    if not _composition_enabled():
        return False, ACRYLIC_COMPOSITION_DISABLED
    # ACCENT_ENABLE_ACRYLICBLURBEHIND is available from Windows 10 1809.
    if _windows_build() < 17763:
        return False, ACRYLIC_UNSUPPORTED_WINDOWS
    try:
        if not getattr(ctypes.windll.user32, "SetWindowCompositionAttribute"):
            return False, ACRYLIC_API_UNAVAILABLE
    except Exception:
        return False, ACRYLIC_API_UNAVAILABLE
    return True, ACRYLIC_AVAILABLE


def _accent_gradient_color(tint_rgb, opacity):
    red, green, blue = (max(0, min(255, int(value))) for value in tint_rgb)
    alpha = max(0, min(255, int(round(float(opacity) * 255))))
    # AccentPolicy expects ABGR, not the more common ARGB ordering.
    return (alpha << 24) | (blue << 16) | (green << 8) | red


def _set_accent_policy(hwnd, state, tint_rgb=(0, 0, 0), opacity=0.0):
    try:
        set_attribute = ctypes.windll.user32.SetWindowCompositionAttribute
        set_attribute.argtypes = [
            wintypes.HWND, ctypes.POINTER(_WINDOWCOMPOSITIONATTRIBDATA)]
        set_attribute.restype = wintypes.BOOL
        policy = _ACCENT_POLICY(
            int(state),
            2 if state else 0,
            _accent_gradient_color(tint_rgb, opacity) if state else 0,
            0,
        )
        data = _WINDOWCOMPOSITIONATTRIBDATA(
            19,  # WCA_ACCENT_POLICY
            ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
            ctypes.sizeof(policy),
        )
        return bool(set_attribute(
            wintypes.HWND(get_toplevel_hwnd(hwnd)), ctypes.byref(data)))
    except Exception:
        return False


def apply_acrylic(hwnd, tint_rgb, tint_opacity=ACRYLIC_TINT_OPACITY):
    """Apply native, DWM-composited Acrylic to a top-level HWND.

    No screenshots or polling are involved: movement and resize are composed by
    Windows in real time. Callers must paint backdrop areas black, as required
    by the classic Win32 client-area backdrop path.
    """
    available, _ = acrylic_capability()
    if not available:
        return False
    top = get_toplevel_hwnd(hwnd)
    try:
        margins = _MARGINS(-1, -1, -1, -1)
        if ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                wintypes.HWND(top), ctypes.byref(margins)) != 0:
            return False
    except Exception:
        return False
    # ACCENT_ENABLE_ACRYLICBLURBEHIND = 4.
    if _set_accent_policy(top, 4, tint_rgb, tint_opacity):
        return True
    remove_acrylic(top)
    return False


def remove_acrylic(hwnd):
    """Remove a previously applied backdrop and restore normal client painting."""
    top = get_toplevel_hwnd(hwnd)
    accent_ok = _set_accent_policy(top, 0)
    try:
        margins = _MARGINS(0, 0, 0, 0)
        frame_ok = (
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                wintypes.HWND(top), ctypes.byref(margins)) == 0)
    except Exception:
        frame_ok = False
    return bool(accent_ok and frame_ok)


def set_window_color_key(hwnd, rgb=(0, 0, 0)):
    """Make one child HWND's exact RGB background transparent to its parent.

    Returns the previous extended style so callers can restore it on failure,
    or ``None`` when Windows rejects the operation. Unlike Tk's top-level
    ``-transparentcolor``, this is intentionally applied to child widgets: it
    reveals the parent HWND's DWM Acrylic while preserving non-key text pixels.
    """
    try:
        user32 = ctypes.windll.user32
        get_style = (
            getattr(user32, "GetWindowLongPtrW", None)
            or user32.GetWindowLongW)
        set_style = (
            getattr(user32, "SetWindowLongPtrW", None)
            or user32.SetWindowLongW)
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        set_layered = user32.SetLayeredWindowAttributes
        set_layered.argtypes = [
            wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
        set_layered.restype = wintypes.BOOL
        handle = wintypes.HWND(int(hwnd))
        old_style = int(get_style(handle, -20))  # GWL_EXSTYLE
        if not set_style(handle, -20, old_style | 0x00080000):
            # SetWindowLongPtr returns the previous value, which can legitimately
            # be zero. A zero return is therefore not itself a failure.
            pass
        red, green, blue = (max(0, min(255, int(v))) for v in rgb)
        colorref = red | (green << 8) | (blue << 16)
        if not set_layered(handle, colorref, 255, 0x00000001):  # LWA_COLORKEY
            set_style(handle, -20, old_style)
            return None
        return old_style
    except Exception:
        return None


def restore_window_exstyle(hwnd, exstyle):
    """Restore an HWND extended style saved by :func:`set_window_color_key`."""
    try:
        user32 = ctypes.windll.user32
        set_style = (
            getattr(user32, "SetWindowLongPtrW", None)
            or user32.SetWindowLongW)
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        set_style(wintypes.HWND(int(hwnd)), -20, ctypes.c_ssize_t(int(exstyle)))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# DPI awareness
# ---------------------------------------------------------------------------
def enable_dpi_awareness():
    """Declare per-monitor DPI awareness so Windows doesn't bitmap-stretch
    (blur) our tkinter windows on high-DPI / scaled displays."""
    try:
        # Prefer Per-Monitor V2 when available: it gives better scaling
        # behavior for IME/composition UI than older awareness modes.
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Multi-monitor work-area geometry
# ---------------------------------------------------------------------------
def get_monitor_rect(point=None):
    """Return (left, top, right, bottom) work area of the monitor containing
    `point` (an (x, y) screen coord); defaults to the mouse cursor's monitor.
    Falls back to None if the query fails.

    tkinter's winfo_screenwidth/height only report the PRIMARY monitor, so on
    a multi-monitor setup its bounds are wrong for a point on a secondary
    screen and would shove the popup back onto the primary display."""
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

        pt = POINT()
        if point is None:
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        else:
            pt.x, pt.y = int(point[0]), int(point[1])
        # MONITOR_DEFAULTTONEAREST = 2
        hmon = ctypes.windll.user32.MonitorFromPoint(pt, 2)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork  # work area excludes the taskbar
            return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Rounded-window regions (used by the borderless-window rounding machinery in
# translator.pyw, which owns the Tk event wiring and keeps its own registry)
# ---------------------------------------------------------------------------
def round_apply_region(hwnd, radius):
    """Clip the window to a rounded rectangle matching its *current* real size."""
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return
        r = max(0, int(radius))
        user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_bool]
        user32.SetWindowRgn.restype = ctypes.c_int
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, r * 2, r * 2)
        if rgn:
            # SetWindowRgn takes ownership of the region handle.
            user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn),
                                True)
    except Exception:
        pass


def prefer_dwm_rounded(hwnd):
    """Ask Windows 11's DWM to prefer rounded corners too (harmless elsewhere)."""
    try:
        dwmapi = ctypes.windll.dwmapi
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        pref = ctypes.c_int(DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def set_taskbar_presence(hwnd, present, *, detach_owner=True):
    """Force a borderless (overrideredirect) window into or out of the Windows
    taskbar via the WS_EX_APPWINDOW / WS_EX_TOOLWINDOW extended styles.

    Tk's overrideredirect Toplevels are owned by the (hidden) root window, and
    an owned window never gets its own taskbar button no matter what ex-style it
    carries. So when ``present=True`` we both clear the owner and set
    WS_EX_APPWINDOW, giving the result popup a real taskbar button the user can
    always click back to. Some Tk windows (notably transparent rounded cards)
    need their owner preserved for stable coordinate/focus behavior; pass
    ``detach_owner=False`` for that case. ``present=False`` sets
    WS_EX_TOOLWINDOW to keep helper dialogs out of the taskbar. Ex-style changes
    only take effect the next time the window is shown, so call this while the
    window is withdrawn/hidden, before deiconify."""
    try:
        GWL_EXSTYLE = -20
        GWLP_HWNDPARENT = -8
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        user32 = ctypes.windll.user32
        getf = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        setf = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        getf.restype = ctypes.c_ssize_t
        getf.argtypes = [ctypes.c_void_p, ctypes.c_int]
        setf.restype = ctypes.c_ssize_t
        setf.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        ex = getf(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
        if present:
            ex = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
            # Detach from the owner so the taskbar will grant a button.
            if detach_owner:
                setf(ctypes.c_void_p(hwnd), GWLP_HWNDPARENT,
                     ctypes.c_ssize_t(0))
        else:
            ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        setf(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ctypes.c_ssize_t(ex))
    except Exception:
        pass


def get_toplevel_hwnd(hwnd):
    """Return the real top-level OS window for a Tk child HWND.

    Tk wraps every Toplevel in an inner frame window, so ``winfo_id()`` is that
    inner frame — NOT the window that actually carries the taskbar / topmost /
    activation styles and that the window manager treats as the top-level. That
    real window is the frame's root ancestor. Manipulating activation or Z-order
    on the inner frame silently no-ops, which is why a summoned borderless
    window could feel 'stuck on top'. Always resolve to the ancestor first."""
    try:
        GA_ROOT = 2
        top = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)
        return top or hwnd
    except Exception:
        return hwnd


def exclude_window_from_capture(hwnd):
    """Exclude a top-level window from screen/window capture.

    Returns True only when Windows accepted an exclusion affinity. Callers that
    render sensitive desktop pixels should fail closed when this returns False.
    """
    try:
        user32 = ctypes.windll.user32
        set_affinity = user32.SetWindowDisplayAffinity
        set_affinity.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        set_affinity.restype = wintypes.BOOL
        top = get_toplevel_hwnd(hwnd)
        # WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+). WDA_MONITOR is the
        # compatible fallback and renders the window blank in captured output.
        if set_affinity(ctypes.c_void_p(top), 0x00000011):
            return True
        return bool(set_affinity(ctypes.c_void_p(top), 0x00000001))
    except Exception:
        return False


def activate_foreground(hwnd):
    """Make ``hwnd`` the true foreground/active window and return whether it
    ended up foreground.

    Windows' foreground lock normally lets a background process only *raise* a
    window, not *activate* it, leaving a summoned borderless window in a
    'top-but-not-active' state: it floats above everything, yet clicking another
    app won't send it behind until this window itself is clicked once. That is
    exactly the 'still force-topmost' feeling users report.

    The standard, side-effect-free workaround: briefly zero the foreground lock
    timeout, attach our input thread to the current foreground thread so the OS
    treats the activation as user-driven, call SetForegroundWindow/SetActiveWindow,
    then restore everything. No synthetic keystrokes (which can pop app menus)."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
        SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
        SPIF_SENDCHANGE = 0x2
        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        cur_tid = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        old = ctypes.c_uint(0)
        user32.SystemParametersInfoW(
            SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(old), 0)
        user32.SystemParametersInfoW(
            SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), SPIF_SENDCHANGE)
        attached = False
        try:
            if fg_tid and fg_tid != cur_tid:
                attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_tid, cur_tid, False)
            user32.SystemParametersInfoW(
                SPI_SETFOREGROUNDLOCKTIMEOUT, 0,
                ctypes.c_void_p(old.value), SPIF_SENDCHANGE)
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Single-instance guard
# ---------------------------------------------------------------------------
def acquire_single_instance_mutex(name="Local\\CCTranslate.SingleInstance"):
    """Return a process-lifetime Win32 mutex handle, or None if another
    instance already holds it. On any failure we fail *open* (return a dummy
    object) rather than block startup."""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                          wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.restype = wintypes.DWORD

        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return object()
        # ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == 183:
            kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        # If mutex API is unavailable, fail open rather than block startup.
        return object()
