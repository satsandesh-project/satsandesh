"""
Mock gateway for every contracts/chat/ endpoint — HTTP and WebSocket. The
other three members build against this today, before services/gateway/ has
a real database, real auth, or a real chat backbone: canned/stored
responses always validate against the real Pydantic contracts. In-memory
only — state resets on restart. Same shape as services/ai/mock/app.py.
"""

import asyncio
import io
import itertools
import math
import os
import struct
import uuid
import wave
from datetime import datetime, timezone

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)

from contracts.chat.circles import Circle, CircleCreate, Membership, MembershipCreate
from contracts.chat.common import AudioFormat, MediaRef, MessageStatus, TargetType
from contracts.chat.envelope import FrameType, RawFrame, SyncBatch, SyncRequest
from contracts.chat.errors import ErrorCode, ErrorPayload
from contracts.chat.media import MediaUploadOut
from contracts.chat.messages import AckOut, MessageIn, MessageOut
from contracts.chat.renderings import Rendering, RenderingDegradedReason

app = FastAPI(title="SatSandesh Chat — Mock Gateway", version="0.1.0")

DEFAULT_LATENCY_MS = float(os.environ.get("MOCK_LATENCY_MS", "0"))

# Keyword -> status, purely so a client dev can exercise every MessageStatus
# without a real moderation pipeline — same trick as services/ai/mock/app.py's
# _MODERATION_KEYWORDS.
_STATUS_KEYWORDS: dict[str, MessageStatus] = {
    "block": MessageStatus.BLOCKED,
    "hold": MessageStatus.HELD,
}

# In-memory only, single process — no Redis, no database. Fine for a mock;
# won't survive a restart or scale past one instance.
_seq_counter = itertools.count(1)
_messages: dict[tuple[str, str], list[MessageOut]] = {}
_message_seq: dict[str, int] = {}
_circles: dict[str, Circle] = {}
_memberships: dict[str, list[Membership]] = {}

# Week 6: raw uploaded bytes, keyed by the opaque id embedded in the
# "media:<id>" uri this mock hands out (contracts/chat/common.py's
# validate_media_uri, DECISIONS.md #13). In-memory only, same caveat as
# everything else above.
_media: dict[str, tuple[bytes, AudioFormat]] = {}

# The real Content-Type to serve each format back as on GET /media/{id} --
# a mock-only concern (real storage would keep this alongside the bytes,
# not hardcode it); not part of the contract itself.
_MEDIA_CONTENT_TYPE: dict[AudioFormat, str] = {
    AudioFormat.WEBM_OPUS: "audio/webm",
    AudioFormat.OGG_OPUS: "audio/ogg",
    AudioFormat.WAV_PCM16: "audio/wav",
    AudioFormat.MP3: "audio/mpeg",
}


# Week 7: renderings. Keyword -> degraded reason, so a client developer can
# exercise every state the real orchestrator will produce without a real
# pipeline -- same trick as _STATUS_KEYWORDS above.
_RENDERING_KEYWORDS: dict[str, RenderingDegradedReason] = {
    "tts-skip": RenderingDegradedReason.TTS_SKIPPED,
    "text-only": RenderingDegradedReason.TEXT_ONLY,
}

# The languages v1 supports (contracts/ai/language.py), restated here because
# this package does not import contracts/ai/ (DECISIONS.md #5).
_RENDER_LANGUAGES: tuple[str, ...] = ("en", "hi", "te")

_RENDERING_AUDIO_MS = 500


