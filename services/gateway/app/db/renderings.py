"""The pipeline's per-message output: transcript, English pivot, renderings.

Same rules as app/db/repository.py and app/db/moderation.py: plain functions
over a Session, no framework, the caller owns commit/rollback.

FIXED AT DELIVERY (M1's answer on #83). Every write here is conditional on
the message still being `pending`, in the statement itself -- not a check
followed by a write, which a concurrent delivery (app/messages.py's
fan_out_message flips `pending` -> `sent`) could slip between. Once a message
has gone out, what its receivers saw must not change underneath them; a
write that arrives late returns False and changes nothing. All writes are
idempotent: a retried job re-writes the stage it already wrote.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence

import sqlalchemy as sa
from contracts.chat.common import AudioFormat, MediaRef
from contracts.chat.renderings import Rendering, RenderingDegradedReason
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import MediaObject, Message, MessageRendering

# A message's renderings and transcript are visible on the wire only once the
# message is out. Pending (still in the undo window / pipeline), held,
# blocked and cancelled messages expose neither.
VISIBLE_STATUSES = ("sent", "delivered")


def _pending(message_id: uuid.UUID):
    return sa.exists().where(Message.id == message_id, Message.status == "pending")


def upsert_rendering(
    session: Session,
    *,
    message_id: uuid.UUID,
    language: str,
    text: str,
    audio_media_object_id: uuid.UUID | None = None,
    degraded_reason: str | None = None,
    model_version_translate: str | None = None,
    model_version_tts: str | None = None,
) -> bool:
    """Insert or replace one language's rendering. True if written, False if
    the message is no longer `pending` (nothing is changed)."""
    # CAST(... AS type) on every value: an INSERT ... SELECT of a bare NULL is
    # typed text by Postgres and then refused by a uuid column.
    source = select(
        sa.cast(sa.literal(message_id), PGUUID(as_uuid=True)),
        sa.cast(sa.literal(language), sa.Text),
        sa.cast(sa.literal(text), sa.Text),
        sa.cast(sa.literal(audio_media_object_id), PGUUID(as_uuid=True)),
        sa.cast(sa.literal(degraded_reason), sa.Text),
        sa.cast(sa.literal(model_version_translate), sa.Text),
        sa.cast(sa.literal(model_version_tts), sa.Text),
    ).where(_pending(message_id))
    insert = pg_insert(MessageRendering).from_select(
        [
            "message_id",
            "language",
            "text",
            "audio_media_object_id",
            "degraded_reason",
            "model_version_translate",
            "model_version_tts",
        ],
        source,
    )
    stmt = insert.on_conflict_do_update(
        index_elements=["message_id", "language"],
        set_={
            "text": insert.excluded.text,
            "audio_media_object_id": insert.excluded.audio_media_object_id,
            "degraded_reason": insert.excluded.degraded_reason,
            "model_version_translate": insert.excluded.model_version_translate,
            "model_version_tts": insert.excluded.model_version_tts,
        },
    )
    return session.execute(stmt).rowcount > 0


def set_message_transcript(
    session: Session, message_id: uuid.UUID, transcript: str, language: str
) -> bool:
    """Store a voice note's transcript (and its language). True if written,
    False if the message is no longer `pending`. A text message is refused by
    the table's own CHECK."""
    result = session.execute(
        update(Message)
        .where(Message.id == message_id, Message.status == "pending")
        .values(transcript=transcript, transcript_language=language)
    )
    return result.rowcount > 0


def set_message_pivot_text(session: Session, message_id: uuid.UUID, pivot_text_en: str) -> bool:
    """Store the English pivot the classifier reads (and a moderator sees
    beside the original). True if written, False if no longer `pending`."""
    result = session.execute(
        update(Message)
        .where(Message.id == message_id, Message.status == "pending")
        .values(pivot_text_en=pivot_text_en)
    )
    return result.rowcount > 0


def list_renderings(
    session: Session, message_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[MessageRendering]]:
    """Renderings for many messages in ONE query (a sync page must not cost a
    query per message), ordered by language. Messages with none are absent."""
    ids = list(message_ids)
    if not ids:
        return {}
    rows = session.scalars(
        select(MessageRendering)
        .where(MessageRendering.message_id.in_(ids))
        .order_by(MessageRendering.message_id, MessageRendering.language)
    )
    grouped: dict[uuid.UUID, list[MessageRendering]] = defaultdict(list)
    for row in rows:
        grouped[row.message_id].append(row)
    return dict(grouped)


def rendering_to_out(
    session: Session, rendering: MessageRendering, media: MediaObject | None = None
) -> Rendering:
    """A stored rendering as the chat contract's wire shape. The audio ref is
    built from the media row (format and duration live there, once); a
    rendering whose audio is gone -- never made, or swept by retention -- has
    `audio=None`."""
    audio = None
    if rendering.audio_media_object_id is not None:
        media = media or session.get(MediaObject, rendering.audio_media_object_id)
        if media is not None:
            audio = MediaRef(
                uri=f"media:{media.id}",
                format=AudioFormat(media.format),
                duration_ms=media.duration_ms,
            )
    return Rendering(
        language=rendering.language,
        text=rendering.text,
        audio=audio,
        degraded_reason=(
            RenderingDegradedReason(rendering.degraded_reason)
            if rendering.degraded_reason
            else None
        ),
    )


def renderings_for_wire(
    session: Session, messages: Sequence[Message]
) -> dict[str, list[Rendering]]:
    """Wire-shaped renderings for a page of messages, keyed by message id
    (str), in at most two queries whatever the page size. Only messages that
    are out (VISIBLE_STATUSES) get any."""
    visible = [m.id for m in messages if m.status in VISIBLE_STATUSES]
    stored = list_renderings(session, visible)
    if not stored:
        return {}
    media_ids = {
        r.audio_media_object_id
        for rows in stored.values()
        for r in rows
        if r.audio_media_object_id is not None
    }
    media = (
        {m.id: m for m in session.scalars(select(MediaObject).where(MediaObject.id.in_(media_ids)))}
        if media_ids
        else {}
    )
    return {
        str(message_id): [
            rendering_to_out(session, r, media.get(r.audio_media_object_id)) for r in rows
        ]
        for message_id, rows in stored.items()
    }
