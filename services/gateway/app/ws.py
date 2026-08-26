import logging
import uuid

from contracts.chat.common import TargetType
from contracts.chat.envelope import FrameType, RawFrame, SyncBatch, SyncRequest
from contracts.chat.errors import ErrorCode, ErrorPayload
from contracts.chat.messages import AckOut, MessageIn
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import user_from_token
from app.db.base import get_db
from app.db.repository import (
    create_message_with_created_flag,
    find_conversation_id,
    get_messages_since,
    is_circle_member,
    list_member_ids_for_circle,
)
from app.messages import message_to_out
from app.models import User
from app.push import maybe_push_for_message

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    # In-memory only — no Redis, no database. Fine for a single gateway
    # process; won't survive a restart or scale past one instance, which
    # real message delivery eventually needs to fix.
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        connections = self._connections.get(user_id)
        if connections is None:
            return
        connections.discard(websocket)
        if not connections:
            del self._connections[user_id]

    def is_connected(self, user_id: str) -> bool:
        # A user with zero live sockets has no key in _connections at all
        # (disconnect() above deletes the key once its set empties out),
        # not an empty set — .get(user_id) alone would be falsy either way,
        # but being explicit here documents that both cases are handled,
        # not just the more obvious "key present with an empty set" one.
        return bool(self._connections.get(user_id))

    async def send_to_user(
        self, user_id: str, frame: dict, *, exclude: WebSocket | None = None
    ) -> None:
        """Push `frame` to every socket currently connected for `user_id`
        (zero, one, or several — a user can have more than one device
        open), skipping `exclude` if given (the originating socket of a
        send, which already gets a `message.ack` and doesn't need its own
        `message.new` echoed back).

        Iterates a snapshot list, not the live `self._connections[user_id]`
        set: `send_json` below is an await point, and another coroutine's
        `disconnect()` for a different socket of this same user can run
        during that await (e.g. one of the user's other devices dropping
        mid-fan-out) — mutating the live set out from under a `for` loop
        over it raises `RuntimeError: Set changed size during iteration`.
        A copy sidesteps that; per-socket send failures (a connection that
        died between the snapshot and the send) are swallowed individually
        so one dead socket can't stop delivery to the rest.
        """
        connections = [ws for ws in self._connections.get(user_id, ()) if ws is not exclude]
        for websocket in connections:
            try:
                await websocket.send_json(frame)
            except Exception:  # noqa: BLE001 - a socket that died between the
                # snapshot above and this send can raise almost anything
                # depending on the ASGI server; one bad send must not stop
                # delivery to the rest of this fan-out.
                logger.warning("send_to_user: dropping a stale connection for user_id=%s", user_id)
                continue

    async def broadcast(
        self, user_ids: list[str], frame: dict, *, exclude: WebSocket | None = None
    ) -> None:
        for user_id in user_ids:
            await self.send_to_user(user_id, frame, exclude=exclude)


manager = ConnectionManager()


async def _send_error(
    websocket: WebSocket, code: ErrorCode, message: str, *, detail: dict | None = None
) -> None:
    payload = ErrorPayload(code=code, message=message, detail=detail)
    await websocket.send_json(
        {"type": FrameType.ERROR.value, "data": payload.model_dump(mode="json")}
    )


