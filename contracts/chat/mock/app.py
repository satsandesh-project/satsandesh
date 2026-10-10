"""
Mock gateway for every contracts/chat/ endpoint — HTTP and WebSocket. The
other three members build against this today, before services/gateway/ has
a real database, real auth, or a real chat backbone: canned/stored
responses always validate against the real Pydantic contracts. In-memory
only — state resets on restart. Same shape as services/ai/mock/app.py.
"""

import asyncio
import base64
import binascii
import io
import itertools
import math
import os
import re
import struct
import uuid
import wave
from datetime import datetime, timezone

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
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
from contracts.chat.mock.admin_org import router as admin_org_router
from contracts.chat.mock.claims import router as claims_router
from contracts.chat.moderation import (
    ModerationAction,
    ModerationActorKind,
    ModerationEvent,
    ModerationEventsOut,
    ModerationLabel,
    ModerationQueueItem,
    ModerationQueueOut,
    ModerationReviewIn,
    ModerationReviewOut,
)
from contracts.chat.renderings import LANGUAGE_PATTERN, Rendering, RenderingDegradedReason
from contracts.chat.users import UserSettingsOut, UserSettingsUpdate

app = FastAPI(title="SatSandesh Chat — Mock Gateway", version="0.1.0")

# Organisation admin routes (`/admin/*`): their own module, one line here.
app.include_router(admin_org_router)
app.include_router(claims_router)

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


def _make_transcript(msg: MessageIn, status: MessageStatus) -> dict[str, str]:
    """A delivered voice note's transcript, in its own language (`source_lang`
    when that is a valid bare subtag; a free-string like "te-IN" degrades to
    "en" rather than breaking the mock). Nothing for text, or before delivery."""
    if msg.kind.value != "voice" or status is not MessageStatus.DELIVERED:
        return {}
    language = msg.source_lang if re.match(LANGUAGE_PATTERN, msg.source_lang or "") else "en"
    return {
        "transcript": f"[{language}] (transcript of the voice note)",
        "transcript_language": language,
    }


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


# Week 7: GET/PATCH /me/settings, in memory per X-Mock-User-Id. Same
# omitted-vs-null rule as the real route: a field the PATCH omits is left alone,
# an explicit null clears it. (No language/timezone allow-list here -- the mock
# has no pipeline and no IANA database to answer to.)
_settings: dict[str, UserSettingsOut] = {}

# Week 7: the moderator console's routes (contracts/chat/moderation.py, issue #65), so the
# console can be built against this mock until it runs on the real gateway. The same rules
# as services/gateway/app/moderation.py: only `held` messages are queued, a release may
# reverse a block but a block needs a held message, a stale `expected_event_id` is a 409, and
# every decision APPENDS an event. What a mock cannot honestly do, it does not pretend to:
#   - identity: there is no role table, so a caller is a moderator when it sends
#     `X-Mock-Role: moderator` (or `admin`); anything else is a 403.
#   - the classifier: a text message containing "hold" or "block" is held/blocked (the
#     existing keyword trick) and gets one classifier event saying so. Voice notes stay
#     PENDING and are never queued.
#   - no push: a release flips the status but sends no `message.new` over the WebSocket.
#   - `notice_sent` is always False, as on the real gateway (OPEN_QUESTIONS #18).
#   - the sender's notice (contracts 0.6.0, #18): the keyword classifier's HOLD/BLOCK event
#     carries a canned Telugu `notice_text`, and `MessageOut.moderation_notice` is filled in on
#     the AUTHOR's reads only (GET /messages with `X-Mock-User-Id`, the WebSocket's
#     `?user_id=`), from the LATEST event: a moderator's release or block has no notice, so
#     afterwards there is none. The wording is a PLACEHOLDER, not policy: what a sender is
#     told is M4's to write (and a native Telugu reader's to check).
_events: dict[str, list[ModerationEvent]] = {}  # message id -> its trail, oldest first
_MOCK_POLICY_VERSION = "policy@mock"
_MOCK_NOTICE_LANGUAGE = "te"
_MOCK_NOTICES = {
    ModerationAction.HOLD: "మీ సందేశం సమీక్షలో ఉంది.",  # "Your message is under review."
    ModerationAction.BLOCK: "ఈ సందేశం పంపబడలేదు.",  # "This message was not sent."
}


def _as_uuid(value: str) -> uuid.UUID:
    """The moderation contract's ids are UUIDs; this mock's identities need not be (its
    default user is "mock-user-1"). A non-UUID maps to a STABLE UUID, so the same author
    is the same author across calls."""
    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"mock:{value}")


