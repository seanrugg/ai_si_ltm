"""One exception type for service-level failures; the API maps it to JSON."""
from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
