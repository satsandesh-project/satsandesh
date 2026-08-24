"""Repository layer: plain functions between routes and the database.

No FastAPI imports here — every function takes a `Session` as its first
argument rather than opening its own, so the caller (a route or a test)
owns the transaction boundary and this module stays testable without HTTP.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Circle, Conversation, Membership, Message

# Exact names Alembic generated for the two UNIQUE constraints this module
# recovers from (alembic/versions/8761697bd6bb_initial_schema_users_circles_.py,
# matching the UniqueConstraint(name=...) args in app/db/models.py) — verified
# against the migration, not guessed, since a mismatch here would silently
# swallow every IntegrityError instead of just the expected ones.
_UQ_CONVERSATIONS_USER_PAIR = "uq_conversations_user_pair"
_UQ_MESSAGES_AUTHOR_CLIENT_MSG_ID = "uq_messages_author_client_msg_id"


def _violated_constraint_name(exc: IntegrityError) -> str | None:
    """The Postgres constraint name the DBAPI error names, via psycopg2's
    libpq-backed diagnostics — None if unavailable, handled defensively
    rather than assumed always present."""
    diag = getattr(exc.orig, "diag", None)
    return diag.constraint_name if diag is not None else None


def _get_or_create_conversation(
    session: Session, user_a_id: uuid.UUID, user_b_id: uuid.UUID
) -> Conversation:
    """Resolve a DM's conversation row, canonically ordering the pair to
    match the `CHECK (user_a < user_b)` constraint (docs/SCHEMA_DRAFT.md
    design question #1a) — so Alice-to-Bob and Bob-to-Alice always resolve
    to the same row regardless of who's sending."""
    low, high = sorted([user_a_id, user_b_id])

    existing = session.execute(
        select(Conversation).where(Conversation.user_a == low, Conversation.user_b == high)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    conversation = Conversation(user_a=low, user_b=high)
    try:
        # A SAVEPOINT (begin_nested), not a plain flush: a bare
        # session.rollback() on IntegrityError would roll back the whole
        # transaction, including whatever else the caller already flushed
        # this session (e.g. the users this conversation references). The
        # nested transaction scopes the rollback to just this insert.
        with session.begin_nested():
            session.add(conversation)
            session.flush()
    except IntegrityError as exc:
        # Two concurrent first-messages between the same pair both miss the
        # SELECT above and both try to insert — one wins, the other hits
        # the UNIQUE (user_a, user_b) constraint. The nested transaction
        # above already rolled back just this insert; re-select the row
        # the winner created instead of surfacing a 500 to the caller.
        # Only treat *that specific* constraint as "expected": any other
        # IntegrityError here (a FK violation because a user was deleted
        # mid-request, say) is a real failure, not a race to recover from.
        if _violated_constraint_name(exc) != _UQ_CONVERSATIONS_USER_PAIR:
            raise
        conversation = session.execute(
            select(Conversation).where(Conversation.user_a == low, Conversation.user_b == high)
        ).scalar_one()
    return conversation


def find_conversation_id(
    session: Session, user_a_id: uuid.UUID, user_b_id: uuid.UUID
) -> uuid.UUID | None:
    """Read-only counterpart to _get_or_create_conversation: the DM's
    conversation id if this pair has ever exchanged a message, None if
    not. Used by the sync/read path (GET /messages), which must not
    create a conversations row as a side effect of a read the way the
    write path's get-or-create legitimately does."""
    low, high = sorted([user_a_id, user_b_id])
    existing = session.execute(
        select(Conversation).where(Conversation.user_a == low, Conversation.user_b == high)
    ).scalar_one_or_none()
    return existing.id if existing is not None else None


def create_message(
    session: Session,
    *,
    author_id: uuid.UUID,
    target_type: str,
    target_user_id: uuid.UUID | None = None,
    target_circle_id: uuid.UUID | None = None,
    kind: str,
    text: str | None = None,
    original_media_ref: str | None = None,
    media_duration_ms: int | None = None,
    source_lang: str | None = None,
    client_msg_id: uuid.UUID,
) -> Message:
    if target_type == "circle":
        conversation_id = target_circle_id
    else:
        conversation_id = _get_or_create_conversation(session, author_id, target_user_id).id

    message = Message(
        author_id=author_id,
        target_type=target_type,
        target_user_id=target_user_id,
        target_circle_id=target_circle_id,
        conversation_id=conversation_id,
        kind=kind,
        text=text,
        original_media_ref=original_media_ref,
        media_duration_ms=media_duration_ms,
        source_lang=source_lang,
        client_msg_id=client_msg_id,
        # undo_expires_at left NULL: Week 4 Phase 8's concern (design
        # question #7), not this phase's — not an oversight.
    )
    try:
        # Idempotency has to be try/insert-then-catch, not check-then-insert:
        # a check-then-insert has a race window between the SELECT and the
        # INSERT where two concurrent retries of the same (author_id,
        # client_msg_id) can both pass the check and both insert — exactly
        # the concurrent-retry scenario this UNIQUE constraint exists to
        # guard against. Catching the IntegrityError and re-selecting is
        # race-free because the database, not app code, arbitrates the
        # conflict. A SAVEPOINT (begin_nested), not a plain flush: a bare
        # session.rollback() would undo the whole transaction, including
        # whatever else this session already flushed (e.g. the
        # get-or-create conversation insert above), not just this insert.
        with session.begin_nested():
            session.add(message)
            session.flush()
    except IntegrityError as exc:
        # Only treat *that specific* constraint as an idempotent retry: any
        # other IntegrityError here (e.g. a dangling target FK, or one of
        # the CHECK constraints in app/db/models.py) is a real validation
        # failure that must surface, not get misreported as "duplicate".
        if _violated_constraint_name(exc) != _UQ_MESSAGES_AUTHOR_CLIENT_MSG_ID:
            raise
        message = session.execute(
            select(Message).where(
                Message.author_id == author_id, Message.client_msg_id == client_msg_id
            )
        ).scalar_one()
    return message


def get_messages_since(
    session: Session,
    *,
    conversation_id: uuid.UUID,
    since_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[Message]:
    # deleted_at IS NULL matches the partial index's own WHERE clause
    # (ix_messages_conversation_id_id_not_deleted) exactly, so Postgres can
    # use it directly instead of falling back to a full index/table scan —
    # this predicate is required for correctness, not optional just because
    # the index also carries it.
    query = select(Message).where(
        Message.conversation_id == conversation_id, Message.deleted_at.is_(None)
    )
    if since_id is not None:
        query = query.where(Message.id > since_id)
    query = query.order_by(Message.id).limit(limit)
    return list(session.execute(query).scalars().all())


def create_circle(session: Session, *, name: str, created_by: uuid.UUID) -> Circle:
    circle = Circle(name=name, created_by=created_by)
    session.add(circle)
    session.flush()
    return circle


def add_member(
    session: Session, *, circle_id: uuid.UUID, user_id: uuid.UUID, role: str = "member"
) -> Membership:
    membership = Membership(circle_id=circle_id, user_id=user_id, role=role)
    session.add(membership)
    session.flush()
    return membership


def is_circle_member(session: Session, *, circle_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Whether user_id has a memberships row for circle_id, any role — the
    authorization check every circle-scoped route needs (posting to a
    circle, syncing a circle's messages, adding a new member)."""
    existing = session.execute(
        select(Membership).where(Membership.circle_id == circle_id, Membership.user_id == user_id)
    ).scalar_one_or_none()
    return existing is not None


def list_circles_for_user(session: Session, *, user_id: uuid.UUID) -> list[Circle]:
    query = (
        select(Circle)
        .join(Membership, Membership.circle_id == Circle.id)
        .where(Membership.user_id == user_id)
    )
    return list(session.execute(query).scalars().all())
