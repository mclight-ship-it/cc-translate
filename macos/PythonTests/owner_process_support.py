"""Same-bundle synthetic child harness shared by history and config owner tests."""

import errno
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
import unittest


class OwnerProcessCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        executable = Path(sys.executable).resolve()
        contents = next((parent for parent in executable.parents
                         if parent.name == "Contents" and parent.parent.suffix == ".app"), None)
        if contents is None or not executable.is_relative_to(contents / "Helpers" / "python"):
            raise RuntimeError("The owner process suite must use the app's bundled Python.")
        cls.contents = contents
        cls.core = contents / "Resources" / "Core"
        for module in cls.bundle_modules:
            expected = cls.core.joinpath(*module.__name__.split("."))
            expected = expected / "__init__.py" if hasattr(module, "__path__") else expected.with_suffix(".py")
            if Path(module.__file__).resolve() != expected.resolve():
                raise RuntimeError("Owner process tests imported code outside the app's Core.")
        if Path(sys.modules[cls.__module__].__file__).resolve().is_relative_to(contents):
            raise RuntimeError("Owner process tests must come from the checkout, not the app.")
        if not sys.flags.isolated or not sys.dont_write_bytecode:
            raise RuntimeError("Run the bundled Python with -I -B.")

    def process_arguments(self):
        return (self.core, self.path)

    def spawn(self, script=None):
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", self.owner_script if script is None else script,
             *(str(argument) for argument in self.process_arguments())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.home, start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.home), "TMPDIR": str(self.home)},
        )
        self.addCleanup(self.cleanup_process, process)
        return process

    def cleanup_process(self, process):
        # Only this Popen's own child may be signalled, and only on failure.
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def line(self, process, limit=256):
        deadline = time.monotonic() + 8
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(max(0, deadline - time.monotonic())):
                    break
                part = os.read(process.stdout.fileno(), 1)
                if not part:
                    self.fail("Synthetic owner child closed stdout before its handshake.")
                if part == b"\n":
                    return output.decode("utf-8")
                output.extend(part)
                self.assertLess(len(output), limit, "Unexpected synthetic protocol output.")
        self.fail("Synthetic owner child did not complete its handshake.")

    def send(self, process, command, expected=None):
        process.stdin.write((command + "\n").encode("ascii"))
        process.stdin.flush()
        if expected is not None:
            self.assertEqual(self.line(process), expected)

    def finish(self, process, code=0):
        process.stdin.close()
        process.stdin = None
        output, errors = process.communicate(timeout=8)
        self.assertEqual(process.wait(timeout=0), code)
        self.assertEqual(process.returncode, code)
        self.assertEqual((output, errors), (b"", b""))

    def assert_fd_closed(self, fd):
        with self.assertRaises(OSError) as raised:
            os.fstat(fd)
        self.assertEqual(raised.exception.errno, errno.EBADF)
