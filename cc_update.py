"""Self-update, autostart and shortcut helpers for CC Translate.

All git operations run with a hidden window, a short timeout, and credential
prompts disabled (GIT_TERMINAL_PROMPT=0) so an unattended nightly check can
never hang waiting on a login dialog. The whole feature degrades gracefully
when git is missing or the folder is not a repo.

Public API used by translator.pyw:
  is_git_deploy()
  local_head()
  remote_head()
  update_available(local_sha, remote_sha)
  version_string()
  is_autostart_enabled()
  set_autostart(enable)
  ensure_startmenu_shortcut()
  _spawn_relauncher(pid=None)

  LEGACY_STARTUP_VBS  — path checked on first run to migrate old launchers
  SCRIPT_PATH         — absolute path to translator.pyw (used by _spawn_relauncher)
  PYTHONW             — pythonw.exe in the active environment
"""

import os
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Paths — computed relative to *this* file's directory (= APP_DIR).
# When moving the code here is kept consistent: both this module and
# translator.pyw resolve APP_DIR the same way.
# ---------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "CC Translate"

PROGRAMS_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs")
STARTUP_DIR = os.path.join(PROGRAMS_DIR, "Startup")
STARTUP_LNK = os.path.join(STARTUP_DIR, f"{APP_NAME}.lnk")
STARTMENU_LNK = os.path.join(PROGRAMS_DIR, f"{APP_NAME}.lnk")
LEGACY_STARTUP_VBS = os.path.join(STARTUP_DIR, "QuickTranslate.vbs")
SCRIPT_PATH = os.path.join(APP_DIR, "translator.pyw")
PYTHONW = os.path.join(sys.prefix, "pythonw.exe")

ICON_PATH = os.path.join(APP_DIR, "cc.ico")

# ---------------------------------------------------------------------------
# Git constants + error-log hook
# ---------------------------------------------------------------------------
GIT_REMOTE = "origin"
GIT_BRANCH = "master"
UPDATE_NET_TIMEOUT = 25          # seconds for network git ops (fetch/ls-remote)

_log_error = None  # set by translator.pyw: cc_update._log_error = log_error


