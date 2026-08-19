"""
Golden fixtures are the Week-1 chat-contract freeze made concrete: each
committed JSON file in fixtures/chat/ is one canonical example of a payload.
If a field is renamed, retyped, or dropped without a version bump, this test
fails in CI instead of the drift being caught by memory later. Mirrors
services/ai/tests/test_golden_fixtures.py.
"""

import json
from pathlib import Path

import pytest
from contracts.chat.circles import Circle, CircleCreate, Membership, MembershipCreate
from contracts.chat.envelope import SyncBatch, SyncRequest
from contracts.chat.errors import ErrorPayload
from contracts.chat.messages import AckOut, MessageIn, MessageOut
from pydantic import BaseModel

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chat"

MODELS_BY_FIXTURE: dict[str, type[BaseModel]] = {
    "message_in.json": MessageIn,
    "message_out.json": MessageOut,
    "ack_out.json": AckOut,
    "circle.json": Circle,
    "circle_create.json": CircleCreate,
    "membership.json": Membership,
    "membership_create.json": MembershipCreate,
    "sync_request.json": SyncRequest,
    "sync_batch.json": SyncBatch,
    "error_payload.json": ErrorPayload,
}


@pytest.mark.parametrize("filename,model_cls", MODELS_BY_FIXTURE.items())
def test_golden_fixture_parses_and_round_trips(filename: str, model_cls: type[BaseModel]) -> None:
    fixture_path = FIXTURES_DIR / filename
    assert fixture_path.exists(), f"missing committed golden fixture: {filename}"
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    instance = model_cls.model_validate(raw)
    assert instance.model_dump(mode="json") == raw
