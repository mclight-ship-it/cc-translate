"""Route this test process's GUI onto a hidden Windows desktop.

Several UI tests build *real* Tk windows (the settings dialog, result popups,
history, …) and a handful of them assert on-screen geometry, mapped state,
focus and topmost behaviour — so they can't just be withdrawn. Left alone,
those windows flash on screen and steal keyboard focus every single time the
pre-push hook runs the suite, which is enough to interrupt whatever the user is
typing at that moment.

The fix: create a separate desktop inside the interactive window station and
switch **only this process's main thread** to it before the first Tk root is
created. Every window Tk then makes renders on that invisible desktop instead of
the one the user is looking at. The windows still map, focus and report their
real coordinates, so the geometry/reveal tests pass unchanged — but nothing
appears on, or grabs focus from, the visible desktop. We deliberately never call
SwitchDesktop, so the desktop the user sees is left completely untouched.

This is a no-op on non-Windows platforms, when disabled via the
``CC_TEST_SHOW_WINDOWS`` environment variable (set it to see the windows while
debugging a UI test), and on any failure — in every case the tests still run,
just visibly as before, rather than breaking.
"""

import os
import sys

# Held for the lifetime of the process so the desktop handle isn't closed (which
# would invalidate the windows rendered on it) until the interpreter exits.
_HDESK = None


def hide_test_windows():
    """Switch the current (main) thread to a fresh hidden desktop.

    Returns True when the switch happened, False otherwise (non-Windows,
    opted out, or any Win32 failure). Must be called before any Tk window is
    created on this thread.
    """
    global _HDESK
    if _HDESK is not None:
        return True
    if not sys.platform.startswith("win"):
        return False
    if os.environ.get("CC_TEST_SHOW_WINDOWS"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)

        # Full control over the new desktop is plenty for creating/showing test
        # windows; GENERIC_ALL keeps the access flags simple.
        GENERIC_ALL = 0x10000000

        user32.CreateDesktopW.restype = wintypes.HANDLE
        user32.CreateDesktopW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        ]
        user32.SetThreadDesktop.restype = wintypes.BOOL
        user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
        user32.CloseDesktop.restype = wintypes.BOOL
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]

        name = "cc_translate_tests_%d" % os.getpid()
        hdesk = user32.CreateDesktopW(name, None, None, 0, GENERIC_ALL, None)
        if not hdesk:
            return False
        # SetThreadDesktop fails if the thread already owns windows or hooks;
        # calling this before the first tk.Tk() keeps that from happening.
        if not user32.SetThreadDesktop(hdesk):
            user32.CloseDesktop(hdesk)
            return False
        _HDESK = hdesk
        return True
    except Exception:
        return False
