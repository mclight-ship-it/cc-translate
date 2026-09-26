"""Model-provider primitives for CC Translate."""

from importlib import import_module
from typing import TYPE_CHECKING

from .base import (
    CLAUDE_PROVIDER,
    CODEX_PROVIDER,
    PROVIDER_IDS,
    ModelProvider,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResult,
    ProviderSelection,
    ProviderStatus,
)
from .registry import ProviderRegistry

if TYPE_CHECKING:
    from .claude_cli import ClaudeCliProvider
    from .codex_cli import CodexCliProvider, build_codex_prompt, find_codex_cmd

_BACKEND_EXPORTS = {
    "ClaudeCliProvider": ".claude_cli",
    "CodexCliProvider": ".codex_cli",
    "build_codex_prompt": ".codex_cli",
    "find_codex_cmd": ".codex_cli",
}


def __getattr__(name):
    module = _BACKEND_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "CLAUDE_PROVIDER",
    "CODEX_PROVIDER",
    "PROVIDER_IDS",
    "CodexCliProvider",
    "ClaudeCliProvider",
    "ModelProvider",
    "ProviderCapabilities",
    "ProviderRequest",
    "ProviderResult",
    "ProviderRegistry",
    "ProviderSelection",
    "ProviderStatus",
    "build_codex_prompt",
    "find_codex_cmd",
]
