"""contracts/chat/satsang.py: the scheduled satsang broadcast.

Golden fixtures (one canonical example each; they fail if a field drifts without a version bump)
and the rules the models enforce on their own.

The test that matters most here is `test_only_the_create_response_can_carry_a_stream_key`. A stream
key is a credential, and OPEN_QUESTIONS #25 (Week 7: the read path returned every message that was
not out) happened because a read path returned something it held and no test read it as the wrong
party. So this file asserts the *absence* of the key from every other shape, structurally, over
every model in the module -- a new model added later is covered without anyone remembering to.
"""

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from contracts.chat import satsang as satsang_module
from contracts.chat.common import CONTRACTS_VERSION
from contracts.chat.satsang import (
    MAX_TITLE_LENGTH,
    SatsangSessionAdminList,
    SatsangSessionCreate,
    SatsangSessionCreated,
    SatsangSessionList,
    SatsangSessionOut,
    SatsangSessionPublic,
    SatsangStatus,
)
from pydantic import BaseModel, ValidationError

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chat"

MODELS_BY_FIXTURE: dict[str, type[BaseModel]] = {
    "satsang_session_create.json": SatsangSessionCreate,
    "satsang_session_out.json": SatsangSessionOut,
    "satsang_session_created.json": SatsangSessionCreated,
    "satsang_session_public.json": SatsangSessionPublic,
    "satsang_session_list.json": SatsangSessionList,
    "satsang_session_admin_list.json": SatsangSessionAdminList,
}

# Anything that would let a caller speak into the broadcast, or that names a person by their
# internal id, must not appear on an elder-facing shape.
_CREDENTIAL_FIELDS = frozenset({"stream_key", "stream_key_sha256", "ingest_url"})


def _utc(offset_minutes: int = 60) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=offset_minutes)


@pytest.mark.parametrize("filename,model_cls", MODELS_BY_FIXTURE.items())
def test_golden_fixture_parses_and_round_trips(filename, model_cls) -> None:
    import json

    raw = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    assert model_cls.model_validate(raw).model_dump(mode="json") == raw
    assert raw["contract_version"] == CONTRACTS_VERSION


# --- the stream key is a credential -----------------------------------------------------------


def _models_in_module() -> list[type[BaseModel]]:
    return [
        obj
        for _, obj in inspect.getmembers(satsang_module, inspect.isclass)
        if issubclass(obj, BaseModel) and obj.__module__ == satsang_module.__name__
    ]


def test_only_the_create_response_can_carry_a_stream_key() -> None:
    """Structural, over every model in the module: exactly one shape may name the key."""
    carriers = {
        model.__name__
        for model in _models_in_module()
        if _CREDENTIAL_FIELDS & set(model.model_fields)
    }
    assert carriers == {"SatsangSessionCreated"}


def test_an_elder_is_never_told_the_key_the_speakers_internal_id_or_our_bookkeeping() -> None:
    fields = set(SatsangSessionPublic.model_fields)
    assert not (_CREDENTIAL_FIELDS & fields)
    # The speaker is a name to an elder, never a row id; and our own bookkeeping is not theirs.
    for leaked in ("speaker_user_id", "reminder_sent_at", "created_by", "created_at"):
        assert leaked not in fields, leaked
    assert "speaker_name" in fields


def test_the_elder_listing_holds_only_elder_facing_sessions() -> None:
    """The list type is the public shape, so a widening of the admin shape cannot leak through it."""
    assert SatsangSessionList.model_fields["items"].annotation == list[SatsangSessionPublic]
    assert SatsangSessionAdminList.model_fields["items"].annotation == list[SatsangSessionOut]


def test_the_admin_view_of_a_session_does_not_carry_the_key_either() -> None:
    """Only the one-time create/rotate response does -- not the shape every admin read returns."""
    assert not (_CREDENTIAL_FIELDS & set(SatsangSessionOut.model_fields))


# --- the shapes themselves ----------------------------------------------------------------------


