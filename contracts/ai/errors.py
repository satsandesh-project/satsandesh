from enum import Enum
from typing import Any

from contracts.ai.common import VersionedModel


class ErrorCode(str, Enum):
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
    OUT_OF_MEMORY = "OUT_OF_MEMORY"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    AUDIO_FETCH_FAILED = "AUDIO_FETCH_FAILED"
    TIMEOUT = "TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class PipelineError(VersionedModel):
    """Machine-readable error for any AI pipeline stage."""

    code: ErrorCode
    message: str
    stage: str | None = None
    detail: dict[str, Any] | None = None
