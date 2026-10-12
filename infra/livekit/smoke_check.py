"""Run INSIDE the compose network by smoke.sh: the gateway's own app/livekit.py against a real
LiveKit server. Checks that LiveKit accepts what the gateway sends (a join token, the admin
calls) and refuses what it should (a room nobody created, a token signed with another secret).
It does not connect a WebRTC client: that needs a browser."""

import os
import sys

import httpx
from app import livekit
from app.livekit import LiveKitAdmin
from jose import jwt

BASE = os.environ["LIVEKIT_INTERNAL_URL"]
failed = 0


def check(name, ok, detail=""):
    global failed
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"   [{detail}]"))
    failed += 0 if ok else 1


def validate(token):
    return httpx.get(f"{BASE}/rtc/validate", params={"access_token": token}, timeout=10).status_code


admin = LiveKitAdmin(BASE)
room = "room-smoke"

admin.create_room(room)
check("CreateRoom is accepted (an admin token with roomCreate)", True)
admin.create_room(room)
check("CreateRoom twice is harmless (idempotent)", True)

listener, _ = livekit.join_token(identity="u-1", name="Anand", room=room, can_publish=False)
lead, _ = livekit.join_token(identity="u-2", name="Bhavya", room=room, can_publish=True)
check(
    "a listener's join token is accepted by LiveKit", validate(listener) == 200, validate(listener)
)
check("a lead's join token is accepted by LiveKit", validate(lead) == 200, validate(lead))

elsewhere, _ = livekit.join_token(
    identity="u-1", name="n", room="room-nobody-made", can_publish=False
)
check(
    "a room the gateway never created cannot be joined (auto_create is off)",
    validate(elsewhere) in (404, 400),
    validate(elsewhere),
)

forged = jwt.encode(
    {"iss": os.environ["LIVEKIT_API_KEY"], "sub": "u-9", "video": {"room": room, "roomJoin": True}},
    "not-the-secret-not-the-secret-not-the-secret",
    algorithm="HS256",
)
check(
    "a token signed with another secret is refused",
    validate(forged) in (401, 403),
    validate(forged),
)

check(
    "UpdateParticipant for someone who is not connected is 'nothing to change' (False)",
    admin.set_can_publish(room, "u-1", True) is False,
)

admin.delete_room(room)
check("DeleteRoom is accepted", True)
check(
    "after DeleteRoom the room can no longer be joined",
    validate(listener) in (404, 400),
    validate(listener),
)
admin.delete_room(room)
check("DeleteRoom of a room that is gone is harmless", True)

print("ALL PASSED" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
