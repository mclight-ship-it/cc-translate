"""Self-update, autostart and shortcut helpers for CC Translate.

All git operations run with a hidden window, a short timeout, and credential
prompts disabled (GIT_TERMINAL_PROMPT=0) so an unattended nightly check can
never hang waiting on a login dialog. The whole feature degrades gracefully
when git is missing or the folder is not a repo.

Public API used by translator.pyw:
  is_git_deploy()
  local_head()
  remote_head()
  fetch_remote_branch()
  classify_update_state()
  update_available(local_sha, remote_sha)
  version_string()
  is_autostart_enabled()
  set_autostart(enable)
  ensure_startmenu_shortcut()
  remove_shortcuts()
  spawn_uninstaller(app_dir=None, data_dir=None, remove_data=False, ...)
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
VERSION_MAJOR = 2
VERSION_MINOR = 3

PROGRAMS_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs")
STARTUP_DIR = os.path.join(PROGRAMS_DIR, "Startup")
STARTUP_LNK = os.path.join(STARTUP_DIR, f"{APP_NAME}.lnk")
STARTMENU_LNK = os.path.join(PROGRAMS_DIR, f"{APP_NAME}.lnk")
LEGACY_STARTUP_VBS = os.path.join(STARTUP_DIR, "QuickTranslate.vbs")
SCRIPT_PATH = os.path.join(APP_DIR, "translator.pyw")
PYTHONW = os.path.join(sys.prefix, "pythonw.exe")

# Start Menu / Startup shortcut icon. The Start Menu doesn't adapt to the
# light/dark theme, so we ship the dark-tile artwork (white CC mark) which
# reads well on both light and dark Start Menu backgrounds. Falls back to the
# legacy cc.ico if the themed icon is missing.
ICON_PATH = os.path.join(APP_DIR, "cc-dark.ico")
if not os.path.exists(ICON_PATH):
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


def _local_commit_count():
    rc, out, _ = _git(["rev-list", "--count", "HEAD"], timeout=8)
    if rc != 0 or not out:
        return None
    try:
        return int(out.strip())
    except Exception:
        return None


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


def fetch_remote_branch():
    """Refresh the local tracking ref for the configured remote branch.

    Returns ``(ok, err)`` where ``ok`` is True on success. Update checks use a
    real fetch so they can compare commit ancestry accurately instead of
    treating any SHA difference as "newer"."""
    rc, _, err = _git(
        ["fetch", "--quiet", "--no-tags", GIT_REMOTE, GIT_BRANCH],
        timeout=UPDATE_NET_TIMEOUT)
    return rc == 0, (err or "")


def classify_update_state(local_ref="HEAD", remote_ref=None):
    """Compare local vs fetched remote history.

    Returns ``(state, local_sha, remote_sha)`` where state is one of:
      - ``current``  : both refs point at the same commit
      - ``behind``   : local is an ancestor of remote (a real update exists)
      - ``ahead``    : local already contains the remote commit
      - ``diverged`` : neither side contains the other
      - ``unknown``  : a git lookup/comparison failed
    """
    if remote_ref is None:
        remote_ref = f"{GIT_REMOTE}/{GIT_BRANCH}"

    rc, local, _ = _git(["rev-parse", local_ref], timeout=8)
    if rc != 0 or not local:
        return "unknown", None, None

    rc, remote, _ = _git(["rev-parse", remote_ref], timeout=8)
    if rc != 0 or not remote:
        return "unknown", local.strip(), None

    local = local.strip()
    remote = remote.strip()
    if local == remote:
        return "current", local, remote

    rc, _, _ = _git(["merge-base", "--is-ancestor", local, remote], timeout=10)
    if rc == 0:
        return "behind", local, remote
    if rc not in (0, 1):
        return "unknown", local, remote

    rc, _, _ = _git(["merge-base", "--is-ancestor", remote, local], timeout=10)
    if rc == 0:
        return "ahead", local, remote
    if rc not in (0, 1):
        return "unknown", local, remote

    return "diverged", local, remote


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


def _format_numeric_version(build):
    """Numeric app version label, e.g. ``2.1.347``."""
    try:
        n = int(build)
    except Exception:
        n = 0
    if n < 0:
        n = 0
    return f"{VERSION_MAJOR}.{VERSION_MINOR}.{n}"


def version_string():
    # Prefer a monotonic numeric version users can read at a glance.
    count = _local_commit_count()
    if count is not None:
        return _format_numeric_version(count)

    # Fallback for non-git environments: still keep a numeric-looking version.
    date = _local_commit_date()
    if date:
        compact = date.replace("-", "")
        return f"{VERSION_MAJOR}.{VERSION_MINOR}.{compact}"
    return _format_numeric_version(0)


# ---------------------------------------------------------------------------
# Relauncher + shortcut helpers
# ---------------------------------------------------------------------------

def _ps_squote(s):
    """Quote a string as a PowerShell single-quoted literal (doubling any
    embedded single quotes). Safe for paths with spaces/quotes."""
    return "'" + str(s).replace("'", "''") + "'"


def _log(tag, exc):
    """Report an exception through the translator-provided hook, if wired up.
    Lets otherwise-silent shortcut failures leave a diagnostic trail."""
    if _log_error is not None:
        try:
            _log_error(tag, exc)
        except Exception:
            pass


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
        f"$l = $ws.CreateShortcut({_ps_squote(link_path)}); "
        f"$l.TargetPath = {_ps_squote(PYTHONW)}; "
        f"$l.Arguments = {_ps_squote('\"' + SCRIPT_PATH + '\"')}; "
        f"$l.WorkingDirectory = {_ps_squote(APP_DIR)}; "
        f"$l.IconLocation = {_ps_squote(ICON_PATH)}; "
        "$l.Save()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)


def ensure_startmenu_shortcut():
    """Ensure Start Menu has a launch entry for this app."""
    try:
        _create_shortcut(STARTMENU_LNK)
    except Exception as e:
        _log("ensure_startmenu_shortcut", e)


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
        except Exception as e:
            _log("set_autostart_enable", e)
    else:
        try:
            if os.path.exists(STARTUP_LNK):
                os.remove(STARTUP_LNK)
        except Exception:
            pass


def remove_shortcuts():
    """Delete the Startup and Start Menu shortcuts (and the legacy VBS
    launcher). Best-effort — missing files are ignored."""
    for p in (STARTUP_LNK, STARTMENU_LNK, LEGACY_STARTUP_VBS):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def spawn_uninstaller(app_dir=None, data_dir=None, remove_data=False,
                      pid=None, notify=True):
    """Write a detached PowerShell helper (in %TEMP%, so it isn't sitting in a
    folder it's about to delete) that waits for THIS process to exit, then
    removes the program folder, optionally the user-data folder, shows an
    optional "uninstalled" message box, and finally deletes itself.

    Modeled on _spawn_relauncher: running the cleanup out-of-process from a
    real script file is what lets the app delete its own folder — the files
    stay locked until the interpreter exits, so we wait on the PID first.
    Returns True if the helper was launched."""
    if pid is None:
        pid = os.getpid()
    if app_dir is None:
        app_dir = APP_DIR
    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or app_dir
    log = os.path.join(tmp, "cc_uninstall.log")
    script_path = os.path.join(tmp, "cc_uninstall.ps1")

    lines = [
        "$ErrorActionPreference = 'SilentlyContinue'",
        f"$log = {_ps_squote(log)}",
        ("function W($m) { \"[$(Get-Date -Format HH:mm:ss.fff)] $m\" | "
         "Out-File -FilePath $log -Append -Encoding utf8 }"),
        f"W 'uninstall start; waiting for pid {pid} to exit'",
        f"for ($i = 0; $i -lt 300; $i++) {{",
        f"  if (-not (Get-Process -Id {pid} -ErrorAction SilentlyContinue)) "
        "{ break }",
        "  Start-Sleep -Milliseconds 100",
        "}",
        "Start-Sleep -Milliseconds 600",
        "W 'old process gone (or wait timed out)'",
    ]
    if remove_data and data_dir:
        lines += [
            f"W 'removing data dir {data_dir}'",
            f"Remove-Item -LiteralPath {_ps_squote(data_dir)} "
            "-Recurse -Force -ErrorAction SilentlyContinue",
        ]
    lines += [
        f"W 'removing app dir {app_dir}'",
        f"Remove-Item -LiteralPath {_ps_squote(app_dir)} "
        "-Recurse -Force -ErrorAction SilentlyContinue",
        "W 'app dir removed'",
    ]
    if notify:
        lines += [
            "Add-Type -AssemblyName System.Windows.Forms",
            "[System.Windows.Forms.MessageBox]::Show("
            f"{_ps_squote('CC Translate has been uninstalled.')}, "
            f"{_ps_squote('CC Translate')}) | Out-Null",
        ]
    lines += [
        "W 'uninstall done'",
        # Self-delete: schedule removal of this very script after we exit.
        "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path "
        "-Force -ErrorAction SilentlyContinue",
    ]
    ps = "\n".join(lines) + "\n"

    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(ps)
    except Exception:
        return False

    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-WindowStyle", "Hidden", "-File", script_path]
    try:
        subprocess.Popen(
            cmd, cwd=tmp, creationflags=subprocess.CREATE_NO_WINDOW,
            close_fds=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    return True
