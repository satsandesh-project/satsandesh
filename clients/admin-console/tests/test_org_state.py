"""
The organisation pages' state: what an admin sees and what a click does.

Same arrangement as test_console_state.py -- Reflex allows State instantiation
under pytest, so handlers are called directly -- over FixtureOrgSource, whose
rules are held to the real routes' by test_org_source.py. What is asserted is
behaviour that matters to the person at the screen: the person is added and
their code is shown (the feature's "done when"), a refused action says why and
keeps what was typed, a dangerous step asks twice, and an announcement cannot
be sent without being read back first.
"""

from __future__ import annotations

import pytest
from admin_console.org import announce, circles, context, members
from admin_console.org.source import FixtureOrgSource
from admin_console.source import SourceError


@pytest.fixture
def source():
    fresh = FixtureOrgSource()
    context.set_source(fresh)
    yield fresh
    context.set_source(None)


def _state(cls, source):
    state = cls()
    state.load()
    return state


# --- Members ---------------------------------------------------------------------------------
def test_an_admin_adds_a_person_and_is_shown_their_code(source) -> None:
    state = _state(members.MembersState, source)
    notices = next(c for c in state.circles if c.kind == "announcement")
    state.set_form_name("  Meera Joshi ")
    state.set_form_language("hi")
    state.toggle_circle(notices.id, True)

    state.create_member()

    assert state.banner_is_error is False and "Meera Joshi has been added" in state.banner
    assert any(u.name == "Meera Joshi" for u in state.users)
    assert state.issued_name == "Meera Joshi" and state.issued_token
    assert state.form_name == "" and not any(c.ticked for c in state.circles), "form is cleared"
    assert any(m.name == "Meera Joshi" for m in source.list_members(notices.id))


def test_adding_without_a_name_asks_for_one_and_creates_nothing(source) -> None:
    state = _state(members.MembersState, source)
    before = len(state.users)
    state.set_form_name("   ")
    state.create_member()
    assert state.banner_is_error and "name" in state.banner
    assert len(source.list_users()) == before and state.issued_token == ""


def test_a_refused_add_says_why_and_keeps_what_was_typed(source, monkeypatch) -> None:
    state = _state(members.MembersState, source)
    state.set_form_name("Meera")

    def refuse(body):
        raise SourceError("That circle does not exist (any more).")

    monkeypatch.setattr(source, "create_user", refuse)
    state.create_member()

    assert state.banner_is_error and "Meera was not added" in state.banner
    assert "does not exist" in state.banner
    assert state.form_name == "Meera", "nothing typed is lost"


def test_the_code_is_shown_until_dismissed_and_then_gone(source) -> None:
    state = _state(members.MembersState, source)
    state.show_signin(state.users[0].id)
    assert state.issued_token and state.issued_name == state.users[0].name
    state.dismiss_code()
    assert state.issued_token == "" and state.issued_qr == "" and state.issued_name == ""


def test_changing_a_role_works_and_a_refusal_resets_the_dropdown(source) -> None:
    state = _state(members.MembersState, source)
    admin = next(u for u in state.users if u.role == "admin")
    other = next(u for u in state.users if u.role == "elder")

    state.change_role(other.id, "moderator")
    assert state.banner_is_error is False
    assert next(u for u in state.users if u.id == other.id).role == "moderator"

    state.change_role(admin.id, "elder")  # the only admin
    assert state.banner_is_error and "at least one admin" in state.banner
    assert next(u for u in state.users if u.id == admin.id).role == "admin", "shows what's stored"


def test_a_failed_refresh_keeps_the_list_on_screen(source, monkeypatch) -> None:
    state = _state(members.MembersState, source)
    shown = list(state.users)

    def down():
        raise SourceError("Could not reach the gateway.")

    monkeypatch.setattr(source, "list_users", down)
    state.load()

    assert state.users == shown, "blanking it would read as 'there is nobody'"
    assert state.banner_is_error and "Could not reach" in state.banner


# --- Circles ---------------------------------------------------------------------------------
def test_a_circle_is_made_with_its_kind(source) -> None:
    state = _state(circles.CirclesState, source)
    state.set_new_name("Bhajan Mandali")
    state.set_new_kind("announcement")
    state.create_circle()
    made = next(c for c in state.circles if c.name == "Bhajan Mandali")
    assert made.kind == "announcement" and state.new_name == ""


def test_a_circle_needs_a_name(source) -> None:
    state = _state(circles.CirclesState, source)
    before = len(state.circles)
    state.set_new_name(" ")
    state.create_circle()
    assert state.banner_is_error and len(source.list_circles()) == before


def test_opening_a_circle_lists_its_people_and_offers_only_those_not_in_it(source) -> None:
    state = _state(circles.CirclesState, source)
    satsang = next(c for c in state.circles if c.name == "Evening Satsang")

    state.open_circle(satsang.id)

    names_in = {m.name for m in state.members}
    names_out = {c.name for c in state.candidates}
    assert names_in and names_out and not (names_in & names_out)
    assert names_in | names_out == {u.name for u in source.list_users()}


