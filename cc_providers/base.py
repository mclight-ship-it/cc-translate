"""Shared data contracts for model providers."""

from dataclasses import dataclass
from typing import Optional, Protocol, Tuple


CLAUDE_PROVIDER = "claude_cli"
CODEX_PROVIDER = "codex_cli"
PROVIDER_IDS = (CLAUDE_PROVIDER, CODEX_PROVIDER)


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


@dataclass(frozen=True)
class ProviderSelection:
    provider_id: str
    model: Optional[str]


@dataclass(frozen=True)
class ProviderStatus:
    installed: bool
    authenticated: bool
    command: Optional[str] = None
    version: str = ""
    auth_method: str = ""
    error_code: str = ""
    error_detail: str = ""


class ModelProvider(Protocol):
    provider_id: str
    capabilities: ProviderCapabilities

    def complete(self, request: ProviderRequest, cancel_event=None) -> ProviderResult:
        ...

    def diagnose(self) -> ProviderStatus:
        ...

    def shutdown(self) -> None:
        ...
