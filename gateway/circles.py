"""Gateway routes for circles and announcements.

Depends on `backbone/interfaces.py`'s CircleBackbone and nothing else --
no import of any concrete backbone, no knowledge of Postgres, outboxes,
Matrix rooms, or which of those is currently wired up. ADR 0002 is still
open; this file is written so that its outcome changes only which object
gets handed to `set_backbone()` at startup.

If you find yourself needing to import a specific backbone here to make
something work, that's the signal the interface is missing a method --
add it to the contract instead.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from interfaces import BackboneUnavailable, CircleBackbone

router = APIRouter(prefix="/circles", tags=["circles"])

_backbone: Optional[CircleBackbone] = None


def set_backbone(backbone: CircleBackbone) -> None:
    """Wire in the backbone implementation. Called once at startup (see
    main.py); swapped for a fake in tests."""
    global _backbone
    _backbone = backbone


def get_backbone() -> CircleBackbone:
    if _backbone is None:
        raise HTTPException(status_code=503, detail="no backbone configured")
    return _backbone


class CreateCircleRequest(BaseModel):
    name: str


class AddMemberRequest(BaseModel):
    user_id: str


class AnnounceRequest(BaseModel):
    sender_id: str
    body: str


def _unavailable(exc: BackboneUnavailable) -> HTTPException:
    """503, not 500: the gateway is fine, its backbone isn't -- the same
    distinction /db-check already makes for Postgres."""
    return HTTPException(status_code=503, detail=f"backbone unavailable: {exc}")


@router.post("")
async def create_circle(
    req: CreateCircleRequest, backbone: CircleBackbone = Depends(get_backbone)
):
    try:
        circle_id = await backbone.create_circle(req.name)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"circle_id": circle_id}


@router.post("/{circle_id}/members")
async def add_member(
    circle_id: str,
    req: AddMemberRequest,
    backbone: CircleBackbone = Depends(get_backbone),
):
    try:
        await backbone.add_member(circle_id, req.user_id)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"status": "ok"}


@router.delete("/{circle_id}/members/{user_id}")
async def remove_member(
    circle_id: str, user_id: str, backbone: CircleBackbone = Depends(get_backbone)
):
    try:
        await backbone.remove_member(circle_id, user_id)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"status": "ok"}


@router.get("/{circle_id}/members")
async def list_members(
    circle_id: str, backbone: CircleBackbone = Depends(get_backbone)
) -> dict:
    try:
        members: List[str] = await backbone.list_members(circle_id)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"members": members}


@router.post("/{circle_id}/announce")
async def announce(
    circle_id: str,
    req: AnnounceRequest,
    backbone: CircleBackbone = Depends(get_backbone),
):
    try:
        message_id = await backbone.post_announcement(circle_id, req.sender_id, req.body)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"message_id": message_id}


@router.get("/{circle_id}/messages")
async def list_messages(
    circle_id: str,
    limit: int = 50,
    before: Optional[str] = None,
    backbone: CircleBackbone = Depends(get_backbone),
):
    try:
        messages = await backbone.list_messages(circle_id, limit=limit, before=before)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {
        "messages": [
            {
                "id": m.id,
                "circle_id": m.circle_id,
                "sender_id": m.sender_id,
                "body": m.body,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ]
    }
