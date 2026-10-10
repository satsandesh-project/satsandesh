"""
The organisation pages' data source, both backends held to the same behaviour.

Every test below runs twice: against FixtureOrgSource (in memory, the default)
and against GatewayOrgSource driving the chat mock's `/admin/*` routes through an
in-process client (real request/response path, no socket, no database). Running
one suite over both is what keeps the fixture honest -- the day it drifts from
the contract's rules, a test fails here instead of a demo lying to someone.

What the mock cannot show (a real database role, the real pipeline) is not
claimed: services/gateway/tests/test_admin_org.py covers the real routes.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from admin_console.org.source import (
    FixtureOrgSource,
    GatewayOrgSource,
    build_org_source_from_env,
)
from admin_console.source import ConsoleConfigError, SourceError
from fastapi.testclient import TestClient

import contracts.chat.mock.admin_org as mock_admin
import contracts.chat.mock.app as mock_app
from contracts.chat.admin_org import (
    AdminActionKind,
    AdminAnnouncementIn,
    AdminCircleCreate,
    AdminUserCreate,
    SiteRole,
)
from contracts.chat.circles import CircleKind, MembershipRole


def _reset_mock() -> None:
    mock_admin._users.clear()
    mock_admin._actions.clear()
    mock_admin._published.clear()
    mock_app._circles.clear()
    mock_app._memberships.clear()
    mock_app._messages.clear()


@pytest.fixture(params=["fixture", "gateway"])
def org(request):
    if request.param == "fixture":
        yield FixtureOrgSource()
        return
    _reset_mock()
    client = TestClient(mock_app.app)
    source = GatewayOrgSource(
        "http://mock",
        "token",
        client=client,
        headers={"X-Mock-Role": "admin", "X-Mock-User-Id": "admin-1"},
    )
    # The mock starts empty and the fixture starts seeded; give the mock an admin
    # so "the last admin" has someone to be.
    source.create_user(AdminUserCreate(name="Asha", role=SiteRole.ADMIN))
    yield source
    _reset_mock()


def _announcement(text="Satsang at 6", **target):
    return AdminAnnouncementIn(text=text, client_msg_id=uuid.uuid4(), **target)


def _kinds(org) -> list[AdminActionKind]:
    return [a.action for a in org.list_actions(limit=50)]


# --- adding a person -------------------------------------------------------------------------
def test_adding_a_person_puts_them_in_the_ticked_circles_and_gives_a_code(org) -> None:
    circle = org.create_circle(AdminCircleCreate(name="Evening Satsang"))

    created = org.create_user(
        AdminUserCreate(name="Meera", preferred_language="hi", circle_ids=[circle.id])
    )

    assert created.user.name == "Meera" and created.access_token
    assert created.circle_ids_joined == [circle.id]
    assert created.user.id in {u.id for u in org.list_users()}
    assert [m.user_id for m in org.list_members(circle.id)] == [created.user.id]
    assert org.list_actions(limit=1)[0].action is AdminActionKind.USER_CREATE


def test_one_unknown_circle_refuses_the_whole_request(org) -> None:
    before = len(org.list_users())
    with pytest.raises(SourceError):
        org.create_user(AdminUserCreate(name="Meera", circle_ids=[str(uuid.uuid4())]))
    assert len(org.list_users()) == before


# --- roles and codes -------------------------------------------------------------------------
def test_a_role_changes_and_the_last_admin_is_protected(org) -> None:
    person = org.create_user(AdminUserCreate(name="Meera")).user
    assert org.set_user_role(person.id, SiteRole.MODERATOR).role is SiteRole.MODERATOR

    admins = [u for u in org.list_users() if u.role is SiteRole.ADMIN]
    for extra in admins[1:]:  # leave exactly one
        org.set_user_role(extra.id, SiteRole.ELDER)
    with pytest.raises(SourceError, match="at least one admin"):
        org.set_user_role(admins[0].id, SiteRole.ELDER)


def test_a_fresh_code_can_be_made_for_someone(org) -> None:
    person = org.create_user(AdminUserCreate(name="Meera")).user
    issued = org.reissue_token(person.id)
    assert issued.user_id == person.id and issued.access_token
    with pytest.raises(SourceError):
        org.reissue_token(str(uuid.uuid4()))


# --- circles ---------------------------------------------------------------------------------
def test_circles_are_made_renamed_and_counted(org) -> None:
    circle = org.create_circle(AdminCircleCreate(name="Old", kind=CircleKind.ANNOUNCEMENT))
    person = org.create_user(AdminUserCreate(name="Meera", circle_ids=[circle.id])).user

    renamed = org.rename_circle(circle.id, "New")

    assert (renamed.name, renamed.kind) == ("New", CircleKind.ANNOUNCEMENT)
    listed = {c.id: c for c in org.list_circles()}
    assert listed[circle.id].name == "New" and listed[circle.id].member_count == 1
    assert person.id
    with pytest.raises(SourceError):
        org.rename_circle(str(uuid.uuid4()), "X")


def test_membership_is_added_changed_and_removed(org) -> None:
    circle = org.create_circle(AdminCircleCreate(name="Chat"))
    person = org.create_user(AdminUserCreate(name="Meera")).user

    org.add_member(circle.id, person.id, MembershipRole.MEMBER)
    again = org.add_member(circle.id, person.id, MembershipRole.ADMIN)
    assert again.role is MembershipRole.MEMBER, "adding someone already in changes nothing"

    changed = org.set_member_role(circle.id, person.id, MembershipRole.MODERATOR)
    assert changed.role is MembershipRole.MODERATOR
    assert [m.name for m in org.list_members(circle.id)] == ["Meera"]

    org.remove_member(circle.id, person.id)
    assert org.list_members(circle.id) == []
    assert person.id in {u.id for u in org.list_users()}, "they stay in the community"
    with pytest.raises(SourceError):
        org.remove_member(circle.id, person.id)


# --- announcements ---------------------------------------------------------------------------
def test_an_announcement_goes_only_into_announcement_circles(org) -> None:
    group = org.create_circle(AdminCircleCreate(name="Chat", kind=CircleKind.GROUP))
    news = org.create_circle(AdminCircleCreate(name="News", kind=CircleKind.ANNOUNCEMENT))

    with pytest.raises(SourceError, match="not an announcement circle"):
        org.publish_announcement(_announcement(circle_id=group.id))

    one = org.publish_announcement(_announcement(circle_id=news.id))
    assert one.circle_ids == [news.id]

    everyone = org.publish_announcement(_announcement(all_circles=True))
    assert news.id in everyone.circle_ids and group.id not in everyone.circle_ids


def test_a_retried_announcement_posts_once(org) -> None:
    news = org.create_circle(AdminCircleCreate(name="News", kind=CircleKind.ANNOUNCEMENT))
    body = _announcement(circle_id=news.id)

    first = org.publish_announcement(body)
    second = org.publish_announcement(body)

    assert first.message_ids == second.message_ids
    assert _kinds(org).count(AdminActionKind.ANNOUNCEMENT_PUBLISH) == 1


def test_all_circles_with_no_announcement_circle_says_so(org) -> None:
    if isinstance(org, FixtureOrgSource):  # the fixture is seeded with one
        org._circles = {k: v for k, v in org._circles.items() if v.kind is CircleKind.GROUP}
    with pytest.raises(SourceError, match="no announcement circle"):
        org.publish_announcement(_announcement(all_circles=True))


# --- only the gateway backend: how failures read ---------------------------------------------
def _gateway(handler) -> GatewayOrgSource:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw")
    return GatewayOrgSource("http://gw", "secret-token", client=client)


@pytest.mark.parametrize(
    "status,words",
    [
        (401, "did not accept this console's token"),
        (403, "not an admin"),
        (404, "Circle not found"),
        (409, "must keep at least one admin"),
        (500, "gateway had a problem"),
    ],
)
def test_every_failure_reads_as_a_sentence(status, words) -> None:
    detail = {
        404: "Circle not found",
        409: "The site must keep at least one admin",
        500: "boom",
    }.get(status)
    source = _gateway(lambda req: httpx.Response(status, json={"detail": detail}))
    with pytest.raises(SourceError) as caught:
        source.list_circles()
    assert words in str(caught.value) or words.lower() in str(caught.value).lower()


def test_a_validation_error_list_becomes_one_line() -> None:
    body = {"detail": [{"loc": ["body", "name"], "msg": "String should have at least 1 character"}]}
    source = _gateway(lambda req: httpx.Response(422, json=body))
    with pytest.raises(SourceError, match="name: String should have at least 1"):
        source.create_circle(AdminCircleCreate(name="x"))


def test_gateway_down_and_slow_are_told_apart() -> None:
    def down(req):
        raise httpx.ConnectError("refused")

    def slow(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(SourceError, match="Could not reach the gateway"):
        _gateway(down).list_circles()
    with pytest.raises(SourceError, match="did not answer"):
        _gateway(slow).list_circles()


def test_an_answer_in_the_wrong_shape_is_reported_not_rendered() -> None:
    source = _gateway(lambda req: httpx.Response(200, json={"items": "nonsense"}))
    with pytest.raises(SourceError, match="shape"):
        source.list_circles()


def test_the_members_list_follows_the_cursor_to_the_end() -> None:
    def user(n):
        return {
            "contract_version": "0.7.0",
            "id": f"u{n}",
            "name": f"P{n}",
            "preferred_language": "te",
            "role": "elder",
            "created_at": "2026-10-08T05:30:00Z",
        }

    pages = {
        None: {"items": [user(1), user(2)], "next_cursor": "c1"},
        "c1": {"items": [user(3)], "next_cursor": None},
    }

    def handler(req):
        return httpx.Response(200, json=pages[req.url.params.get("cursor")])

    assert [u.id for u in _gateway(handler).list_users()] == ["u1", "u2", "u3"]


def test_a_server_repeating_a_cursor_is_an_error_not_a_loop() -> None:
    page = {"items": [], "next_cursor": "same"}
    with pytest.raises(SourceError, match="same page"):
        _gateway(lambda req: httpx.Response(200, json=page)).list_users()


def test_the_token_is_sent_but_never_shown() -> None:
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(403, json={"detail": "Insufficient role"})

    source = _gateway(handler)
    with pytest.raises(SourceError) as caught:
        source.list_circles()
    assert seen["auth"] == "Bearer secret-token"
    assert "secret-token" not in str(caught.value) and "secret-token" not in source.label


# --- choosing the backend --------------------------------------------------------------------
def test_the_default_backend_is_the_sample_data() -> None:
    assert isinstance(build_org_source_from_env({}), FixtureOrgSource)


def test_the_gateway_backend_is_built_from_the_same_variables_as_the_queue() -> None:
    source = build_org_source_from_env(
        {
            "CONSOLE_SOURCE": "gateway",
            "CONSOLE_GATEWAY_URL": "http://gateway:8000/",
            "CONSOLE_GATEWAY_TOKEN": "tok",
            "CONSOLE_GATEWAY_HEADERS": '{"X-Mock-Role": "admin"}',
        }
    )
    assert isinstance(source, GatewayOrgSource)
    assert source._base == "http://gateway:8000"
    assert source._headers == {"Authorization": "Bearer tok", "X-Mock-Role": "admin"}
    assert "tok" not in source.label


@pytest.mark.parametrize(
    "env,variable",
    [
        ({"CONSOLE_SOURCE": "gateway", "CONSOLE_GATEWAY_TOKEN": "t"}, "CONSOLE_GATEWAY_URL"),
        ({"CONSOLE_SOURCE": "gateway", "CONSOLE_GATEWAY_URL": "http://g"}, "CONSOLE_GATEWAY_TOKEN"),
        ({"CONSOLE_SOURCE": "elsewhere"}, "CONSOLE_SOURCE"),
    ],
)
def test_a_bad_environment_fails_naming_the_variable(env, variable) -> None:
    with pytest.raises(ConsoleConfigError, match=variable):
        build_org_source_from_env(env)
