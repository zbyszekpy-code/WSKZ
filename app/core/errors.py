"""Domain errors raised by routing services."""


class RoutingError(Exception):
    """Raised when a downstream service fails during routing.

    Tasks 03 and 04 raise this for Ollama and SMTP failures. The application
    maps it to HTTP 503 with the user-safe ``detail``.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
