"""WarmClaude — pre-warmed Claude CLI process for CC Translate.

Spawning a Claude CLI process takes ~1s (Node.js + CLI init). We start one
ahead of time in stream-json mode with the translate system prompt already
loaded so the startup cost is paid while the user is idle. When the next
translation fires, we send one message and stream the reply; the warm process
is consumed after one use (to avoid context accumulation) and replaced.

Public API used by translator.pyw:
  WarmClaude(model, system_prompt, key)
  CLAUDE_CMD          — resolved path/name for the claude CLI binary
  WARM_POOL_ENABLED   — master on/off switch (set to False to debug cold path)
  WARM_UP_MS          — time (ms) we give the CLI to initialise before "ready"
  WARM_MAX_AGE_S      — recycle a warm process older than this (stale guard)
  WARM_SEND_TIMEOUT_S — hard cap on a single warm translation round-trip
"""

import json
import os
import subprocess
import threading
import time

# ---------------------------------------------------------------------------
# Locate the Claude CLI binary
# ---------------------------------------------------------------------------

def _npm_global_prefix():
    """Return npm's configured global prefix dir (where global .cmd shims live),
    or None. This is where `npm install -g` puts binaries; it is NOT always
    %APPDATA%\\npm — users can set a custom prefix (e.g. via npm config or a
    corp-managed toolchain), so we ask npm itself rather than guessing."""
    for npm in ("npm.cmd", "npm"):
        try:
            out = subprocess.run(
                [npm, "config", "get", "prefix"],
                capture_output=True, text=True, timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            prefix = (out.stdout or "").strip()
            if prefix and prefix.lower() != "undefined" and os.path.isdir(prefix):
                return prefix
        except Exception:
            continue
    return None


def find_claude_cmd():
    """Locate the Claude Code CLI without hardcoding a machine-specific path.
    Checks PATH first, then the usual npm global install locations, then npm's
    actual configured prefix (covers custom npm prefixes)."""
    import shutil
    for name in ("claude.cmd", "claude"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd"),
        os.path.join(os.environ.get("APPDATA", ""), "npm", "claude"),
        os.path.join(os.environ.get("ProgramFiles", ""), "nodejs", "claude.cmd"),
    ]
    prefix = _npm_global_prefix()
    if prefix:
        candidates += [
            os.path.join(prefix, "claude.cmd"),
            os.path.join(prefix, "claude"),
        ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return "claude"


CLAUDE_CMD = find_claude_cmd()

# ---------------------------------------------------------------------------
# Pool configuration constants
# ---------------------------------------------------------------------------
WARM_POOL_ENABLED = True
WARM_UP_MS = 2000          # give the CLI this long to initialise before it's "ready"
WARM_MAX_AGE_S = 480       # recycle a warm process older than this (stale-session guard)
WARM_SEND_TIMEOUT_S = 60   # hard cap on a single warm translation


# ---------------------------------------------------------------------------
# Optional error logger — wired up by translator.pyw after DATA_DIR is known
# ---------------------------------------------------------------------------
def _noop_log_error(where, exc):
    pass


_log_error = _noop_log_error


def set_log_error(fn):
    """Register the application-level log_error function. Called once from
    translator.pyw after DATA_DIR is resolved so error records land in the
    right file."""
    global _log_error
    _log_error = fn


# ---------------------------------------------------------------------------
# WarmClaude class
# ---------------------------------------------------------------------------

class WarmClaude:
    """A single pre-warmed Claude CLI process running in stream-json mode.

    Spawned ahead of a translation with a fixed model + system prompt so the
    expensive node/CLI startup finishes while the user is idle. When a
    translation fires we push exactly one user message and stream the reply,
    then discard the process (a resident process accumulates conversation
    context, so we never reuse it). If anything goes wrong the caller falls
    back to the normal cold path, so this is always safe.
    """

    def __init__(self, model, system_prompt, key):
        self.model = model
        self.system_prompt = system_prompt
        self.key = key                       # (model, direction) — matched at use time
        self.proc = None
        self.ready = False                   # True once warmup elapsed
        self.spent = False                   # True once a message has been sent
        self.born = time.monotonic()
        self._lock = threading.Lock()

    def start(self):
        """Spawn the process and arm the readiness timer (non-blocking)."""
        try:
            cmd = [CLAUDE_CMD, "-p", "--safe-mode", "--model", self.model,
                   "--system-prompt", self.system_prompt,
                   "--input-format", "stream-json",
                   "--output-format", "stream-json",
                   "--include-partial-messages", "--verbose",
                   "--tools", "",
                   "--exclude-dynamic-system-prompt-sections",
                   "--no-session-persistence"]
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.born = time.monotonic()

            def _arm():
                time.sleep(WARM_UP_MS / 1000.0)
                self.ready = True
            threading.Thread(target=_arm, daemon=True).start()
            return True
        except Exception as e:
            # log_perf("warm_spawn_error") is a no-op in production; skip it.
            self.proc = None
            return False

    def usable(self, key):
        """True if this process is alive, warmed, unused and matches key."""
        if self.spent or not self.ready or self.key != key:
            return False
        if self.proc is None or self.proc.poll() is not None:
            return False
        if time.monotonic() - self.born > WARM_MAX_AGE_S:
            return False
        return True

    def send_and_stream(self, text, on_delta):
        """Send one user message and stream the reply. Calls on_delta(str) for
        each text delta. Returns the final translated string, or None on
        failure (caller then falls back to the cold path). The process is
        consumed regardless of outcome."""
        with self._lock:
            if self.spent:
                return None
            self.spent = True
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return None

        # Watchdog: kill the process if the round-trip runs away, so the read
        # loop below can't block the translation thread forever.
        killed = {"v": False}

        def _watchdog():
            killed["v"] = True
            try:
                proc.kill()
            except Exception:
                pass
        timer = threading.Timer(WARM_SEND_TIMEOUT_S, _watchdog)
        timer.daemon = True
        timer.start()

        acc = []
        result_text = None
        try:
            msg = {"type": "user",
                   "message": {"role": "user",
                               "content": f"<text>\n{text}\n</text>"}}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = obj.get("type")
                if typ == "stream_event":
                    ev = obj.get("event", {})
                    if ev.get("type") == "content_block_delta":
                        txt = ev.get("delta", {}).get("text", "")
                        if txt:
                            acc.append(txt)
                            try:
                                on_delta(txt)
                            except Exception:
                                pass
                elif typ == "result":
                    if not obj.get("is_error"):
                        r = (obj.get("result") or "").strip()
                        if r:
                            result_text = r
                    break
        except Exception as e:
            _log_error("warm_stream", e)
        finally:
            timer.cancel()

        if killed["v"]:
            return None
        final = (result_text or "".join(acc)).strip()
        return final or None

    def close(self):
        """Terminate the process. Safe to call multiple times / concurrently."""
        p = self.proc
        self.proc = None
        if p is None:
            return
        try:
            if p.stdin and not p.stdin.closed:
                p.stdin.close()
        except Exception:
            pass
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
