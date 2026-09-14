"""Immutable request capture, without configuration or platform policy.

Callers must not mutate inputs concurrently while a snapshot is constructed.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Optional

from cc_providers.base import ProviderRequest, ProviderSelection


def _check_type(value, allowed, path):
    # Scalar subclasses can carry mutable attributes, so accept exact types only.
    if type(value) not in allowed:
        expected = " or ".join(kind.__name__ for kind in allowed)
        raise TypeError(f"{path} must be {expected}; got {type(value).__name__}")


def _freeze_config(value, active, path):
    if type(value) in (type(None), str, bool, int, float):
        return value
    if not isinstance(value, (Mapping, list, tuple)):
        raise TypeError(f"{path} has unsupported type {type(value).__name__}")
    identity = id(value)
    if identity in active:
        raise ValueError(f"cycle detected at {path}")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            frozen = {}
            for key, child in value.items():
                _check_type(key, (str,), f"{path} key")
                frozen[key] = _freeze_config(child, active, f"{path}.value")
            return MappingProxyType(frozen)
        return tuple(
            _freeze_config(child, active, f"{path}[{index}]")
            for index, child in enumerate(value)
        )
    finally:
        active.remove(identity)


def _copy_request(request):
    if not isinstance(request, ProviderRequest):
        raise TypeError("request must be ProviderRequest")
    image_paths = request.image_paths
    if not isinstance(image_paths, (list, tuple)):
        raise TypeError("request.image_paths must be list or tuple")
    copied = ProviderRequest(
        task=request.task,
        model=request.model,
        system_prompt=request.system_prompt,
        user_text=request.user_text,
        image_paths=tuple(path for path in image_paths),
        timeout_seconds=request.timeout_seconds,
    )
    for name in ("task", "system_prompt", "user_text"):
        _check_type(getattr(copied, name), (str,), f"request.{name}")
    _check_type(copied.model, (str, type(None)), "request.model")
    _check_type(copied.timeout_seconds, (int, float), "request.timeout_seconds")
    for index, path in enumerate(copied.image_paths):
        _check_type(path, (str,), f"request.image_paths[{index}]")
    return copied


def _copy_selection(selection):
    if not isinstance(selection, ProviderSelection):
        raise TypeError("selection must be ProviderSelection")
    copied = ProviderSelection(provider_id=selection.provider_id, model=selection.model)
    _check_type(copied.provider_id, (str,), "selection.provider_id")
    _check_type(copied.model, (str, type(None)), "selection.model")
    return copied


@dataclass(frozen=True)
class RequestSnapshot:
    request: ProviderRequest
    selection: ProviderSelection
    config: Mapping[str, object]
    input: Optional[str]
    origin: str
    content_class: str
    kind: str
    sig: str
    direction: str
    app_language: str
    target_lang: Optional[str]
    summarize: bool
    dictionary: bool
    stream_enabled: bool
    action: str = "translation"

    def __post_init__(self):
        for name in (
            "origin", "content_class", "kind", "sig", "direction",
            "app_language", "action",
        ):
            _check_type(getattr(self, name), (str,), name)
        for name in ("input", "target_lang"):
            _check_type(getattr(self, name), (str, type(None)), name)
        for name in ("summarize", "dictionary", "stream_enabled"):
            _check_type(getattr(self, name), (bool,), name)
        if not isinstance(self.config, Mapping):
            raise TypeError("config must be a mapping")
        request = _copy_request(self.request)
        selection = _copy_selection(self.selection)
        config = _freeze_config(self.config, set(), "config")
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "selection", selection)
        object.__setattr__(self, "config", config)

    @property
    def history_metadata(self) -> Mapping[str, object]:
        """Describe this request without granting history-write permission."""
        return MappingProxyType({
            "input": self.input,
            "origin": self.origin,
            "is_code": self.content_class == "code",
            "kind": self.kind,
            "sig": self.sig,
        })

    def with_timeout(self, seconds: float) -> ProviderRequest:
        """Return an execution request copy; keep the captured request intact."""
        _check_type(seconds, (int, float), "seconds")
        return replace(self.request, timeout_seconds=seconds)