def _append_event(
    message: MessageOut,
    *,
    actor_kind: ModerationActorKind,
    actor_id: uuid.UUID | None,
    label: ModerationLabel,
    action: ModerationAction,
    rationale: str,
    note: str | None = None,
    confidence: float | None = None,
    model_version: str | None = None,
    notice_text: str | None = None,
) -> ModerationEvent:
    event = ModerationEvent(
        id=uuid.uuid4(),
        message_id=_as_uuid(message.id),
        actor_kind=actor_kind,
        actor_id=actor_id,
        label=label,
        action=action,
        confidence=confidence,
        rationale=rationale,
        note=note,
        notice_text=notice_text,
        policy_version=_MOCK_POLICY_VERSION,
        model_version=model_version,
        created_at=datetime.now(timezone.utc),
    )
    _events.setdefault(message.id, []).append(event)
    return event


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
        **_make_transcript(msg, status),
        created_at=datetime.now(timezone.utc),
        status=status,
    )
    key = (msg.target_type.value, msg.target_id)
    _messages.setdefault(key, []).append(record)
    _message_seq[record.id] = next(_seq_counter)
    if status in (MessageStatus.HELD, MessageStatus.BLOCKED):
        blocked = status is MessageStatus.BLOCKED
        _append_event(
            record,
            actor_kind=ModerationActorKind.CLASSIFIER,
            actor_id=None,
            label=ModerationLabel.E_HARMFUL if blocked else ModerationLabel.D_DISPUTATIONAL,
            action=ModerationAction.BLOCK if blocked else ModerationAction.HOLD,
            rationale=f"Mock classifier: the text contained {'block' if blocked else 'hold'!r}.",
            confidence=0.9,
            model_version="mock-classifier",
            notice_text=_MOCK_NOTICES[ModerationAction.BLOCK if blocked else ModerationAction.HOLD],
        )
    return record


def _for_viewer(message: MessageOut, viewer_id: str) -> MessageOut:
    """`moderation_notice` is AUTHOR-ONLY (the same rule as the real gateway's
    notices_for_author), taken from the message's LATEST event: if a moderator ruled after the
    classifier there is no notice on that event and none is shown. Stored records never carry it,
    so a `message.new` echo or another reader's copy cannot leak it."""
    if message.author_id != viewer_id:
        return message
    trail = _events.get(message.id)
    notice = trail[-1].notice_text if trail else None
    if not notice:
        return message
    return message.model_copy(
        update={
            "moderation_notice": notice,
            "moderation_notice_language": _MOCK_NOTICE_LANGUAGE,
        }
    )


def _sync_batch(req: SyncRequest, viewer_id: str = "mock-user-1") -> SyncBatch:
    key = (req.target_type.value, req.target_id)
    history = _messages.get(key, [])
    since_seq = _message_seq.get(req.since_id, 0) if req.since_id else 0
    remaining = [m for m in history if _message_seq[m.id] > since_seq]
    page = remaining[: req.limit]
    return SyncBatch(
        target_type=req.target_type,
        target_id=req.target_id,
        messages=[_for_viewer(m, viewer_id) for m in page],
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
    x_mock_user_id: str = Header(default="mock-user-1"),
) -> SyncBatch:
    await _sleep_for_latency(x_mock_latency_ms)
    req = SyncRequest(target_type=target_type, target_id=target_id, since_id=since, limit=limit)
    return _sync_batch(req, x_mock_user_id)


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


@app.get("/me/settings", response_model=UserSettingsOut)
async def get_my_settings(
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_latency_ms: str | None = Header(default=None),
) -> UserSettingsOut:
    await _sleep_for_latency(x_mock_latency_ms)
    return _settings.get(x_mock_user_id) or UserSettingsOut(preferred_language="te", tts_on=True)


@app.patch("/me/settings", response_model=UserSettingsOut)
async def patch_my_settings(
    update: UserSettingsUpdate,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_latency_ms: str | None = Header(default=None),
) -> UserSettingsOut:
    await _sleep_for_latency(x_mock_latency_ms)
    current = _settings.get(x_mock_user_id) or UserSettingsOut(preferred_language="te", tts_on=True)
    changes = {
        field: getattr(update, field)
        for field in update.model_fields_set
        if field != "contract_version"
    }
    _settings[x_mock_user_id] = current.model_copy(update=changes)
    return _settings[x_mock_user_id]


def _require_moderator(
    x_mock_role: str | None = Header(default=None),
    x_mock_user_id: str = Header(default="mock-user-1"),
) -> uuid.UUID:
    if (x_mock_role or "").lower() not in ("moderator", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient role")
    return _as_uuid(x_mock_user_id)


def _find_message(message_id: str) -> MessageOut | None:
    try:
        uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="message_id must be a UUID") from None
    for history in _messages.values():
        for message in history:
            if message.id == message_id:
                return message
    return None


