import pytest
from contracts.ai.language import LanguageCode
from pydantic import BaseModel, ValidationError


class _Holder(BaseModel):
    language: LanguageCode


@pytest.mark.parametrize("code", ["en", "hi", "te"])
def test_language_code_round_trips(code: str) -> None:
    holder = _Holder(language=code)  # type: ignore[arg-type]  # exercising pydantic's runtime str->enum coercion
    assert holder.language.value == code
    restored = _Holder.model_validate_json(holder.model_dump_json())
    assert restored.language == holder.language


@pytest.mark.parametrize(
    "bad_code",
    [
        "english",  # not a code at all
        "en-US",  # BCP-47 region subtag, not in our closed v1 set
        "ta",  # Tamil — stretch, must not be assumed
        "kn",  # Kannada — stretch, must not be assumed
        "EN",  # wrong case
        "",
    ],
)
def test_language_code_rejects_unsupported_values(bad_code: str) -> None:
    with pytest.raises(ValidationError):
        _Holder(language=bad_code)  # type: ignore[arg-type]  # deliberately invalid at runtime


def test_language_code_rejects_non_string_input() -> None:
    with pytest.raises(ValidationError):
        _Holder.model_validate({"language": 123})
