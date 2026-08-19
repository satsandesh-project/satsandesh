"""
Regenerates services/gateway/tests/fixtures/chat/*.json from the current
contracts/chat/ models. Run this whenever a contract shape changes
deliberately (and bump CONTRACTS_VERSION in contracts/chat/common.py);
test_golden_fixtures_chat.py then catches any further drift. Mirrors
services/ai/tools/generate_fixtures.py.

    PYTHONPATH=../.. ./.venv/Scripts/python.exe tools/generate_chat_fixtures.py
"""

import json
from pathlib import Path

from contracts.chat.circles import (
    Circle,
    CircleCreate,
    Membership,
    MembershipCreate,
    MembershipRole,
)
from contracts.chat.common import MessageKind, MessageStatus, TargetType
from contracts.chat.envelope import SyncBatch, SyncRequest
from contracts.chat.errors import ErrorCode, ErrorPayload
from contracts.chat.messages import AckOut, MessageIn, MessageOut
from pydantic import BaseModel

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "chat"

_CLIENT_MSG_ID = "8f14e45f-ceea-467e-adde-3fb5d3a5fa1c"


def _write(filename: str, instance: BaseModel) -> None:
    path = FIXTURES_DIR / filename
    path.write_text(
        json.dumps(instance.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {path.relative_to(FIXTURES_DIR.parents[2])}")


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    _write(
        "message_in.json",
        MessageIn(
            client_msg_id=_CLIENT_MSG_ID,
            target_type=TargetType.CIRCLE,
            target_id="circle-satsang-evening",
            kind=MessageKind.TEXT,
            text="ఈ రోజు సత్సంగం ఎప్పుడు జరుగుతుంది?",
            source_lang="te",
        ),
    )

    _write(
        "message_out.json",
        MessageOut(
            id="msg-01H8X5Q7Z1",
            author_id="user-elder-42",
            target_type=TargetType.CIRCLE,
            target_id="circle-satsang-evening",
            kind=MessageKind.TEXT,
            text="ఈ రోజు సత్సంగం ఎప్పుడు జరుగుతుంది?",
            created_at="2026-08-17T09:00:00Z",
            status=MessageStatus.DELIVERED,
        ),
    )

    _write(
        "ack_out.json",
        AckOut(client_msg_id=_CLIENT_MSG_ID, id="msg-01H8X5Q7Z1", status=MessageStatus.PENDING),
    )

    _write(
        "circle.json",
        Circle(
            id="circle-satsang-evening",
            name="Evening Satsang",
            created_by="user-moderator-1",
            created_at="2026-08-10T18:30:00Z",
        ),
    )

    _write("circle_create.json", CircleCreate(name="Evening Satsang"))

    _write(
        "membership.json",
        Membership(
            circle_id="circle-satsang-evening",
            user_id="user-elder-42",
            role=MembershipRole.MEMBER,
            joined_at="2026-08-11T07:15:00Z",
        ),
    )

    _write("membership_create.json", MembershipCreate(user_id="user-elder-42"))

    _write(
        "sync_request.json",
        SyncRequest(
            target_type=TargetType.CIRCLE,
            target_id="circle-satsang-evening",
            since_id="msg-01H8X5Q7Z0",
            limit=50,
        ),
    )

    _write(
        "sync_batch.json",
        SyncBatch(
            target_type=TargetType.CIRCLE,
            target_id="circle-satsang-evening",
            messages=[
                MessageOut(
                    id="msg-01H8X5Q7Z1",
                    author_id="user-elder-42",
                    target_type=TargetType.CIRCLE,
                    target_id="circle-satsang-evening",
                    kind=MessageKind.VOICE,
                    text=None,
                    created_at="2026-08-17T09:00:00Z",
                    status=MessageStatus.PENDING,
                ),
            ],
            has_more=False,
        ),
    )

    _write(
        "error_payload.json",
        ErrorPayload(
            code=ErrorCode.NOT_FOUND,
            message="circle 'circle-does-not-exist' does not exist",
            detail={"circle_id": "circle-does-not-exist"},
        ),
    )


if __name__ == "__main__":
    main()
