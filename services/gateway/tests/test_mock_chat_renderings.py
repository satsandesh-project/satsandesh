"""Week 7: renderings in the mock chat gateway (contracts/chat/mock/app.py).

M1 builds the receiver screen against this mock before the real orchestrator
exists, so it has to let a client developer exercise every state the real one
will produce: translated text + audio, text-only, TTS skipped, nothing yet.
Everything is checked the way a client would use it -- through the API, and
by actually opening the audio the mock hands out.
"""

import io
import uuid
import wave

import pytest
from contracts.chat.envelope import FrameType, SyncBatch
from contracts.chat.messages import AckOut, MessageOut
from contracts.chat.mock.app import app
from contracts.chat.renderings import RenderingDegradedReason
from fastapi.testclient import TestClient

_SUPPORTED = {"en", "hi", "te"}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _send_text(client: TestClient, text: str, *, source_lang: str | None = "te", **kwargs):
    target = f"circle-{uuid.uuid4()}"
    resp = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "circle",
            "target_id": target,
            "kind": "text",
            "text": text,
            "source_lang": source_lang,
        },
        **kwargs,
    )
    assert resp.status_code == 200
    return target, AckOut.model_validate(resp.json())


def _only_message(client: TestClient, target: str) -> MessageOut:
    resp = client.get("/messages", params={"target_type": "circle", "target_id": target})
    batch = SyncBatch.model_validate(resp.json())
    assert len(batch.messages) == 1
    return batch.messages[0]


def test_a_delivered_text_message_has_a_rendering_for_every_other_language(client) -> None:
    target, _ack = _send_text(client, "satsang starts at six", source_lang="te")
    msg = _only_message(client, target)
    assert {r.language for r in msg.renderings} == _SUPPORTED - {"te"}
    assert all(r.text for r in msg.renderings)
    assert all(r.degraded_reason is None for r in msg.renderings)


def test_an_unknown_source_language_gets_every_supported_language(client) -> None:
    target, _ack = _send_text(client, "satsang starts at six", source_lang=None)
    msg = _only_message(client, target)
    assert {r.language for r in msg.renderings} == _SUPPORTED


def test_the_audio_in_a_rendering_can_actually_be_fetched_and_played(client) -> None:
    target, _ack = _send_text(client, "satsang starts at six")
    msg = _only_message(client, target)
    for rendering in msg.renderings:
        assert rendering.audio is not None
        assert rendering.audio.uri.startswith("media:")
        media_id = rendering.audio.uri.removeprefix("media:")
        resp = client.get(f"/media/{media_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"
        # A real, parseable WAV -- not just some bytes with a wav label.
        with wave.open(io.BytesIO(resp.content)) as wav:
            assert wav.getnframes() > 0


def test_tts_skipped_keyword_gives_text_without_audio(client) -> None:
    target, _ack = _send_text(client, "please tts-skip this")
    msg = _only_message(client, target)
    assert msg.renderings
    for rendering in msg.renderings:
        assert rendering.degraded_reason is RenderingDegradedReason.TTS_SKIPPED
        assert rendering.audio is None
        assert rendering.text


def test_text_only_keyword_gives_text_without_audio(client) -> None:
    target, _ack = _send_text(client, "this one is text-only")
    msg = _only_message(client, target)
    assert msg.renderings
    for rendering in msg.renderings:
        assert rendering.degraded_reason is RenderingDegradedReason.TEXT_ONLY
        assert rendering.audio is None


@pytest.mark.parametrize("keyword,status", [("hold", "held"), ("block", "blocked")])
def test_a_message_that_is_not_delivered_has_no_renderings(client, keyword, status) -> None:
    target, ack = _send_text(client, f"please {keyword} this")
    assert ack.status.value == status
    assert _only_message(client, target).renderings == []


def _send_voice(client: TestClient, **kwargs):
    target = f"circle-{uuid.uuid4()}"
    up = client.post("/media", params={"format": "webm_opus"}, content=b"opus-bytes")
    ref = up.json()
    resp = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "circle",
            "target_id": target,
            "kind": "voice",
            "media_ref": {"uri": ref["uri"], "format": ref["format"]},
            "source_lang": "te",
        },
        **kwargs,
    )
    assert resp.status_code == 200
    return target, AckOut.model_validate(resp.json())


def test_a_voice_note_stays_pending_with_no_renderings_by_default(client) -> None:
    # Existing behaviour (no real ASR in a mock) is unchanged.
    target, ack = _send_voice(client)
    assert ack.status.value == "pending"
    assert _only_message(client, target).renderings == []


def test_a_voice_note_can_be_made_to_deliver_with_renderings(client) -> None:
    # The headline case -- a Telugu note reaching a Hindi receiver -- needs a
    # delivered voice note to build against; this is the opt-in for it.
    target, ack = _send_voice(client, headers={"X-Mock-Deliver-Voice": "true"})
    assert ack.status.value == "delivered"
    msg = _only_message(client, target)
    assert {r.language for r in msg.renderings} == _SUPPORTED - {"te"}
    assert all(r.text for r in msg.renderings)
    # The original audio is still there, one tap away.
    assert msg.media_ref is not None


def test_renderings_arrive_in_the_websocket_message_new_frame(client) -> None:
    target = f"circle-{uuid.uuid4()}"
    with client.websocket_connect("/ws?user_id=mock-user-1") as ws:
        ws.send_json(
            {
                "type": FrameType.MESSAGE_SEND.value,
                "data": {
                    "client_msg_id": str(uuid.uuid4()),
                    "target_type": "circle",
                    "target_id": target,
                    "kind": "text",
                    "text": "satsang starts at six",
                    "source_lang": "te",
                },
            }
        )
        assert ws.receive_json()["type"] == FrameType.MESSAGE_ACK.value
        frame = ws.receive_json()
    assert frame["type"] == FrameType.MESSAGE_NEW.value
    msg = MessageOut.model_validate(frame["data"])
    assert {r.language for r in msg.renderings} == _SUPPORTED - {"te"}
