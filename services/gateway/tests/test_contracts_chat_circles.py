from contracts.chat.circles import (
    Circle,
    CircleCreate,
    Membership,
    MembershipCreate,
    MembershipRole,
)


def test_circle_create_only_needs_a_name() -> None:
    body = CircleCreate(name="Satsang Group")
    assert body.name == "Satsang Group"


def test_membership_create_defaults_to_member_role() -> None:
    body = MembershipCreate(user_id="user-2")
    assert body.role is MembershipRole.MEMBER


def test_circle_round_trips() -> None:
    circle = Circle(
        id="circle-1",
        name="Satsang Group",
        created_by="user-1",
        created_at="2026-08-17T09:00:00Z",
    )
    assert circle.name == "Satsang Group"


def test_membership_round_trips() -> None:
    membership = Membership(
        circle_id="circle-1",
        user_id="user-2",
        role=MembershipRole.MODERATOR,
        joined_at="2026-08-17T09:00:00Z",
    )
    assert membership.role is MembershipRole.MODERATOR
