"""contracts/chat/admin_org.py: the `/admin/*` wire shapes -- golden fixtures
(one canonical example each, which fails if a field drifts without a version
bump) and the rules the models enforce on their own."""

import json
from pathlib import Path

import pytest
from contracts.chat.admin_org import (
    AdminActionOut,
    AdminAnnouncementIn,
    AdminCircleCreate,
    AdminCircleOut,
    AdminUserCreate,
    AdminUserCreated,
    SiteRole,
)
from contracts.chat.common import CONTRACTS_VERSION
from pydantic import BaseModel, ValidationError

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chat"

MODELS_BY_FIXTURE: dict[str, type[BaseModel]] = {
    "admin_user_create.json": AdminUserCreate,
    "admin_user_created.json": AdminUserCreated,
    "admin_announcement_in.json": AdminAnnouncementIn,
    "admin_circle_out.json": AdminCircleOut,
    "admin_action_out.json": AdminActionOut,
}

CLIENT_MSG_ID = "0b1d7c52-8f4e-4c0e-9a57-3e0f6f2f7a11"


@pytest.mark.parametrize("filename,model_cls", MODELS_BY_FIXTURE.items())
def test_golden_fixture_parses_and_round_trips(filename, model_cls) -> None:
    raw = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    assert model_cls.model_validate(raw).model_dump(mode="json") == raw
    assert raw["contract_version"] == CONTRACTS_VERSION


def test_a_new_user_defaults_to_an_elder_who_speaks_telugu_in_no_circles() -> None:
    body = AdminUserCreate.model_validate({"name": "  Ravi  "})
    assert body.name == "Ravi"
    assert (body.role, body.preferred_language, body.circle_ids) == (SiteRole.ELDER, "te", [])


@pytest.mark.parametrize("name", ["", "   ", "x" * 121])
def test_a_blank_or_huge_name_is_refused(name) -> None:
    for model in (AdminUserCreate, AdminCircleCreate):
        with pytest.raises(ValidationError):
            model.model_validate({"name": name})


def test_a_role_outside_the_three_site_roles_is_refused() -> None:
    with pytest.raises(ValidationError):
        AdminUserCreate.model_validate({"name": "Ravi", "role": "superuser"})


@pytest.mark.parametrize(
    "target", [{}, {"circle_id": "c1", "all_circles": True}, {"circle_id": None}]
)
def test_an_announcement_needs_exactly_one_target(target) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        AdminAnnouncementIn.model_validate(
            {"text": "hello", "client_msg_id": CLIENT_MSG_ID, **target}
        )


@pytest.mark.parametrize("target", [{"circle_id": "c1"}, {"all_circles": True}])
def test_either_single_target_is_accepted(target) -> None:
    AdminAnnouncementIn.model_validate({"text": "hello", "client_msg_id": CLIENT_MSG_ID, **target})


def test_a_blank_announcement_is_refused() -> None:
    with pytest.raises(ValidationError):
        AdminAnnouncementIn.model_validate(
            {"text": "   ", "circle_id": "c1", "client_msg_id": CLIENT_MSG_ID}
        )