def test_a_title_is_trimmed_and_may_not_be_blank_or_huge() -> None:
    body = SatsangSessionCreate(
        title="  Thursday discourse  ", speaker_user_id="u-1", scheduled_start_at=_utc()
    )
    assert body.title == "Thursday discourse"
    for bad in ("", "   ", "x" * (MAX_TITLE_LENGTH + 1)):
        with pytest.raises(ValidationError):
            SatsangSessionCreate(title=bad, speaker_user_id="u-1", scheduled_start_at=_utc())


def test_a_start_time_without_a_timezone_is_refused() -> None:
    """A satsang at "seven o'clock" in no particular zone is a reminder that fires in the wrong
    hour. The column is `timestamptz`, so a naive value would be silently assumed, not rejected --
    this is the first contract here to require awareness, deliberately."""
    # The same instant with its zone stripped. Written this way rather than as a bare
    # `datetime(2026, 10, 15, 19, 0)` so that neither ruff invocation in this repo needs a
    # suppression: the root `ruff check .` that CI runs does not enable flake8-datetimez, while a
    # run from services/gateway does, so a `# noqa: DTZ001` is required by one and reported as
    # unused by the other.
    naive = datetime.now(UTC).replace(tzinfo=None)
    with pytest.raises(ValidationError):
        SatsangSessionCreate(title="t", speaker_user_id="u-1", scheduled_start_at=naive)
    aware = SatsangSessionCreate(
        title="t", speaker_user_id="u-1", scheduled_start_at=_utc()
    ).scheduled_start_at
    assert aware.tzinfo is not None


def test_the_four_statuses_are_exactly_these_words() -> None:
    """Drift guard. These same four words are the `ck_sessions_status` CHECK in the migration, and
    the partial unique index that allows one `live` row keys off this spelling."""
    assert {s.value for s in SatsangStatus} == {"scheduled", "live", "ended", "cancelled"}


def test_a_session_offers_audio_only_while_it_is_live() -> None:
    """`hls_url` is what the join-to-listen screen plays. A scheduled or ended session carrying one
    would send an elder to a stream that is not there; a live one without it has nothing to play."""
    live = SatsangSessionPublic(
        id="s-1",
        title="t",
        speaker_name="Swami",
        scheduled_start_at=_utc(-5),
        status=SatsangStatus.LIVE,
        hls_url="/hls/s-1.m3u8",
    )
    assert live.hls_url == "/hls/s-1.m3u8"
    for not_live in (SatsangStatus.SCHEDULED, SatsangStatus.ENDED, SatsangStatus.CANCELLED):
        with pytest.raises(ValidationError):
            SatsangSessionPublic(
                id="s-1",
                title="t",
                speaker_name="Swami",
                scheduled_start_at=_utc(),
                status=not_live,
                hls_url="/hls/s-1.m3u8",
            )
    with pytest.raises(ValidationError):
        SatsangSessionPublic(
            id="s-1",
            title="t",
            speaker_name="Swami",
            scheduled_start_at=_utc(-5),
            status=SatsangStatus.LIVE,
        )


def test_a_scheduled_session_has_not_started_and_an_ended_one_has() -> None:
    """The same coherence the database enforces (`ck_sessions_started_at_iff_started`,
    `ck_sessions_ended_at_iff_ended`), stated once more where a client can see it."""
    with pytest.raises(ValidationError):
        SatsangSessionOut(
            id="s-1",
            title="t",
            speaker_user_id="u-1",
            speaker_name="Swami",
            scheduled_start_at=_utc(),
            status=SatsangStatus.SCHEDULED,
            started_at=_utc(-1),
            created_by="u-2",
            created_at=_utc(-120),
        )
    with pytest.raises(ValidationError):
        SatsangSessionOut(
            id="s-1",
            title="t",
            speaker_user_id="u-1",
            speaker_name="Swami",
            scheduled_start_at=_utc(-60),
            status=SatsangStatus.ENDED,
            started_at=_utc(-55),
            created_by="u-2",
            created_at=_utc(-120),
        )


def test_a_reminder_records_when_it_was_sent_not_merely_that_it_was() -> None:
    """A boolean cannot survive a crash between send and commit informatively, and cannot say that
    a reminder went out after the session had already started."""
    field = SatsangSessionOut.model_fields["reminder_sent_at"]
    assert field.annotation == datetime | None
    assert field.default is None
