"""Small explicit registry for parallel model providers."""


class ProviderRegistry:
    def __init__(self):
        self._providers = {}

    def register(self, provider):
        provider_id = provider.provider_id
        if not provider_id:
            raise ValueError("provider_id must not be empty")
        self._providers[provider_id] = provider
        return provider

    def get(self, provider_id):
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise KeyError(f"Unknown model provider: {provider_id}") from exc

    def ids(self):
        return tuple(self._providers)

    def shutdown(self):
        errors = []
        for provider in tuple(self._providers.values()):
            try:
                provider.shutdown()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(
                f"{len(errors)} model provider(s) failed to shut down"
            ) from errors[0]
