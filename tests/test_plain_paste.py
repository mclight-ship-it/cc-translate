import ctypes
import threading
import time
import types
import unittest
from unittest import mock

import cc_plain_paste as plain_paste
from tests._tr import tr


class TestPlainTextClipboard(unittest.TestCase):
    def _apis(self, *, has_text=True, send_input=4):
        source = ctypes.create_unicode_buffer("Hello\r\n世界")
        destination = ctypes.create_unicode_buffer("Hello\r\n世界")
        user32 = types.SimpleNamespace(
            IsClipboardFormatAvailable=mock.Mock(return_value=has_text),
            OpenClipboard=mock.Mock(return_value=True),
            CloseClipboard=mock.Mock(return_value=True),
            GetClipboardData=mock.Mock(return_value=101),
            EmptyClipboard=mock.Mock(return_value=True),
            SetClipboardData=mock.Mock(return_value=202),
            SendInput=mock.Mock(return_value=send_input),
            GetAsyncKeyState=mock.Mock(return_value=0),
        )
        kernel32 = types.SimpleNamespace(
            GlobalAlloc=mock.Mock(return_value=202),
            GlobalLock=mock.Mock(side_effect=[
                ctypes.addressof(source),
                ctypes.addressof(destination),
            ]),
            GlobalUnlock=mock.Mock(return_value=True),
            GlobalFree=mock.Mock(return_value=None),
        )
        return user32, kernel32

    def test_image_only_clipboard_is_untouched(self):
        user32, kernel32 = self._apis(has_text=False)
        with mock.patch.object(
                plain_paste.ctypes, "windll",
                types.SimpleNamespace(user32=user32, kernel32=kernel32)):
            self.assertFalse(
                plain_paste.convert_clipboard_to_plain_text(123))
        user32.OpenClipboard.assert_not_called()
        user32.EmptyClipboard.assert_not_called()
        user32.SetClipboardData.assert_not_called()

    def test_text_clipboard_is_republished_as_unicode_only(self):
        user32, kernel32 = self._apis()
        with mock.patch.object(
                plain_paste.ctypes, "windll",
                types.SimpleNamespace(user32=user32, kernel32=kernel32)):
            self.assertTrue(
                plain_paste.convert_clipboard_to_plain_text(123))
        user32.OpenClipboard.assert_called_once_with(123)
        user32.EmptyClipboard.assert_called_once_with()
        user32.SetClipboardData.assert_called_once_with(
            plain_paste.CF_UNICODETEXT, 202)
        user32.CloseClipboard.assert_called_once_with()
        kernel32.GlobalFree.assert_not_called()

    def test_ctrl_v_requires_every_injected_event(self):
        user32, kernel32 = self._apis(send_input=3)
        with mock.patch.object(
                plain_paste.ctypes, "windll",
                types.SimpleNamespace(user32=user32, kernel32=kernel32)):
            self.assertFalse(plain_paste.send_ctrl_v())

    def test_input_structure_matches_win32_abi(self):
        expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(plain_paste.INPUT), expected_size)

    def test_shortcut_waits_for_every_key_to_be_released(self):
        user32, kernel32 = self._apis()
        user32.GetAsyncKeyState.side_effect = [0, 0x8000, 0]
        with mock.patch.object(
                plain_paste.ctypes, "windll",
                types.SimpleNamespace(user32=user32, kernel32=kernel32)):
            self.assertFalse(plain_paste.shortcut_keys_released())


class TestPlainPasteAppFlow(unittest.TestCase):
    def _app(self):
        app = object.__new__(tr.TranslatorApp)
        app.cfg = tr.Config(dict(tr.DEFAULT_CONFIG))
        app.cfg[tr.CFG.PLAIN_TEXT_PASTE_ENABLED] = True
        app.root = mock.Mock()
        app.root.winfo_id.return_value = 123
        return tr, app

    def test_non_text_clipboard_does_not_send_paste(self):
        tr, app = self._app()
        with mock.patch.object(
                tr, "convert_clipboard_to_plain_text", return_value=False), \
                mock.patch.object(tr, "send_ctrl_v") as send:
            self.assertFalse(app._paste_clipboard_as_plain_text())
        send.assert_not_called()

    def test_text_clipboard_is_converted_then_pasted(self):
        tr, app = self._app()
        with mock.patch.object(
                tr, "convert_clipboard_to_plain_text", return_value=True) as convert, \
                mock.patch.object(tr, "send_ctrl_v", return_value=True) as send:
            self.assertTrue(app._paste_clipboard_as_plain_text())
        convert.assert_called_once_with(123)
        send.assert_called_once_with()

    def test_registration_conflict_keeps_setting_enabled(self):
        tr, app = self._app()
        app._plain_paste_queue = tr.queue.Queue()
        app._plain_paste_hotkey = None

        service = mock.Mock()
        service.start.return_value = False
        service.error_code = 1409
        service.is_alive = False
        with mock.patch.object(tr, "PlainPasteHotkey", return_value=service):
            self.assertFalse(app._configure_plain_paste_hotkey())

        self.assertTrue(app.cfg[tr.CFG.PLAIN_TEXT_PASTE_ENABLED])
        self.assertFalse(app._plain_paste_hotkey_available)
        self.assertEqual(app._plain_paste_hotkey_error, 1409)


class TestPlainPasteHotkeyLifecycle(unittest.TestCase):
    def test_start_timeout_cancels_worker_before_registration(self):
        class DelayedHotkey(plain_paste.PlainPasteHotkey):
            def _message_loop(self):
                time.sleep(0.03)
                self._thread_id = threading.get_ident()
                if self._cancel_requested.is_set():
                    self._ready.set()
                    return
                self.available = True
                self._ready.set()

        hotkey = DelayedHotkey(lambda: None)
        self.assertFalse(hotkey.start(timeout_s=0.001))
        self.assertFalse(hotkey.available)
        self.assertFalse(hotkey.is_alive)


if __name__ == "__main__":
    unittest.main()
