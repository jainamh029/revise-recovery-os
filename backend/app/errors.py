class DomainError(Exception):
    """Business-rule violation. Mapped to HTTP 409/422 by the API layer."""

    status_code = 409

    def __init__(self, message: str, *, code: str = "rule_violation", details: dict | None = None):
        super().__init__(message)
        self.message, self.code, self.details = message, code, details or {}


class NotFound(DomainError):
    status_code = 404

    def __init__(self, what: str):
        super().__init__(f"{what} not found", code="not_found")


class Invalid(DomainError):
    status_code = 422
