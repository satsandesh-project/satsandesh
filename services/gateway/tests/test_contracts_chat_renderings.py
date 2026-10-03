"""Week 7: the per-receiver-language renderings surface on `MessageOut`.

A rendering is what the pipeline made of one message for one language: text,
and (when TTS ran) audio. These tests pin the rules a client (M1's receiver
screen) relies on, so a rule can't loosen without a visible test change.
"""

import pytest
from contracts.ai.common import DegradedReason as AiDegradedReason
from contracts.chat.common import CONTRACTS_VERSION, AudioFormat, MediaRef
from contracts.chat.envelope import SyncBatch
from contracts.chat.messages import MessageOut
from contracts.chat.renderings import (
    MAX_RENDERINGS_PER_MESSAGE,
    Rendering,
    RenderingDegradedReason,
)
from pydantic import ValidationError

_AUDIO = MediaRef(
    uri="media:7c1e6e2a-9b0e-4c4a-8f2e-4a2e6b1c9d3a",
    format=AudioFormat.WAV_PCM16,
    duration_ms=3100,
)


def _rendering(**overrides) -> Rendering:
    base = {"language": "hi", "text": "आज सत्संग कब है?", "audio": _AUDIO}
    base.update(overrides)
    return Rendering(**base)


def _message(**overrides) -> MessageOut:
    base = {
        "id": "msg-01H8X5Q7Z1",
        "author_id": "user-elder-42",
        "target_type": "circle",
        "target_id": "circle-satsang-evening",
        "kind": "text",
        "text": "ఈ రోజు సత్సంగం ఎప్పుడు జరుగుతుంది?",
        "created_at": "2026-08-17T09:00:00Z",
        "status": "delivered",
    }
    base.update(overrides)
    return MessageOut(**base)


# --- Rendering ------------------------------------------------------------


def test_a_complete_rendering_is_not_degraded() -> None:
    r = _rendering()
    assert r.degraded_reason is None
    assert r.audio == _AUDIO


def test_rendering_is_not_a_versioned_payload() -> None:
    # A nested value object, like MediaRef and ModerationEvent -- DECISIONS.md #9.
    assert not hasattr(_rendering(), "contract_version")


@pytest.mark.parametrize("language", ["hi", "te", "en", "fil"])
def test_language_accepts_a_bare_lowercase_primary_subtag(language: str) -> None:
    assert _rendering(language=language).language == language


@pytest.mark.parametrize(
    "language",
    ["", "h", "HI", "Hindi", "hi-IN", "hin_Deva", "hi ", "english", "h1"],
)
def test_language_rejects_anything_that_is_not_a_bare_primary_subtag(language: str) -> None:
    # Same shape the AI package's closed enum uses on the wire, without
    # importing it (DECISIONS.md #5): a client compares this string with the
    # user's preferred_language, so "hi-IN" vs "hi" would silently never match.
    with pytest.raises(ValidationError):
        _rendering(language=language)


def test_text_must_not_be_empty() -> None:
    # The pipeline returns "" for a silent note. A client given an empty
    # rendering would show a blank bubble; the orchestrator must omit it.
    with pytest.raises(ValidationError):
        _rendering(text="")


def test_audio_is_optional() -> None:
    assert _rendering(audio=None).audio is None


@pytest.mark.parametrize(
    "reason", [RenderingDegradedReason.TEXT_ONLY, RenderingDegradedReason.TTS_SKIPPED]
)
def test_a_reason_that_means_no_audio_cannot_carry_audio(reason: RenderingDegradedReason) -> None:
    with pytest.raises(ValidationError):
        _rendering(degraded_reason=reason, audio=_AUDIO)
    assert _rendering(degraded_reason=reason, audio=None).degraded_reason is reason


def test_model_fallback_may_still_carry_audio() -> None:
    r = _rendering(degraded_reason=RenderingDegradedReason.MODEL_FALLBACK)
    assert r.audio == _AUDIO


def test_degraded_reasons_match_the_ai_package_minus_none() -> None:
    """Duplicated rather than imported (DECISIONS.md #5); the drift guard is
    this test, same pattern as the moderation enums."""
    assert {r.value for r in RenderingDegradedReason} == {
        r.value for r in AiDegradedReason if r is not AiDegradedReason.NONE
    }


# --- MessageOut.renderings -------------------------------------------------


def test_a_message_has_no_renderings_by_default() -> None:
    assert _message().renderings == []


def test_a_message_from_an_older_producer_without_the_field_still_parses() -> None:
    payload = _message().model_dump(mode="json")
    del payload["renderings"]
    assert MessageOut.model_validate(payload).renderings == []


def test_a_message_carries_its_renderings() -> None:
    msg = _message(
        renderings=[_rendering(language="hi"), _rendering(language="en", audio=None)],
    )
    assert [r.language for r in msg.renderings] == ["hi", "en"]


def test_one_rendering_per_language() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        _message(renderings=[_rendering(language="hi"), _rendering(language="hi")])


def test_renderings_are_bounded() -> None:
    # Fan-out must have an upper bound (docs/security-checklist.md, Part A).
    ok = [_rendering(language=f"a{chr(98 + i)}") for i in range(MAX_RENDERINGS_PER_MESSAGE)]
    assert len(_message(renderings=ok).renderings) == MAX_RENDERINGS_PER_MESSAGE
    too_many = [*ok, _rendering(language="zz")]
    with pytest.raises(ValidationError):
        _message(renderings=too_many)


def test_renderings_survive_a_json_round_trip() -> None:
    msg = _message(renderings=[_rendering(language="hi"), _rendering(language="te", audio=None)])
    again = MessageOut.model_validate_json(msg.model_dump_json())
    assert again == msg


def test_sync_batch_carries_renderings_through() -> None:
    batch = SyncBatch(
        target_type="circle",
        target_id="circle-satsang-evening",
        messages=[_message(renderings=[_rendering()])],
        has_more=False,
    )
    again = SyncBatch.model_validate(batch.model_dump(mode="json"))
    assert again.messages[0].renderings[0].audio == _AUDIO


def test_the_contract_version_was_bumped_for_the_new_field() -> None:
    # Adding a field to MessageOut is a shape change (common.py's rule). The
    # literal is pinned in test_chat_transcript.py, which is the latest bump.
    assert _message().contract_version == CONTRACTS_VERSION
