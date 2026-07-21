"""Win32 / ctypes helpers for CC Translate.

All the raw native calls the app needs — DPI awareness, multi-monitor work-area
geometry, rounded-window regions, the single-instance mutex — live here instead
of being scattered through translator.pyw. Keeping them in one dependency-free
module (it imports only ctypes) makes the native surface easy to find, reason
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
    acquire_single_instance_mutex(name) -> handle | None
"""

import ctypes
from ctypes import wintypes


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


def set_taskbar_presence(hwnd, present):
    """Force a borderless (overrideredirect) window into or out of the Windows
    taskbar via the WS_EX_APPWINDOW / WS_EX_TOOLWINDOW extended styles.

    Tk's overrideredirect Toplevels are owned by the (hidden) root window, and
    an owned window never gets its own taskbar button no matter what ex-style it
    carries. So when ``present=True`` we both clear the owner and set
    WS_EX_APPWINDOW, giving the result popup a real taskbar button the user can
    always click back to. ``present=False`` sets WS_EX_TOOLWINDOW to keep helper
    dialogs out of the taskbar. Ex-style changes only take effect the next time
    the window is shown, so call this while the window is withdrawn/hidden,
    before deiconify."""
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
            setf(ctypes.c_void_p(hwnd), GWLP_HWNDPARENT, ctypes.c_ssize_t(0))
        else:
            ex = (ex | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        setf(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ctypes.c_ssize_t(ex))
    except Exception:
        pass


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