def test_adding_someone_moves_them_from_the_offer_to_the_circle(source) -> None:
    state = _state(circles.CirclesState, source)
    satsang = next(c for c in state.circles if c.name == "Evening Satsang")
    state.open_circle(satsang.id)
    newcomer = state.candidates[0]

    state.add_member()  # nobody chosen yet
    assert state.banner_is_error

    state.set_add_user(newcomer.id)
    state.add_member()

    assert state.banner_is_error is False
    assert newcomer.name in {m.name for m in state.members}
    assert newcomer.id not in {c.id for c in state.candidates}


def test_removing_someone_asks_first_and_only_removes_them_from_that_circle(source) -> None:
    state = _state(circles.CirclesState, source)
    satsang = next(c for c in state.circles if c.name == "Evening Satsang")
    state.open_circle(satsang.id)
    person = state.members[0]

    state.ask_remove(person.user_id)
    assert state.pending_remove_id == person.user_id
    assert person.user_id in {m.user_id for m in state.members}, "asking removes no one"

    state.cancel_remove()
    assert state.pending_remove_id == ""
    assert person.user_id in {m.user_id for m in state.members}

    state.ask_remove(person.user_id)
    state.confirm_remove()
    assert person.user_id not in {m.user_id for m in state.members}
    assert person.user_id in {u.id for u in source.list_users()}, "still in the community"
    assert "still a member of the community" in state.banner


def test_renaming_a_circle(source) -> None:
    state = _state(circles.CirclesState, source)
    satsang = next(c for c in state.circles if c.name == "Evening Satsang")
    state.open_circle(satsang.id)
    state.set_rename_text("Morning Satsang")
    state.rename()
    assert state.selected_name == "Morning Satsang"
    assert any(c.name == "Morning Satsang" for c in state.circles)
    state.set_rename_text("  ")
    state.rename()
    assert state.banner_is_error and state.selected_name == "Morning Satsang"


# --- Announcements ---------------------------------------------------------------------------
def test_an_announcement_cannot_be_sent_without_being_checked_first(source) -> None:
    state = _state(announce.AnnounceState, source)
    state.set_text("Satsang moves to 6 pm.")
    sent_before = len(source.list_actions(limit=50))

    state.send()  # skipped the check

    assert len(source.list_actions(limit=50)) == sent_before and not state.reviewing


def test_the_check_shows_where_it_goes_and_how_many_will_get_it(source) -> None:
    state = _state(announce.AnnounceState, source)
    state.set_text("Satsang moves to 6 pm.")

    state.check()

    assert state.reviewing and state.target_words == "all 1 announcement circle"
    assert state.audience == 3  # the three people seeded into Notices


def test_a_blank_announcement_is_not_checked(source) -> None:
    state = _state(announce.AnnounceState, source)
    state.set_text("   ")
    state.check()
    assert state.banner_is_error and not state.reviewing


def test_sending_posts_once_clears_the_page_and_lists_it_under_recent(source) -> None:
    state = _state(announce.AnnounceState, source)
    state.set_text("Satsang moves to 6 pm.")
    state.check()

    state.send()

    assert state.banner_is_error is False and "Sent to 1 circle" in state.banner
    assert state.text == "" and not state.reviewing
    assert len(state.recent) == 1 and "Notices" in state.recent[0].where


def test_a_failed_send_keeps_the_text_and_the_request_id_so_a_retry_posts_once(
    source, monkeypatch
) -> None:
    state = _state(announce.AnnounceState, source)
    state.set_text("Satsang moves to 6 pm.")
    state.check()
    request_id = state.request_id
    real = source.publish_announcement
    calls = []

    def flaky(body):
        calls.append(body.client_msg_id)
        if len(calls) == 1:
            raise SourceError("The gateway did not answer within 10 s. Nothing was changed.")
        return real(body)

    monkeypatch.setattr(source, "publish_announcement", flaky)
    state.send()
    assert state.banner_is_error and state.text and state.request_id == request_id

    state.send()
    assert len(set(calls)) == 1, "the retry carries the same id, so it cannot post twice"
    assert state.text == ""


def test_editing_after_the_check_goes_back_to_composing(source) -> None:
    state = _state(announce.AnnounceState, source)
    state.set_text("one")
    state.check()
    assert state.reviewing
    state.set_text("one, edited")
    assert not state.reviewing, "a changed message must be checked again"


def test_one_circle_can_be_chosen_instead_of_everyone(source) -> None:
    state = _state(announce.AnnounceState, source)
    notices = state.destinations[0]
    state.set_text("hello")
    state.set_target(notices.id)
    state.check()
    assert state.target_words == "“Notices”"
    state.send()
    assert "Sent to 1 circle" in state.banner


def test_with_no_announcement_circle_it_points_to_the_circles_page(source) -> None:
    source._circles = {k: v for k, v in source._circles.items() if v.kind.value == "group"}
    state = _state(announce.AnnounceState, source)
    state.set_text("hello")
    state.check()
    assert state.banner_is_error and "Circles page" in state.banner


# --- the pages build -------------------------------------------------------------------------
@pytest.mark.parametrize(
    "build", [members.members_page, circles.circles_page, announce.announce_page]
)
def test_each_page_builds_a_component_tree(build) -> None:
    assert build() is not None


def test_the_pages_are_registered_on_the_console_with_their_own_routes() -> None:
    from admin_console.admin_console import app

    routes = {route.strip("/") for route in app._unevaluated_pages}
    assert {"members", "circles", "announce"} <= routes
    assert "" in routes or "index" in routes, "the review queue is still there"
