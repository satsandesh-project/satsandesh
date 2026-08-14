import pytest
from contracts.ai.errors import ErrorCode, PipelineError
from pydantic import ValidationError


def test_pipeline_error_requires_known_code() -> None:
    with pytest.raises(ValidationError):
        PipelineError.model_validate({"code": "SOMETHING_MADE_UP", "message": "boom"})


def test_pipeline_error_valid_construction() -> None:
    err = PipelineError(code=ErrorCode.OUT_OF_MEMORY, message="ran out of VRAM", stage="tts")
    assert err.code is ErrorCode.OUT_OF_MEMORY
    assert err.contract_version
