"""Compatibility facade over CC Translate's existing Claude implementation."""

from .base import (
    CLAUDE_PROVIDER,
    ProviderCapabilities,
    ProviderResult,
    ProviderStatus,
)


class ClaudeCliProvider:
    """Delegate to the unchanged app methods that own Claude CLI behavior."""

    provider_id = CLAUDE_PROVIDER
    capabilities = ProviderCapabilities(
        text=True,
        images=True,
        streaming=True,
        warm_sessions=True,
    )

    def __init__(self, complete_func, image_func, command):
        self._complete_func = complete_func
        self._image_func = image_func
        self.command = command

    def complete(self, request, cancel_event=None):
        ok, text = self._complete_func(
            request.user_text, request.system_prompt)
        return ProviderResult(ok, text=text if ok else "", error_detail="" if ok else text)

    def complete_image(self, request, cancel_event=None):
        if len(request.image_paths) != 1:
            return ProviderResult(
                False,
                error_code="invalid_image_count",
                error_detail="Claude Vision requires exactly one image",
            )
        ok, text = self._image_func(request.image_paths[0])
        return ProviderResult(ok, text=text if ok else "", error_detail="" if ok else text)

    def diagnose(self):
        return ProviderStatus(
            installed=bool(self.command),
            authenticated=False,
            command=self.command,
        )

    def shutdown(self):
        return None
