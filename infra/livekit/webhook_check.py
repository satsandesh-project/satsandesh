"""Run INSIDE the throwaway network by webhook_check.sh, next to a real LiveKit: does LiveKit send
`participant_joined` to a webhook when someone joins, does the gateway's own signature check
(app/livekit_webhook.py `_verify`) accept LiveKit's real signature, and does the event carry the
room name and the person's identity the gateway reads? It joins with LiveKit's official Python SDK,
a real WebRTC connection: a signalling-only WebSocket join does NOT produce the event (tried).
Checks the event and the signature only; it does not run the gateway or change any permission."""
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import livekit
from app.livekit import LiveKitAdmin
from app.livekit_webhook import _verify

BASE = os.environ["LIVEKIT_INTERNAL_URL"]
got = []


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        got.append((self.headers.get("authorization"), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


threading.Thread(target=HTTPServer(("0.0.0.0", 9000), Hook).serve_forever, daemon=True).start()
failed = 0


def check(name, ok, detail=""):
    global failed
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"   [{detail}]"))
    failed += 0 if ok else 1


admin = LiveKitAdmin(BASE)
admin.create_room("room-hook")
token, _ = livekit.join_token(identity="u-hook", name="Hook", room="room-hook", can_publish=False)


async def join():
    from livekit import rtc  # LiveKit's official Python SDK: a real WebRTC join

    room = rtc.Room()
    await asyncio.wait_for(room.connect(BASE.replace("http", "ws"), token), 20)
    await asyncio.sleep(6)  # stay connected while the webhook arrives
    await room.disconnect()


asyncio.run(join())
events = [(a, b) for a, b in got]
kinds = [json.loads(b).get("event") for _, b in events]
print("events received by the webhook:", kinds)
check("LiveKit sent a webhook at all", bool(events))
joined = [(a, b) for a, b in events if json.loads(b).get("event") == "participant_joined"]
check("LiveKit sent participant_joined for a real WebRTC join", bool(joined), kinds)
if joined:
    auth, body = joined[0]
    try:
        _verify(auth, body)
        check("the gateway's own signature check accepts LiveKit's real signature", True)
    except Exception as exc:  # noqa: BLE001
        check("the gateway's own signature check accepts LiveKit's real signature", False, repr(exc))
    event = json.loads(body)
    check("the event names the room we created", (event.get("room") or {}).get("name") == "room-hook", event.get("room"))
    check("the event names the person by identity", (event.get("participant") or {}).get("identity") == "u-hook", event.get("participant"))
    print("fields in the event:", sorted(event), "| participant fields:", sorted(event.get("participant", {})))
print("ALL PASSED" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
