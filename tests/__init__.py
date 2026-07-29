"""cc-translate test package.

Importing the package (which ``unittest discover`` / ``python -m unittest`` does
before loading any test module) first routes this process's GUI onto a hidden
desktop, so the real Tk windows the UI tests build never flash on screen or
steal keyboard focus. See ``_headless`` for the full rationale; it is a safe
no-op on non-Windows and on failure.
"""

from . import _headless as _headless

_headless.hide_test_windows()