def _tone_wav(duration_ms: int = _RENDERING_AUDIO_MS) -> bytes:
    """A short, audible 440 Hz tone as a real 16 kHz mono PCM16 WAV -- so a
    client developer can verify playback end to end, not just that a URL
    exists. Mock-only; the real service returns synthesized speech."""
    rate = 16000
    n = rate * duration_ms // 1000
    frames = b"".join(
        struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return buf.getvalue()


def _make_renderings(msg: MessageIn, status: MessageStatus) -> list[Rendering]:
    """One rendering per supported language other than the message's own
    (`source_lang`; every supported language when it's unknown). Only for a
    message a receiver can actually see -- a pending, held or blocked message
    has none, same as the real pipeline (contracts/chat/renderings.py)."""
    if status is not MessageStatus.DELIVERED:
        return []
    lowered = (msg.text or "").lower()
    reason = next((r for kw, r in _RENDERING_KEYWORDS.items() if kw in lowered), None)
    renderings = []
    for language in _RENDER_LANGUAGES:
        if language == msg.source_lang:
            continue
        text = f"[{language}] {msg.text}" if msg.text else f"[{language}] (translated voice note)"
        audio = None
        if reason is None:
            media_id = str(uuid.uuid4())
            _media[media_id] = (_tone_wav(), AudioFormat.WAV_PCM16)
            audio = MediaRef(
                uri=f"media:{media_id}",
                format=AudioFormat.WAV_PCM16,
                duration_ms=_RENDERING_AUDIO_MS,
            )
        renderings.append(
            Rendering(language=language, text=text, audio=audio, degraded_reason=reason)
        )
    return renderings


async def _sleep_for_latency(x_mock_latency_ms: str | None) -> None:
    """Per-request `X-Mock-Latency-Ms` header overrides the MOCK_LATENCY_MS
    env var default — lets one client simulate a slow moderation/translation
    round trip without slowing every other call. Mirrors
    services/ai/mock/app.py's _sleep_for_latency."""
    latency_ms = float(x_mock_latency_ms) if x_mock_latency_ms is not None else DEFAULT_LATENCY_MS
    if latency_ms > 0:
        await asyncio.sleep(latency_ms / 1000)


def _resolve_status(msg: MessageIn, deliver_voice: bool = False) -> MessageStatus:
    if msg.kind.value == "voice":
        # No real ASR in a mock — voice messages sit PENDING until a real
        # transcription pipeline (or a future mock endpoint) resolves them.
        # Opt-in (X-Mock-Deliver-Voice header / ?deliver_voice= on the WS) to
        # deliver one straight away, so a client can build the receiver side
        # of a voice note -- translated text + audio -- against this mock.
        return MessageStatus.DELIVERED if deliver_voice else MessageStatus.PENDING
    lowered = (msg.text or "").lower()
    for keyword, status in _STATUS_KEYWORDS.items():
        if keyword in lowered:
            return status
    return MessageStatus.DELIVERED


def _store_message(msg: MessageIn, author_id: str, deliver_voice: bool = False) -> MessageOut:
    status = _resolve_status(msg, deliver_voice)
    record = MessageOut(
        id=str(uuid.uuid4()),
        author_id=author_id,
        target_type=msg.target_type,
        target_id=msg.target_id,
        kind=msg.kind,
        text=msg.text,
        # Week 6: previously dropped -- OPEN_QUESTIONS.md #1's gap. Passed
        # through unchanged; this mock never transcodes.
        media_ref=msg.media_ref,
        renderings=_make_renderings(msg, status),
        created_at=datetime.now(timezone.utc),
        status=status,
    )
    key = (msg.target_type.value, msg.target_id)
    _messages.setdefault(key, []).append(record)
    _message_seq[record.id] = next(_seq_counter)
    return record


def _sync_batch(req: SyncRequest) -> SyncBatch:
    key = (req.target_type.value, req.target_id)
    history = _messages.get(key, [])
    since_seq = _message_seq.get(req.since_id, 0) if req.since_id else 0
    remaining = [m for m in history if _message_seq[m.id] > since_seq]
    page = remaining[: req.limit]
    return SyncBatch(
        target_type=req.target_type,
        target_id=req.target_id,
        messages=page,
        has_more=len(remaining) > len(page),
    )


@app.post("/messages", response_model=AckOut)
async def send_message(
    msg: MessageIn,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_latency_ms: str | None = Header(default=None),
    x_mock_deliver_voice: str | None = Header(default=None),
) -> AckOut:
    await _sleep_for_latency(x_mock_latency_ms)
    record = _store_message(
        msg, x_mock_user_id, deliver_voice=(x_mock_deliver_voice or "").lower() == "true"
    )
    return AckOut(client_msg_id=msg.client_msg_id, id=record.id, status=record.status)


@app.get("/messages", response_model=SyncBatch)
async def list_messages(
    target_type: TargetType,
    target_id: str,
    since: str | None = None,
    limit: int = 50,
    x_mock_latency_ms: str | None = Header(default=None),
) -> SyncBatch:
    await _sleep_for_latency(x_mock_latency_ms)
    req = SyncRequest(target_type=target_type, target_id=target_id, since_id=since, limit=limit)
    return _sync_batch(req)


@app.get("/circles", response_model=list[Circle])
async def list_circles() -> list[Circle]:
    return list(_circles.values())


@app.post("/circles", response_model=Circle)
async def create_circle(
    body: CircleCreate,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_latency_ms: str | None = Header(default=None),
) -> Circle:
    await _sleep_for_latency(x_mock_latency_ms)
    circle = Circle(
        id=str(uuid.uuid4()),
        name=body.name,
        created_by=x_mock_user_id,
        created_at=datetime.now(timezone.utc),
    )
    _circles[circle.id] = circle
    _memberships[circle.id] = []
    return circle


@app.post("/circles/{circle_id}/members", response_model=Membership)
async def add_member(
    circle_id: str,
    body: MembershipCreate,
    x_mock_latency_ms: str | None = Header(default=None),
) -> Membership:
    await _sleep_for_latency(x_mock_latency_ms)
    if circle_id not in _circles:
        raise HTTPException(status_code=404, detail="circle not found")
    membership = Membership(
        circle_id=circle_id,
        user_id=body.user_id,
        role=body.role,
        joined_at=datetime.now(timezone.utc),
    )
    _memberships[circle_id].append(membership)
    return membership


@app.post("/media", response_model=MediaUploadOut)
async def upload_media(
    request: Request,
    format: AudioFormat,
    duration_ms: int | None = None,
    x_mock_latency_ms: str | None = Header(default=None),
) -> MediaUploadOut:
    """Raw bytes as the body, declared metadata as query params -- same
    "reference, never embedded bytes" split MediaRef/AudioRef already make,
    just on the write side: a client can't put an audio blob inside a JSON
    field without base64, which is exactly the waste this package's design
    already avoids elsewhere. See README.md's HTTP routes table and
    DECISIONS.md #14 for why `format` is a required, declared query param
    rather than sniffed from the bytes."""
    await _sleep_for_latency(x_mock_latency_ms)
    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail="empty upload")
    media_id = str(uuid.uuid4())
    _media[media_id] = (body, format)
    return MediaUploadOut(uri=f"media:{media_id}", format=format, duration_ms=duration_ms)


