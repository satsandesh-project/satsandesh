from contracts.chat.errors import ErrorCode, ErrorPayload


def test_error_payload_round_trips() -> None:
    err = ErrorPayload(code=ErrorCode.NOT_FOUND, message="circle not found")
    assert err.code is ErrorCode.NOT_FOUND
    assert err.detail is None


def test_error_payload_carries_optional_detail() -> None:
    err = ErrorPayload(
        code=ErrorCode.VALIDATION_FAILED,
        message="text is required when kind is 'text'",
        detail={"field": "text"},
    )
    assert err.detail == {"field": "text"}