def _noop(*a):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(args, timeout=15, cwd=None):
    """Run a git command in the app dir. Returns ``(rc, stdout, stderr)`` with
    both streams stripped. The window is hidden and credential prompts are
    disabled so a call fails fast instead of blocking on interactive auth."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GCM_INTERACTIVE", "never")
    try:
        p = subprocess.run(
            ["git", *args], cwd=cwd or APP_DIR, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "git-not-found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 1, "", str(e)[:200]


def is_git_deploy():
    """True when the app folder is a git working tree and git is available."""
    rc, out, _ = _git(["rev-parse", "--is-inside-work-tree"], timeout=8)
    return rc == 0 and out == "true"


def local_head():
    rc, out, _ = _git(["rev-parse", "HEAD"], timeout=8)
    return out if rc == 0 and out else None


def _local_head_short():
    rc, out, _ = _git(["rev-parse", "--short", "HEAD"], timeout=8)
    return out if rc == 0 and out else None


def _local_commit_date():
    rc, out, _ = _git(
        ["show", "-s", "--format=%cd", "--date=format:%Y-%m-%d", "HEAD"],
        timeout=8)
    return out if rc == 0 and out else None


def remote_head():
    """Latest commit SHA on the remote branch via ``ls-remote`` (cheap; touches
    no local refs and writes no files). Returns None on any failure."""
    rc, out, _ = _git(
        ["ls-remote", GIT_REMOTE, f"refs/heads/{GIT_BRANCH}"],
        timeout=UPDATE_NET_TIMEOUT)
    if rc != 0 or not out:
        return None
    first = out.splitlines()[0].split()
    return first[0].strip() if first else None


def update_available(local_sha, remote_sha):
    """Pure predicate: the remote points at a different commit than local.

    This is a cheap heuristic; the actual updater still confirms a clean
    fast-forward (via merge-base) before touching anything, so a local checkout
    that happens to be *ahead* of the remote is handled there, not here."""
    if not local_sha or not remote_sha:
        return False
    return local_sha.strip() != remote_sha.strip()


def _format_version(short_sha, date):
    """Human version label, e.g. ``9ef3615 · 2026-07-13``."""
    if not short_sha:
        return "未知版本"
    return f"{short_sha} · {date}" if date else short_sha


def version_string():
    return _format_version(_local_head_short(), _local_commit_date())


# ---------------------------------------------------------------------------
# Relauncher + shortcut helpers
# ---------------------------------------------------------------------------

def _ps_squote(s):
    """Quote a string as a PowerShell single-quoted literal (doubling any
    embedded single quotes). Safe for paths with spaces/quotes."""
    return "'" + str(s).replace("'", "''") + "'"


def _spawn_relauncher(pid=None, data_dir=None):
    """Write a small detached PowerShell helper that waits for THIS process to
    fully exit (which releases the single-instance mutex), then starts a fresh
    instance — retrying if the first attempt dies immediately (which would mean
    the mutex was still held). Every step is logged to relaunch.log so a failed
    restart can be diagnosed. Running the waiter out-of-process, from a real
    script file rather than a fragile inline string, is what makes an in-place
    restart reliable despite the single-instance guard."""
    if pid is None:
        pid = os.getpid()
    if data_dir is None:
        data_dir = APP_DIR
    log = os.path.join(data_dir, "relaunch.log")
    script_path = os.path.join(data_dir, "_relaunch.ps1")
    ps = (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        f"$log = {_ps_squote(log)}\n"
        "function W($m) { \"[$(Get-Date -Format HH:mm:ss.fff)] $m\" | "
        "Out-File -FilePath $log -Append -Encoding utf8 }\n"
        f"W 'relaunch start; waiting for pid {pid} to exit'\n"
        f"for ($i = 0; $i -lt 300; $i++) {{\n"
        f"  if (-not (Get-Process -Id {pid} -ErrorAction SilentlyContinue)) "
        "{ break }\n"
        "  Start-Sleep -Milliseconds 100\n"
        "}\n"
        "W 'old process gone (or wait timed out)'\n"
        "Start-Sleep -Milliseconds 600\n"
        "for ($try = 1; $try -le 5; $try++) {\n"
        f"  $p = Start-Process -FilePath {_ps_squote(PYTHONW)} "
        f"-ArgumentList {_ps_squote('\"' + SCRIPT_PATH + '\"')} "
        f"-WorkingDirectory {_ps_squote(APP_DIR)} -PassThru\n"
        "  W \"started attempt $try pid=$($p.Id)\"\n"
        "  Start-Sleep -Seconds 2\n"
        "  if (Get-Process -Id $p.Id -ErrorAction SilentlyContinue) "
        "{ W 'alive after 2s - success'; break }\n"
        "  W 'new instance died within 2s — retrying'\n"
        "  Start-Sleep -Milliseconds 800\n"
        "}\n"
        "W 'relaunch done'\n"
    )
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(ps)
    except Exception:
        script_path = None

    if script_path:
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", script_path]
    else:
        inline = (
            "$ErrorActionPreference='SilentlyContinue';"
            f"for($i=0;$i -lt 300;$i++){{if(-not (Get-Process -Id {pid} "
            "-ErrorAction SilentlyContinue)){break};Start-Sleep -Milliseconds 100};"
            "Start-Sleep -Milliseconds 600;"
            f"Start-Process -FilePath {_ps_squote(PYTHONW)} "
            f"-ArgumentList {_ps_squote('\"' + SCRIPT_PATH + '\"')} "
            f"-WorkingDirectory {_ps_squote(APP_DIR)}"
        )
        cmd = ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
               "-Command", inline]
    subprocess.Popen(
        cmd, creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)


def _create_shortcut(link_path):
    """Create or update a .lnk pointing to this app's pythonw launcher."""
    try:
        import pythoncom  # noqa: F401
    except Exception:
        pass
    ps = (
        "$ErrorActionPreference = 'Stop'; "
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$l = $ws.CreateShortcut('{link_path}'); "
        f"$l.TargetPath = '{PYTHONW}'; "
        f"$l.Arguments = '\"{SCRIPT_PATH}\"'; "
        f"$l.WorkingDirectory = '{APP_DIR}'; "
        f"$l.IconLocation = '{ICON_PATH}'; "
        "$l.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)


def ensure_startmenu_shortcut():
    """Ensure Start Menu has a launch entry for this app."""
    try:
        _create_shortcut(STARTMENU_LNK)
    except Exception:
        pass


def is_autostart_enabled():
    return os.path.exists(STARTUP_LNK)


def set_autostart(enable):
    """Create or remove a Startup-folder shortcut to launch this app silently."""
    try:
        if os.path.exists(LEGACY_STARTUP_VBS):
            os.remove(LEGACY_STARTUP_VBS)
    except Exception:
        pass
    if enable:
        try:
            _create_shortcut(STARTUP_LNK)
        except Exception:
            pass
    else:
        try:
            if os.path.exists(STARTUP_LNK):
                os.remove(STARTUP_LNK)
        except Exception:
            pass