async def _handle_message_send(
    websocket: WebSocket, db: Session, user: User, raw_data: dict
) -> None:
    try:
        msg = MessageIn.model_validate(raw_data)
    except ValidationError as exc:
        await _send_error(
            websocket,
            ErrorCode.VALIDATION_FAILED,
            "invalid message.send payload",
            detail={"error": str(exc)},
        )
        return

    try:
        target_uuid = uuid.UUID(msg.target_id)
    except ValueError:
        await _send_error(websocket, ErrorCode.VALIDATION_FAILED, "target_id must be a valid UUID")
        return

    caller_id = uuid.UUID(user.id)

    target_user_id: uuid.UUID | None = None
    target_circle_id: uuid.UUID | None = None
    if msg.target_type is TargetType.CIRCLE:
        if not is_circle_member(db, circle_id=target_uuid, user_id=caller_id):
            await _send_error(websocket, ErrorCode.UNAUTHORIZED, "Not a member of this circle")
            return
        target_circle_id = target_uuid
    else:
        if target_uuid == caller_id:
            # Not a supported case: docs/SCHEMA_DRAFT.md design question
            # #1a's `conversations` table enforces
            # CHECK (user_a < user_b) — a *strict* inequality, so a
            # self-pair is structurally impossible at the DB level. Never
            # discussed anywhere as a "note to self" feature, just ruled
            # out by the schema. Rejected explicitly here (a clean
            # VALIDATION_FAILED) rather than left to surface as an opaque
            # IntegrityError off that CHECK constraint — and before
            # `recipients` below could ever end up with target_user_id
            # and caller_id as the same id twice.
            await _send_error(
                websocket, ErrorCode.VALIDATION_FAILED, "cannot send a DM to yourself"
            )
            return
        target_user_id = target_uuid

    try:
        # create_message_with_created_flag, not create_message: the
        # `created` flag is decided atomically inside the same
        # try/except that does the SAVEPOINT recovery (see
        # app/db/repository.py's _create_message_impl), so it's a single
        # source of truth about whether *this* call actually inserted —
        # not a separate existence check racing against the insert it's
        # supposed to be checking. A pre-check-then-act version of this
        # (checked before create_message, decided from a plain SELECT)
        # was tried first and is exactly the double-fan-out bug this
        # guards against: two near-simultaneous sends of the same
        # (author_id, client_msg_id) could both see "not yet persisted"
        # before either finished inserting, and both fan out message.new.
        message, created = create_message_with_created_flag(
            db,
            author_id=caller_id,
            target_type=msg.target_type.value,
            target_user_id=target_user_id,
            target_circle_id=target_circle_id,
            kind=msg.kind.value,
            text=msg.text,
            original_media_ref=msg.media_ref.uri if msg.media_ref is not None else None,
            media_duration_ms=msg.media_ref.duration_ms if msg.media_ref is not None else None,
            source_lang=msg.source_lang,
            client_msg_id=msg.client_msg_id,
        )
    except IntegrityError:
        # Persist failed (e.g. target_id doesn't reference a real user/
        # circle) — nothing to push. `db.close()` in the route's finally
        # block rolls back the failed SAVEPOINT's outer transaction state;
        # no explicit rollback needed here (same reasoning
        # tests/test_repository.py's reraise test documents for
        # create_message's own SAVEPOINT-scoped recovery).
        await _send_error(websocket, ErrorCode.INTERNAL_ERROR, "message could not be created")
        return

    # Persist before push: commit only after a successful create_message,
    # before the ack and before any fan-out, so a failed persist can never
    # leak a partial delivery to anyone, sender included.
    db.commit()

    ack = AckOut(client_msg_id=msg.client_msg_id, id=str(message.id), status=message.status)
    await websocket.send_json(
        {"type": FrameType.MESSAGE_ACK.value, "data": ack.model_dump(mode="json")}
    )

    if not created:
        # A retry of an already-delivered send — the sender gets their ack
        # (same server id, proving the earlier attempt landed), but nobody
        # gets a second message.new for it.
        return

    new_frame = {
        "type": FrameType.MESSAGE_NEW.value,
        "data": message_to_out(message).model_dump(mode="json"),
    }

    if msg.target_type is TargetType.CIRCLE:
        recipients = [
            str(member_id)
            for member_id in list_member_ids_for_circle(db, circle_id=target_circle_id)
        ]
    else:
        # The other party, plus the sender's own other connected devices —
        # same reasoning as the circle case (confirmed product decision,
        # Week 3 Phase 5 Step 0, now applied consistently to DMs too): a
        # second open tab/device should see a message you just sent
        # without waiting for a sync.
        recipients = [str(target_user_id), str(caller_id)]

    # A circle poster's own other devices, and now a DM sender's own other
    # devices, are both included in `recipients` above — `exclude` is what
    # keeps either case from also echoing back onto the exact socket that
    # sent it (which already got the ack).
    await manager.broadcast(recipients, new_frame, exclude=websocket)

    # Week 3 Phase 7 (extracted per the correction: app/messages.py's
    # POST /messages needs this exact same trigger, not a second copy of
    # it) — push for whoever won't see the WS frame above live. Always
    # excludes the sender, unlike `recipients` above, which deliberately
    # includes the sender's own other devices for the WS echo.
    maybe_push_for_message(db, message=message, sender_id=caller_id, connection_manager=manager)


