class ProviderError(Exception):
    """Upstream call failed. Retryable."""
    kind = "provider"

    def __init__(self, message, detail=None, status=None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.status = status


class ConfigError(Exception):
    """The deployment is not configured. NOT retryable, and never masked
    with a fabricated result."""
    kind = "config"

    def __init__(self, message, missing=None):
        super().__init__(message)
        self.message = message
        self.missing = missing or []