def _encode_cursor(message_id: str) -> str:
    return base64.urlsafe_b64encode(message_id.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> str:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return str(uuid.UUID(base64.urlsafe_b64decode(padded.encode()).decode()))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="cursor is not a valid cursor") from None


def _queue_item(message: MessageOut) -> ModerationQueueItem:
    trail = _events[message.id]
    return ModerationQueueItem(
        message_id=_as_uuid(message.id),
        author_id=_as_uuid(message.author_id),
        author_display_name=message.author_id,
        target_type=message.target_type.value,
        target_id=_as_uuid(message.target_id),
        original_text=message.text,
        original_media_ref=message.media_ref,
        # The mock has no translator: an obviously fake pivot, so the side-by-side view has
        # two different texts to lay out.
        pivot_text_en=f"[en] {message.text}" if message.text else None,
        latest_event=trail[-1],
        event_count=len(trail),
        created_at=message.created_at,
    )


@app.get("/moderation/queue", response_model=ModerationQueueOut)
async def moderation_queue(
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    _moderator: uuid.UUID = Depends(_require_moderator),
) -> ModerationQueueOut:
    after = _message_seq.get(_decode_cursor(cursor), 0) if cursor is not None else 0
    waiting = sorted(
        (
            m
            for history in _messages.values()
            for m in history
            if m.status is MessageStatus.HELD and _events.get(m.id)
        ),
        key=lambda m: _message_seq[m.id],
    )
    rows = [m for m in waiting if _message_seq[m.id] > after][: limit + 1]
    page = rows[:limit]
    return ModerationQueueOut(
        items=[_queue_item(m) for m in page],
        next_cursor=_encode_cursor(page[-1].id) if len(rows) > limit else None,
    )


@app.get("/moderation/messages/{message_id}/events", response_model=ModerationEventsOut)
async def moderation_events(
    message_id: str, _moderator: uuid.UUID = Depends(_require_moderator)
) -> ModerationEventsOut:
    if _find_message(message_id) is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return ModerationEventsOut(
        message_id=uuid.UUID(message_id), events=list(_events.get(message_id, []))
    )


def _decide(
    message_id: str, body: ModerationReviewIn, moderator_id: uuid.UUID, action: ModerationAction
) -> ModerationReviewOut:
    message = _find_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    # A release may reverse an earlier block; a block applies only to a message still waiting.
    allowed = (
        (MessageStatus.HELD, MessageStatus.BLOCKED)
        if action is ModerationAction.ALLOW
        else (MessageStatus.HELD,)
    )
    if message.status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Message is {message.status.value!r}; it cannot be {action.value.lower()}ed",
        )
    trail = _events.get(message_id)
    if not trail:
        raise HTTPException(status_code=409, detail="Message has no moderation history to review")
    latest = trail[-1]
    if body.expected_event_id is not None and body.expected_event_id != latest.id:
        raise HTTPException(
            status_code=409, detail="The message was decided by someone else since you opened it"
        )
    label = body.label or latest.label
    rationale = (
        "Released by a moderator."
        if action is ModerationAction.ALLOW
        else "Blocked by a moderator."
    )
    if label != latest.label:
        rationale += f" Label corrected from {latest.label.value} to {label.value}."
    message.status = (
        MessageStatus.SENT if action is ModerationAction.ALLOW else MessageStatus.BLOCKED
    )
    event = _append_event(
        message,
        actor_kind=ModerationActorKind.MODERATOR,
        actor_id=moderator_id,
        label=label,
        action=action,
        rationale=rationale,
        note=body.note,
    )
    return ModerationReviewOut(event=event, message_status=message.status.value, notice_sent=False)


@app.post("/moderation/messages/{message_id}/release", response_model=ModerationReviewOut)
async def moderation_release(
    message_id: str,
    body: ModerationReviewIn,
    moderator_id: uuid.UUID = Depends(_require_moderator),
) -> ModerationReviewOut:
    return _decide(message_id, body, moderator_id, ModerationAction.ALLOW)


@app.post("/moderation/messages/{message_id}/block", response_model=ModerationReviewOut)
async def moderation_block(
    message_id: str,
    body: ModerationReviewIn,
    moderator_id: uuid.UUID = Depends(_require_moderator),
) -> ModerationReviewOut:
    return _decide(message_id, body, moderator_id, ModerationAction.BLOCK)


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
                batch = _sync_batch(req, user_id)
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
