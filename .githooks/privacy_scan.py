"""Reject private machine data and credentials in outgoing Git changes."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


SENSITIVE_BASENAMES = {
    ".env",
    "auth.json",
    "config.json",
    "history.json",
    "perf.log",
    "id_rsa",
    "id_ed25519",
}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}
PLACEHOLDER_USERS = {
    "example",
    "me",
    "name",
    "person",
    "test",
    "user",
    "username",
}

WINDOWS_USER_PATH_RE = re.compile(
    r"(?i)[a-z]:[\\/]+users[\\/]+([^\\/\s\"']+)"
)
UNIX_USER_PATH_RE = re.compile(
    r"(?i)/(?:users|home)/([^/\s\"']+)"
)
EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])"
)
SAFE_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "users.noreply.github.com",
}
TOKEN_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("OpenAI/Anthropic token", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
)
GENERIC_CREDENTIAL_RE = re.compile(
    r"""(?ix)
    \b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)
    \s*[:=]\s*["']?
    ([A-Za-z0-9_./+=-]{20,})
    """
)
SAFE_CREDENTIAL_MARKERS = (
    "example",
    "fake",
    "placeholder",
    "redacted",
    "sample",
    "secret",
    "test",
    "your_",
)


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return proc.stdout


def _sensitive_path_reason(path: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    if name in SAFE_ENV_NAMES:
        return None
    if name in SENSITIVE_BASENAMES:
        return "sensitive local-data filename"
    if PurePosixPath(name).suffix.lower() in SENSITIVE_SUFFIXES:
        return "private key or credential container"
    return None


def _added_lines(base: str, head: str) -> list[tuple[str, str]]:
    diff = _git(
        "diff", "--no-ext-diff", "--text", "--unified=0",
        base, head, "--",
    )
    path = ""
    added: list[tuple[str, str]] = []
    for raw_line in diff.splitlines():
        if raw_line.startswith("+++ b/"):
            path = raw_line[6:]
        elif raw_line.startswith("+") and not raw_line.startswith("+++"):
            added.append((path, raw_line[1:]))
    return added


def _changed_paths(base: str, head: str) -> list[str]:
    output = _git(
        "diff", "--name-only", "--diff-filter=ACMR", base, head, "--",
    )
    return [line for line in output.splitlines() if line]


def scan_line(_path: str, line: str, home: Path | None = None) -> list[str]:
    findings: list[str] = []
    normalized = line.replace("\\", "/").casefold()
    resolved_home = (home or Path.home()).as_posix().rstrip("/").casefold()
    if resolved_home and resolved_home in normalized:
        findings.append("current user's home path")

    for pattern in (WINDOWS_USER_PATH_RE, UNIX_USER_PATH_RE):
        for match in pattern.finditer(line):
            if match.group(1).casefold() not in PLACEHOLDER_USERS:
                findings.append("machine-specific user path")
                break

    for email in EMAIL_RE.findall(line):
        domain = email.rsplit("@", 1)[1].casefold()
        if domain not in SAFE_EMAIL_DOMAINS:
            findings.append("email address or account identifier")
            break

    for label, pattern in TOKEN_PATTERNS:
        if pattern.search(line):
            findings.append(label)

    credential = GENERIC_CREDENTIAL_RE.search(line)
    if credential:
        value = credential.group(1).casefold()
        if not any(marker in value for marker in SAFE_CREDENTIAL_MARKERS):
            findings.append("credential-like assignment")

    return list(dict.fromkeys(findings))


def scan_range(base: str, head: str) -> list[str]:
    findings: list[str] = []
    for path in _changed_paths(base, head):
        reason = _sensitive_path_reason(path)
        if reason:
            findings.append(f"{path}: {reason}")

    for path, line in _added_lines(base, head):
        for reason in scan_line(path, line):
            preview = line.strip()
            if len(preview) > 120:
                preview = preview[:117] + "..."
            findings.append(f"{path}: {reason}: {preview}")
    return findings


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: privacy_scan.py <base-sha> <head-sha>", file=sys.stderr)
        return 2
    try:
        findings = scan_range(argv[1], argv[2])
    except (OSError, RuntimeError) as exc:
        print(f"pre-push privacy scan failed: {exc}", file=sys.stderr)
        return 2
    if not findings:
        print("pre-push: privacy scan passed.")
        return 0
    print("pre-push: PRIVATE DATA CHECK FAILED — push blocked.", file=sys.stderr)
    for finding in findings:
        print(f"  - {finding}", file=sys.stderr)
    print(
        "Remove or replace the flagged data before pushing. "
        "Do not commit real credentials or machine-specific paths.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
