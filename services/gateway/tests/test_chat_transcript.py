"""Week 7: `MessageOut.transcript` -- the original-language text of a voice note.

Raised as open question #1 on the renderings PR (#83) and answered by M1: a
voice message has `text: null`, so a Telugu receiver of a Telugu voice note
got audio and nothing to read -- an accessibility gap for elders who find
audio hard to follow. The answer: a separate field (with its language), not a
source-language entry in `renderings`, which keeps "a rendering is derived
from the pivot" true.

Contract rules first, then what the mock gives a client developer.
"""

import uuid

import pytest
from contracts.chat.common import CONTRACTS_VERSION
from contracts.chat.envelope import SyncBatch
from contracts.chat.messages import MessageOut
from contracts.chat.mock.app import app
from fastapi.testclient import TestClient
from pydantic import ValidationError

_VOICE = {
    "id": "msg-01H8X5Q7Z1",
    "author_id": "user-elder-42",
    "target_type": "circle",
    "target_id": "circle-satsang-evening",
    "kind": "voice",
    "media_ref": {"uri": "media:7c1e6e2a-9b0e-4c4a-8f2e-4a2e6b1c9d3a", "format": "webm_opus"},
    "created_at": "2026-08-17T09:00:00Z",
    "status": "delivered",
}


def _voice(**overrides) -> MessageOut:
    return MessageOut(**{**_VOICE, **overrides})


# --- the contract --------------------------------------------------------------------


def test_a_message_has_no_transcript_by_default() -> None:
    msg = _voice()
    assert msg.transcript is None and msg.transcript_language is None


def test_a_voice_message_can_carry_a_transcript_and_its_language() -> None:
    msg = _voice(transcript="ఈ రోజు సత్సంగం ఎప్పుడు?", transcript_language="te")
    assert msg.transcript == "ఈ రోజు సత్సంగం ఎప్పుడు?"
    assert msg.transcript_language == "te"


def test_the_transcript_and_its_language_come_together() -> None:
    # Text with no language can't be matched against a reader's preference;
    # a language with no text is a claim about nothing.
    with pytest.raises(ValidationError, match="transcript_language"):
        _voice(transcript="hello")
    with pytest.raises(ValidationError, match="transcript"):
        _voice(transcript_language="te")


def test_only_a_voice_message_has_a_transcript() -> None:
    # A text message already carries its text; a transcript there is noise
    # that a client would have to decide how to show.
    text_msg = {
        **_VOICE,
        "kind": "text",
        "text": "hello",
        "media_ref": None,
    }
    with pytest.raises(ValidationError, match="voice"):
        MessageOut(**text_msg, transcript="hello", transcript_language="en")


def test_an_empty_transcript_is_refused() -> None:
    with pytest.raises(ValidationError):
        _voice(transcript="", transcript_language="te")


@pytest.mark.parametrize("language", ["", "te-IN", "Telugu", "TE", "t"])
def test_the_transcript_language_is_a_bare_primary_subtag(language) -> None:
    # Same rule as Rendering.language, for the same reason: a client compares
    # it with preferred_language.
    with pytest.raises(ValidationError):
        _voice(transcript="x", transcript_language=language)


def test_an_older_payload_without_the_fields_still_parses() -> None:
    payload = _voice().model_dump(mode="json")
    del payload["transcript"], payload["transcript_language"]
    assert MessageOut.model_validate(payload).transcript is None


def test_a_transcript_survives_a_round_trip_and_a_sync_batch() -> None:
    msg = _voice(transcript="నమస్కారం", transcript_language="te")
    assert MessageOut.model_validate_json(msg.model_dump_json()) == msg
    batch = SyncBatch(
        target_type="circle", target_id="circle-satsang-evening", messages=[msg], has_more=False
    )
    again = SyncBatch.model_validate(batch.model_dump(mode="json"))
    assert again.messages[0].transcript_language == "te"


def test_the_contract_version_was_bumped_for_the_new_fields() -> None:
    assert CONTRACTS_VERSION == "0.4.0"


# --- the mock --------------------------------------------------------------------------


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _send_voice(client, *, source_lang="te", deliver=True):
    target = f"circle-{uuid.uuid4()}"
    up = client.post("/media", params={"format": "webm_opus"}, content=b"opus-bytes").json()
    resp = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "circle",
            "target_id": target,
            "kind": "voice",
            "media_ref": {"uri": up["uri"], "format": up["format"]},
            "source_lang": source_lang,
        },
        headers={"X-Mock-Deliver-Voice": "true"} if deliver else {},
    )
    assert resp.status_code == 200
    batch = client.get("/messages", params={"target_type": "circle", "target_id": target}).json()
    return MessageOut.model_validate(batch["messages"][0])


def test_a_delivered_voice_note_has_a_transcript_in_its_own_language(client) -> None:
    msg = _send_voice(client, source_lang="te")
    assert msg.transcript
    assert msg.transcript_language == "te"
    # ... and the transcript is NOT one of the renderings.
    assert "te" not in {r.language for r in msg.renderings}


def test_a_pending_voice_note_has_no_transcript_yet(client) -> None:
    assert _send_voice(client, deliver=False).transcript is None


def test_a_regional_source_language_does_not_break_the_mock(client) -> None:
    # `source_lang` is a free string on the wire; "te-IN" is not a valid
    # transcript_language and must degrade, not 500.
    msg = _send_voice(client, source_lang="te-IN")
    assert msg.transcript and msg.transcript_language


def test_a_text_message_never_has_a_transcript(client) -> None:
    target = f"circle-{uuid.uuid4()}"
    client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "circle",
            "target_id": target,
            "kind": "text",
            "text": "hello",
            "source_lang": "en",
        },
    )
    batch = client.get("/messages", params={"target_type": "circle", "target_id": target}).json()
    assert MessageOut.model_validate(batch["messages"][0]).transcript is None
