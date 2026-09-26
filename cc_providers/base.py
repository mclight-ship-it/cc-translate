"""Shared data contracts for model providers."""

from dataclasses import dataclass
import re
from typing import Optional, Protocol, Tuple


CLAUDE_PROVIDER = "claude_cli"
CODEX_PROVIDER = "codex_cli"
PROVIDER_IDS = (CLAUDE_PROVIDER, CODEX_PROVIDER)
MODEL_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})
_MODEL_IDENTIFIER = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)?"
    r"(?::[A-Za-z0-9][A-Za-z0-9._-]*)?")


def safe_model_identifier(value: object) -> Optional[str]:
    """Omit untrusted display text, paths, URLs and credential-shaped values."""
    if (type(value) is not str or not 1 <= len(value) <= 128
            or _MODEL_IDENTIFIER.fullmatch(value) is None
            or value.lower().startswith((
                "sk-", "sk_", "ghp_", "gho_", "github_pat_", "eyj", "akia", "asia"))
            or ".." in value):
        return None
    return value


@dataclass(frozen=True)
class ProviderModelInfo:
    """Requested profile and independently confirmed, optional runtime settings."""

    requested_model: Optional[str] = None
    resolved_model: Optional[str] = None
    reasoning_effort: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, "requested_model", safe_model_identifier(self.requested_model))
        resolved = safe_model_identifier(self.resolved_model)
        object.__setattr__(self, "resolved_model", None if resolved in ("auto", "auto-fast") else resolved)
        effort = self.reasoning_effort
        object.__setattr__(self, "reasoning_effort",
                           effort if type(effort) is str and effort in MODEL_REASONING_EFFORTS else None)


@dataclass(frozen=True)
class ProviderCapabilities:
    text: bool
    images: bool
    streaming: bool
    warm_sessions: bool


@dataclass(frozen=True)
class ProviderRequest:
    task: str
    model: Optional[str]
    system_prompt: str
    user_text: str
    image_paths: Tuple[str, ...] = ()
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    text: str = ""
    error_code: str = ""
    error_detail: str = ""
    metrics: Tuple[Tuple[str, int], ...] = ()
    model_info: Optional[ProviderModelInfo] = None


@dataclass(frozen=True)
class ProviderSelection:
    provider_id: str
    model: Optional[str]


@dataclass(frozen=True)
class ProviderStatus:
    installed: bool
    authenticated: Optional[bool]
    command: Optional[str] = None
    version: str = ""
    auth_method: str = ""
    error_code: str = ""
    error_detail: str = ""
    backend: str = ""


class ModelProvider(Protocol):
    provider_id: str
    capabilities: ProviderCapabilities

    def complete(self, request: ProviderRequest, cancel_event=None) -> ProviderResult:
        ...

    def diagnose(self) -> ProviderStatus:
        ...

    def shutdown(self) -> None:
        ...
