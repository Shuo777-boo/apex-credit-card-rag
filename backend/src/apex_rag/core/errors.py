"""Safe public errors; provider payloads and connection secrets never reach clients."""

from typing import Any


class ApexError(Exception):
    def __init__(self, code: str, message: str, status: int = 503,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.details = details or {}