async def _handle_sync_request(
    websocket: WebSocket, db: Session, user: User, raw_data: dict
) -> None:
    """Week 3 Phase 6, Path 1 (confirmed Step 0): the offline-queue read
    side, built exactly per the shipped contract — per-conversation,
    resolving `(target_type, target_id)` -> `conversation_id` the same way
    `app/messages.py::get_messages` already does (`find_conversation_id`/
    `is_circle_member`), then calling the existing `get_messages_since`
    unchanged. No new repository function, no contract change — this is a
    read path, so no `db.commit()` and no interaction with Phase 5's
    fan-out/broadcast machinery at all.
    """
    try:
        req = SyncRequest.model_validate(raw_data)
    except ValidationError as exc:
        await _send_error(
            websocket,
            ErrorCode.VALIDATION_FAILED,
            "invalid sync.request payload",
            detail={"error": str(exc)},
        )
        return

    try:
        target_uuid = uuid.UUID(req.target_id)
    except ValueError:
        await _send_error(websocket, ErrorCode.VALIDATION_FAILED, "target_id must be a valid UUID")
        return

    caller_id = uuid.UUID(user.id)

    if req.target_type is TargetType.CIRCLE:
        if not is_circle_member(db, circle_id=target_uuid, user_id=caller_id):
            await _send_error(websocket, ErrorCode.UNAUTHORIZED, "Not a member of this circle")
            return
        conversation_id: uuid.UUID | None = target_uuid
    else:
        # Read-only resolution (find_conversation_id, not
        # _get_or_create_conversation): a sync of a DM that's never been
        # messaged must return empty history, not create a conversations
        # row as a side effect of a sync.
        conversation_id = find_conversation_id(db, caller_id, target_uuid)

    if conversation_id is None:
        batch = SyncBatch(
            target_type=req.target_type, target_id=req.target_id, messages=[], has_more=False
        )
        await websocket.send_json(
            {"type": FrameType.SYNC_BATCH.value, "data": batch.model_dump(mode="json")}
        )
        return

    if req.since_id is not None:
        try:
            since_uuid = uuid.UUID(req.since_id)
        except ValueError:
            await _send_error(
                websocket, ErrorCode.VALIDATION_FAILED, "since_id must be a valid UUID"
            )
            return
    else:
        since_uuid = None

    # Fetch one extra row past the page to learn whether more remain,
    # without a second COUNT query — same trick as
    # app/messages.py::get_messages.
    rows = get_messages_since(
        db, conversation_id=conversation_id, since_id=since_uuid, limit=req.limit + 1
    )
    has_more = len(rows) > req.limit
    page = rows[: req.limit]

    batch = SyncBatch(
        target_type=req.target_type,
        target_id=req.target_id,
        messages=[message_to_out(row) for row in page],
        has_more=has_more,
    )
    await websocket.send_json(
        {"type": FrameType.SYNC_BATCH.value, "data": batch.model_dump(mode="json")}
    )


async def _process_frame(websocket: WebSocket, db: Session, user: User, raw_data: dict) -> None:
    try:
        raw = RawFrame.model_validate(raw_data)
    except ValidationError as exc:
        await _send_error(
            websocket, ErrorCode.VALIDATION_FAILED, "malformed frame", detail={"error": str(exc)}
        )
        return

    if raw.type is FrameType.MESSAGE_SEND:
        await _handle_message_send(websocket, db, user, raw.data)
    elif raw.type is FrameType.SYNC_REQUEST:
        await _handle_sync_request(websocket, db, user, raw.data)
    else:
        await _send_error(
            websocket,
            ErrorCode.VALIDATION_FAILED,
            f"unsupported frame type from a client: {raw.type.value}",
        )


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, db: Session = Depends(get_db)) -> None:
    # Browsers cannot set custom headers (e.g. Authorization) on a WebSocket
    # handshake, so Depends(HTTPBearer) — which reads that header — can't
    # authenticate this route the way it does /me. The token travels as a
    # query param instead; user_from_token is the same stub (soon: real JWT
    # verification) that get_current_user uses, just fed a differently-sourced
    # token string, so there's one auth implementation, not two.
    token = websocket.query_params.get("token")
    try:
        user = user_from_token(token)
    except HTTPException:
        # Deliberately accept() before close(): uvicorn can only send a real
        # WS close frame after the handshake completes, so closing before
        # accept() collapses to a bare HTTP 403 and browsers report ambiguous
        # code 1006 instead of 1008 — see docs/DECISIONS.md for the full
        # mechanism and why the distinction matters for reconnect logic: a
        # bad-token client must stop retrying (1008), a dropped-network
        # client must keep retrying with backoff (1006), and this delivery
        # path is exactly where a client that can't tell them apart would
        # hammer the server under the rural-network conditions it serves.
        await websocket.accept()
        await websocket.close(code=1008, reason="missing_or_invalid_token")
        return

    await manager.connect(user.id, websocket)
    try:
        while True:
            try:
                raw_data = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception:  # noqa: BLE001 - a malformed client payload can
                # raise json.JSONDecodeError, UnicodeDecodeError, or similar;
                # all of them mean the same thing here (bad frame, not a
                # connection problem), so this becomes an error frame, not a
                # close.
                logger.info("receive_json: dropping a non-JSON frame")
                await _send_error(
                    websocket, ErrorCode.VALIDATION_FAILED, "malformed frame: not valid JSON"
                )
                continue

            # A failure processing one frame (bad payload, a DB error, an
            # unexpected bug) must not kill the connection — only an auth
            # failure, handled above before the loop even starts, closes
            # it. Reconnect logic needs the two to stay distinguishable.
            try:
                await _process_frame(websocket, db, user, raw_data)
            except WebSocketDisconnect:
                raise
            except Exception:
                # the "any failure -> error frame, never a close" rule for
                # this route: an unexpected bug here must not look like a
                # dropped connection to a client's reconnect logic.
                logger.exception("_process_frame: unexpected failure handling a WS frame")
                await _send_error(websocket, ErrorCode.INTERNAL_ERROR, "failed to process message")
    except WebSocketDisconnect:
        # Normal control flow, not an error: phones sleep, lifts lose signal,
        # Wi-Fi hands over to 4G. Every one of those looks identical to a
        # deliberate disconnect from the server's side, so this is not logged
        # or treated as a failure — it's the expected way a session ends.
        pass
    finally:
        manager.disconnect(user.id, websocket)
