import pytest
from contracts.ai.language import LanguageCode
from contracts.ai.pivot import PivotRequest, PivotResponse
from pydantic import ValidationError


def test_pivot_request_construction() -> None:
    req = PivotRequest(text="నమస్తే", source_language=LanguageCode.TELUGU)
    assert req.source_language is LanguageCode.TELUGU


def test_pivot_response_requires_model_version() -> None:
    with pytest.raises(ValidationError):
        PivotResponse.model_validate(
            {"pivot_text": "hello", "source_language": "te", "duration_ms": 5.0}
        )


def test_pivot_response_allows_english_source_as_identity_pass() -> None:
    resp = PivotResponse(
        pivot_text="hello",
        source_language=LanguageCode.ENGLISH,
        model_version="indictrans2-distilled@1",
        duration_ms=1.0,
    )
    assert resp.source_language is LanguageCode.ENGLISH
    assert resp.degraded.active is False
