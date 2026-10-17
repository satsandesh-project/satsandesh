"""A satsang is the organisation's, not a circle's, and so its stream address may be a capability URL
(OPEN_QUESTIONS #40, decided 2026-10-16). These are TRIPWIRES: they pass today and exist so that the
day somebody scopes a satsang to a circle, or signs a stream address per listener, a test fails and its
message points here, instead of the two halves of the system silently disagreeing.

Why the two decisions are one decision. `/hls/<session id>.m3u8` has no login: anyone who has the id can
listen (the media server and Caddy never ask who is asking). That is coherent ONLY while every signed-in
user is allowed to hear every satsang, which is what the contract, the model, the listing and the
reminder's audience all say today. A satsang scoped to a circle whose stream anyone with the URL could
hear would be incoherent: the scoping would be a promise about the listing and nothing about the audio.
So adding a circle to a satsang (or to its reminder's audience) REQUIRES an authorised stream first, and
the cheapest one is named in OPEN_QUESTIONS #40.
"""

# ruff: noqa: F811 (the fixtures are imported by name and used as arguments)
import inspect

from contracts.chat import satsang as contract
from pydantic import BaseModel
from sqlalchemy import text

import app.satsang_reminder as reminder
from app.db import repository
from app.db.satsang import SatsangReminderDelivery, SatsangReminderRendering, SatsangSession
from tests.test_satsang_reminder_delivery import (  # noqa: F401 (fixtures are used by name)
    LEAD,
    _headers,
    _member,
    _row,
    _schedule,
    admin,
    clock,
    swami,
)

READ_FIRST = (
    "A satsang scoped to a circle needs an authorised stream first: read OPEN_QUESTIONS #40."
)


def test_no_satsang_table_and_no_contract_shape_mentions_a_circle():
    for table in (SatsangSession, SatsangReminderRendering, SatsangReminderDelivery):
        names = [c.name for c in table.__table__.columns]
        assert not [n for n in names if "circle" in n], (table.__tablename__, names, READ_FIRST)
    models = [
        obj
        for _, obj in inspect.getmembers(contract, inspect.isclass)
        if issubclass(obj, BaseModel) and obj.__module__ == contract.__name__
    ]
    assert models, "the contract has no models to check: this test is broken"
    for model in models:
        fields = [f for f in model.model_fields if "circle" in f]
        assert not fields, (model.__name__, fields, READ_FIRST)


def test_the_reminder_goes_to_every_subscribed_member_whatever_circles_they_are_in(
    client, db_session, admin, swami, clock
):
    inside = _member(db_session, "In a circle", "ta")
    outside = _member(db_session, "In no circle", "ta")
    circle = repository.create_circle(db_session, name="Hyderabad", created_by=admin.id)
    repository.add_member(db_session, circle_id=circle.id, user_id=inside.id)
    db_session.commit()
    recipients = {u.id for u in reminder._recipients(db_session, clock.current)}
    assert {inside.id, outside.id} <= recipients, READ_FIRST


def test_every_signed_in_member_sees_the_satsang_in_the_listing_whatever_their_circles(
    client, db_session, admin, swami
):
    inside = _member(db_session, "In a circle", "ta", subscribed=False)
    outside = _member(db_session, "In no circle", "ta", subscribed=False)
    circle = repository.create_circle(db_session, name="Hyderabad", created_by=admin.id)
    repository.add_member(db_session, circle_id=circle.id, user_id=inside.id)
    db_session.commit()
    made = _schedule(client, admin, swami)
    for who in (inside, outside):
        listing = client.get("/sessions", headers=_headers(who))
        assert listing.status_code == 200, listing.text
        assert [i["id"] for i in listing.json()["items"]] == [made.session.id], READ_FIRST


def test_the_stream_address_is_the_same_for_every_listener_and_names_nobody(
    client, db_session, admin, swami
):
    """A capability URL: the same path for everyone who asks, with no per-listener token in it. If this
    starts to differ, the address has become an authorisation, and #40's decision has moved on."""
    one = _member(db_session, "One", "ta", subscribed=False)
    two = _member(db_session, "Two", "ta", subscribed=False)
    made = _schedule(client, admin, swami)
    db_session.execute(
        text("update sessions set status='live', started_at=now() where id=:i"),
        {"i": made.session.id},
    )
    db_session.commit()
    urls = []
    for who in (one, two):
        items = client.get("/sessions", headers=_headers(who)).json()["items"]
        urls.append(items[0]["hls_url"])
    assert urls[0] == urls[1] == f"/hls/{made.session.id}.m3u8", (urls, READ_FIRST)
    assert str(one.id) not in urls[0] and str(two.id) not in urls[0]
    assert _row(db_session, made.session.id).status == "live"
