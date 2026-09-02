"""Repository layer: plain functions between routes and the database.

No FastAPI imports here — every function takes a `Session` as its first
argument rather than opening its own, so the caller (a route or a test)
owns the transaction boundary and this module stays testable without HTTP.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Circle, Conversation, Membership, Message, PushSubscription, User

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


def _create_message_impl(
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
    undo_expires_at: datetime | None = None,
) -> tuple[Message, bool]:
    """The real create-or-recover logic, returning whether *this call*
    performed the fresh INSERT (`True`) or recovered an existing row via
    the idempotency SAVEPOINT-recovery path below (`False`). That boolean
    is decided in exactly one place — inside the same try/except that does
    the recovery — so it's an atomic fact about what this call did, not a
    separate check that could race against the insert. `create_message`
    and `create_message_with_created_flag` below are both thin wrappers
    over this; the flag exists only because a caller pushing real-time
    notifications (app/ws.py) needs to know whether to announce a message
    as new (a genuine first insert) or stay silent (an idempotent retry) —
    HTTP's POST /messages doesn't care either way, since it returns the
    same AckOut regardless."""
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
        # Week 4 Phase 8: set by both send paths to now + settings.
        # UNDO_WINDOW_SECONDS; left NULL by any caller that doesn't pass it
        # (there are none left, but the default keeps this optional rather
        # than forcing every existing call site to compute a timestamp it
        # doesn't otherwise need).
        undo_expires_at=undo_expires_at,
    )
    created = True
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
        created = False
    return message, created


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
    undo_expires_at: datetime | None = None,
) -> Message:
    message, _created = _create_message_impl(
        session,
        author_id=author_id,
        target_type=target_type,
        target_user_id=target_user_id,
        target_circle_id=target_circle_id,
        kind=kind,
        text=text,
        original_media_ref=original_media_ref,
        media_duration_ms=media_duration_ms,
        source_lang=source_lang,
        client_msg_id=client_msg_id,
        undo_expires_at=undo_expires_at,
    )
    return message


def create_message_with_created_flag(
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
    undo_expires_at: datetime | None = None,
) -> tuple[Message, bool]:
    """Same as create_message, but also reports whether this call actually
    performed the insert — see _create_message_impl's docstring. Used by
    app/ws.py to decide whether a `message.send` is a genuine first send
    (fan out `message.new`) or an idempotent retry (stay silent), without
    a separate existence check that would race against the insert it's
    supposed to be checking."""
    return _create_message_impl(
        session,
        author_id=author_id,
        target_type=target_type,
        target_user_id=target_user_id,
        target_circle_id=target_circle_id,
        kind=kind,
        text=text,
        original_media_ref=original_media_ref,
        media_duration_ms=media_duration_ms,
        source_lang=source_lang,
        client_msg_id=client_msg_id,
        undo_expires_at=undo_expires_at,
    )


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


def get_message_by_id(session: Session, message_id: uuid.UUID) -> Message | None:
    return session.get(Message, message_id)


def set_message_status(
    session: Session, message_id: uuid.UUID, *, new_status: str, expected: str
) -> bool:
    """Atomically set `messages.status` to `new_status`, but only if it's
    currently `expected` — a WHERE clause, not fetch-then-update, so a
    concurrent transition (e.g. app/undo.py's scheduled fan-out and a
    DELETE /messages/{id} undo racing each other) can't have both sides
    read the old status and both believe they won. Returns True if this
    call performed the transition, False if the row was already in some
    other state."""
    result = session.execute(
        update(Message)
        .where(Message.id == message_id, Message.status == expected)
        .values(status=new_status)
    )
    session.flush()
    return result.rowcount > 0


def get_or_create_user(
    session: Session, *, user_id: uuid.UUID, name: str, preferred_language: str, role: str
) -> User:
    """Provisions a `users` row for a token-derived id if one doesn't exist
    yet. app/auth.py's user_from_token stub (its "Week 3 Phase 7 widening")
    fabricates a wire-layer User purely from the token -- a UUID-shaped
    token becomes that user's real per-user id with no corresponding DB
    row ever inserted. That's fine for routes that only read `user.id`,
    but anything with a FK to `users.id` (circles.created_by,
    memberships.user_id, messages.author_id, ...) hits a ForeignKeyViolation
    the first time a fresh token is actually used to write -- reproduced via
    POST /circles 500ing with exactly that constraint violation. Called
    from user_from_token itself so both call sites (HTTP's get_current_user
    and app/ws.py) are covered from the one place, matching that stub's own
    "single swap point" reasoning."""
    existing = session.get(User, user_id)
    if existing is not None:
        return existing
    user = User(id=user_id, name=name, preferred_language=preferred_language, role=role)
    session.add(user)
    session.flush()
    return user


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


def get_membership(
    session: Session, *, circle_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    """Like is_circle_member, but returns the row itself -- for callers
    that need the caller's own ROLE, not just membership (e.g. whether
    they're privileged enough to grant an elevated role to someone else),
    which a bool can't answer. is_circle_member is left as-is rather than
    rebuilt on top of this, since most call sites only need the bool and
    fetching a row they then discard would be a needless field to keep in
    sync if Membership ever grows heavier columns."""
    return session.execute(
        select(Membership).where(Membership.circle_id == circle_id, Membership.user_id == user_id)
    ).scalar_one_or_none()


def list_circles_for_user(session: Session, *, user_id: uuid.UUID) -> list[Circle]:
    query = (
        select(Circle)
        .join(Membership, Membership.circle_id == Circle.id)
        .where(Membership.user_id == user_id)
    )
    return list(session.execute(query).scalars().all())


def list_member_ids_for_circle(session: Session, *, circle_id: uuid.UUID) -> list[uuid.UUID]:
    """All member user ids for a circle — the fan-out target list a WS
    `message.send` to a circle needs (app/ws.py), the mirror image of
    list_circles_for_user above (circles for a user, not members of a
    circle)."""
    return list(
        session.execute(select(Membership.user_id).where(Membership.circle_id == circle_id))
        .scalars()
        .all()
    )


def upsert_push_subscription(
    session: Session, *, user_id: uuid.UUID, endpoint: str, p256dh: str, auth: str
) -> PushSubscription:
    """Create a subscription row for `endpoint`, or, if one already exists
    (a browser rotating its own keys for the same endpoint — the Push API
    guarantees `endpoint` is unique per subscription), update its
    `p256dh`/`auth` in place rather than erroring or creating a second row
    for what's still the same subscription."""
    existing = session.execute(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    ).scalar_one_or_none()
    if existing is not None:
        existing.p256dh = p256dh
        existing.auth = auth
        session.flush()
        return existing

    subscription = PushSubscription(user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth)
    session.add(subscription)
    session.flush()
    return subscription


def delete_push_subscription(session: Session, *, endpoint: str, user_id: uuid.UUID) -> None:
    """Scoped delete: only removes a subscription that's both this
    `endpoint` and owned by this `user_id`. A no-op, not an error, when
    nothing matches — whether because the endpoint was never subscribed,
    already removed (e.g. app/push.py's own 404/410 cleanup), or belongs
    to someone else entirely."""
    session.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == endpoint, PushSubscription.user_id == user_id
        )
    )
    session.flush()


def list_push_subscriptions_for_user(
    session: Session, *, user_id: uuid.UUID
) -> list[PushSubscription]:
    return list(
        session.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))
        .scalars()
        .all()
    )