@app.get("/media/{media_id}")
async def fetch_media(media_id: str) -> Response:
    stored = _media.get(media_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="media not found")
    body, media_format = stored
    return Response(content=body, media_type=_MEDIA_CONTENT_TYPE[media_format])


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    # Mock only: identity travels as a `?user_id=` query param, standing in
    # for the real gateway's token-based auth (see app/ws.py in
    # services/gateway/ for the pattern this will eventually follow).
    user_id = websocket.query_params.get("user_id", "mock-user-1")
    deliver_voice = websocket.query_params.get("deliver_voice", "").lower() == "true"
    await websocket.accept()
    try:
        while True:
            raw = RawFrame.model_validate(await websocket.receive_json())
            if raw.type is FrameType.MESSAGE_SEND:
                msg = MessageIn.model_validate(raw.data)
                record = _store_message(msg, user_id, deliver_voice)
                ack = AckOut(client_msg_id=msg.client_msg_id, id=record.id, status=record.status)
                await websocket.send_json(
                    {
                        "type": FrameType.MESSAGE_ACK.value,
                        "data": ack.model_dump(mode="json"),
                    }
                )
                await websocket.send_json(
                    {
                        "type": FrameType.MESSAGE_NEW.value,
                        "data": record.model_dump(mode="json"),
                    }
                )
            elif raw.type is FrameType.SYNC_REQUEST:
                req = SyncRequest.model_validate(raw.data)
                batch = _sync_batch(req)
                await websocket.send_json(
                    {
                        "type": FrameType.SYNC_BATCH.value,
                        "data": batch.model_dump(mode="json"),
                    }
                )
            else:
                err = ErrorPayload(
                    code=ErrorCode.VALIDATION_FAILED,
                    message=f"unsupported frame type from a client: {raw.type.value}",
                )
                await websocket.send_json(
                    {"type": FrameType.ERROR.value, "data": err.model_dump(mode="json")}
                )
    except WebSocketDisconnect:
        # Normal control flow, not an error — same reasoning as
        # services/gateway/app/ws.py.
        pass
