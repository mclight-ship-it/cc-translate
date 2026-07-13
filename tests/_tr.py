"""Load the main program (translator.pyw) as an importable module for tests.

translator.pyw has a .pyw extension and only starts the GUI under
`if __name__ == "__main__"`, so importing it here runs the pure-function
definitions (and cheap module-level setup) without launching any window.
The loaded module is cached so every test file shares one instance.
"""
import importlib.util
import os

_MODULE = None


def load():
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "translator.pyw")
    spec = importlib.util.spec_from_file_location("cc_translate_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULE = module
    return module


tr = load()
