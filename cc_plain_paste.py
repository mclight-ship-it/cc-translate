"""Win32 helpers for the optional global plain-text paste shortcut."""

import ctypes
import threading
import time
from ctypes import wintypes

from cc_core import log_error


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_K = 0x4B
VK_V = 0x56

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

PLAIN_PASTE_HOTKEY_ID = 0x4350


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTUNION),
    ]


def _configure_clipboard_api(user32, kernel32):
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL


def convert_clipboard_to_plain_text(owner_hwnd, timeout_s=0.5):
    """Replace a text clipboard with only CF_UNICODETEXT.

    Returns False without opening or changing the clipboard when Unicode text
    is unavailable, which keeps image- and file-only clipboards untouched.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    _configure_clipboard_api(user32, kernel32)

    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return False

    deadline = time.monotonic() + timeout_s
    while not user32.OpenClipboard(owner_hwnd):
        if time.monotonic() >= deadline:
            raise RuntimeError("OpenClipboard timed out")
        time.sleep(0.01)

    memory = None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return False

        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            raise ctypes.WinError()
        locked = kernel32.GlobalLock(handle)
        if not locked:
            raise ctypes.WinError()
        try:
            text = ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)

        buffer = ctypes.create_unicode_buffer(text)
        memory = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(buffer))
        if not memory:
            raise ctypes.WinError()
        locked = kernel32.GlobalLock(memory)
        if not locked:
            raise ctypes.WinError()
        try:
            ctypes.memmove(
                locked, ctypes.addressof(buffer), ctypes.sizeof(buffer))
        finally:
            kernel32.GlobalUnlock(memory)

        if not user32.EmptyClipboard():
            raise ctypes.WinError()
        if not user32.SetClipboardData(CF_UNICODETEXT, memory):
            raise ctypes.WinError()
        memory = None
        return True
    finally:
        if memory:
            kernel32.GlobalFree(memory)
        if not user32.CloseClipboard():
            raise ctypes.WinError()


def shortcut_keys_released():
    """Whether Ctrl, Shift, and K are all up before injecting Ctrl+V."""
    get_key_state = ctypes.windll.user32.GetAsyncKeyState
    get_key_state.argtypes = [ctypes.c_int]
    get_key_state.restype = wintypes.SHORT
    return not any(
        get_key_state(vk) & 0x8000
        for vk in (VK_CONTROL, VK_SHIFT, VK_K)
    )


def send_ctrl_v():
    """Inject one Ctrl+V chord and return whether every event was accepted."""
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = [
        wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT

    inputs = (INPUT * 4)(
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_CONTROL)),
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=VK_V)),
        INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(wVk=VK_V, dwFlags=KEYEVENTF_KEYUP)),
        INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(wVk=VK_CONTROL, dwFlags=KEYEVENTF_KEYUP)),
    )
    return int(user32.SendInput(
        len(inputs), inputs, ctypes.sizeof(INPUT))) == len(inputs)


class PlainPasteHotkey:
    """Own a thread-scoped RegisterHotKey registration and message loop."""

    def __init__(self, callback):
        self._callback = callback
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._cancel_requested = threading.Event()
        self.available = False
        self.error_code = None

    def start(self, timeout_s=1.0):
        if self._thread is not None and self._thread.is_alive():
            return self.available
        self._ready.clear()
        self._cancel_requested.clear()
        self.available = False
        self.error_code = None
        self._thread = threading.Thread(
            target=self._message_loop,
            name="CCTranslate-PlainPasteHotkey",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout_s):
            self.error_code = 1460  # ERROR_TIMEOUT
            self._cancel_requested.set()
            self.stop()
            return False
        return self.available

    def stop(self, timeout_s=1.0):
        thread = self._thread
        self._cancel_requested.set()
        deadline = time.monotonic() + timeout_s
        while thread is not None and thread.is_alive():
            thread_id = self._thread_id
            if thread_id:
                ctypes.windll.user32.PostThreadMessageW(
                    thread_id, WM_QUIT, 0, 0)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(min(0.05, remaining))
        if thread is None or not thread.is_alive():
            self._thread = None
            self._thread_id = None
        self.available = False
        return self._thread is None

    @property
    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _message_loop(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND,
            wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.RegisterHotKey.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND,
            wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL

        self._thread_id = int(kernel32.GetCurrentThreadId())
        message = wintypes.MSG()
        user32.PeekMessageW(
            ctypes.byref(message), None, 0, 0, PM_NOREMOVE)
        if self._cancel_requested.is_set():
            self._ready.set()
            return
        registered = bool(user32.RegisterHotKey(
            None,
            PLAIN_PASTE_HOTKEY_ID,
            MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT,
            VK_K,
        ))
        self.available = registered
        if not registered:
            self.error_code = int(kernel32.GetLastError())
        self._ready.set()
        if not registered:
            return

        try:
            if self._cancel_requested.is_set():
                return
            while True:
                result = int(user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0))
                if result <= 0:
                    break
                if (message.message == WM_HOTKEY
                        and int(message.wParam) == PLAIN_PASTE_HOTKEY_ID):
                    try:
                        self._callback()
                    except Exception as exc:
                        log_error("plain_paste_hotkey_callback", exc)
        finally:
            user32.UnregisterHotKey(None, PLAIN_PASTE_HOTKEY_ID)
            self.available = False
