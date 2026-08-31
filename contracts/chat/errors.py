from enum import Enum
from typing import Any

from contracts.chat.common import VersionedModel


class ErrorCode(str, Enum):
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorPayload(VersionedModel):
    """Machine-readable error, carried as the `data` of an `error` WS frame
    and used for any HTTP error body this package's routes want to return in
    a typed shape. Mirrors contracts/ai/errors.py's PipelineError."""

    code: ErrorCode
    message: str
    detail: dict[str, Any] | None = None
