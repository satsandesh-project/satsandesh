"""HTTP half of the end-to-end pipeline proof (see run_proof.sh).

Runs INSIDE the compose network (in the gateway image) so it reaches the
gateway as http://gateway:8000, exactly as any other service would. Three fixed
identities -- the gateway's stub auth treats any UUID bearer token as a user:

    alice  the author           (fixed id ...0001)
    bob    the recipient        (fixed id ...0002)
    mod    a moderator          (fixed id ...0003; run_proof.sh sets its role
                                 in the database -- the stub cannot grant one)

Subcommands (each prints one JSON object per line, so run_proof.sh can read
them and a human can too):

    send  <samples/file.webm> <label> [source_lang]
    watch <message_id> [timeout_s] [sent_at]   poll BOB's sync until it appears
    queue                                 the moderator queue
    events <message_id>                   the moderation trail
    release <message_id>
    mine  <message_id>                    ALICE's own view of her message

Only the Python standard library plus httpx (already in the gateway image).
"""

from __future__ import annotations

import json
import sys
import time
import uuid

import httpx

BASE = "http://gateway:8000"
ALICE = "00000000-0000-4000-8000-000000000001"
BOB = "00000000-0000-4000-8000-000000000002"
MOD = "00000000-0000-4000-8000-000000000003"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def emit(**fields) -> None:
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def cmd_send(path: str, label: str, source_lang: str | None) -> None:
    with open(path, "rb") as f:
        data = f.read()
    t0 = time.time()
    up = httpx.post(
        f"{BASE}/media",
        params={"format": "webm_opus", "duration_ms": 4000},
        content=data,
        headers=_auth(ALICE),
        timeout=60,
    )
    up.raise_for_status()
    media = up.json()
    body = {
        "client_msg_id": str(uuid.uuid4()),
        "target_type": "user",
        "target_id": BOB,
        "kind": "voice",
        "media_ref": {"uri": media["uri"], "format": media["format"], "duration_ms": 4000},
    }
    if source_lang:
        body["source_lang"] = source_lang
    resp = httpx.post(f"{BASE}/messages", json=body, headers=_auth(ALICE), timeout=60)
    resp.raise_for_status()
    ack = resp.json()
    emit(
        event="sent",
        label=label,
        message_id=ack["id"],
        ack_status=ack["status"],
        bytes=len(data),
        sent_at=time.time(),
        upload_and_send_s=round(time.time() - t0, 3),
    )


def _bobs_view() -> list[dict]:
    r = httpx.get(
        f"{BASE}/messages",
        params={"target_type": "user", "target_id": ALICE},
        headers=_auth(BOB),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["messages"]


def cmd_watch(message_id: str, timeout_s: float, sent_at: float | None = None) -> None:
    """Poll BOB's sync until the message shows up, recording what he sees."""
    start = time.time()
    polls_before = 0
    while time.time() - start < timeout_s:
        mine = [m for m in _bobs_view() if m["id"] == message_id]
        if mine:
            m = mine[0]
            emit(
                event="visible_to_recipient",
                after_watch_started_s=round(time.time() - start, 2),
                after_send_s=round(time.time() - sent_at, 2) if sent_at else None,
                polls_where_it_was_not_visible=polls_before,
                status=m["status"],
                transcript=m.get("transcript"),
                transcript_language=m.get("transcript_language"),
                renderings=[
                    {
                        "language": r["language"],
                        "text": r["text"],
                        "has_audio": r["audio"] is not None,
                        "degraded_reason": r["degraded_reason"],
                    }
                    for r in m.get("renderings", [])
                ],
                has_original_media_ref=m.get("media_ref") is not None,
            )
            return
        polls_before += 1
        time.sleep(0.25)
    emit(
        event="never_visible_to_recipient",
        waited_s=round(time.time() - start, 2),
        polls=polls_before,
    )


def cmd_mine(message_id: str) -> None:
    r = httpx.get(
        f"{BASE}/messages",
        params={"target_type": "user", "target_id": BOB},
        headers=_auth(ALICE),
        timeout=30,
    )
    r.raise_for_status()
    mine = [m for m in r.json()["messages"] if m["id"] == message_id]
    emit(event="authors_view", status=mine[0]["status"] if mine else None)


def cmd_queue() -> None:
    r = httpx.get(f"{BASE}/moderation/queue", headers=_auth(MOD), timeout=30)
    emit(event="queue", http=r.status_code, items=[
        {
            "message_id": i["message_id"],
            "event_count": i["event_count"],
            "latest_actor": i["latest_event"]["actor_kind"],
            "latest_action": i["latest_event"]["action"],
            "rationale": i["latest_event"]["rationale"],
            "has_audio": i["original_media_ref"] is not None,
        }
        for i in (r.json().get("items", []) if r.status_code == 200 else [])
    ])


def cmd_events(message_id: str) -> None:
    r = httpx.get(f"{BASE}/moderation/messages/{message_id}/events", headers=_auth(MOD), timeout=30)
    emit(event="events", http=r.status_code, trail=[
        {
            "actor": e["actor_kind"],
            "action": e["action"],
            "label": e["label"],
            "degraded": e["degraded"],
            "policy": e["policy_version"],
            "model": e["model_version"],
        }
        for e in (r.json().get("events", []) if r.status_code == 200 else [])
    ])


def cmd_release(message_id: str) -> None:
    r = httpx.post(
        f"{BASE}/moderation/messages/{message_id}/release",
        json={"note": "released during the end-to-end proof"},
        headers=_auth(MOD),
        timeout=30,
    )
    emit(event="released", http=r.status_code, body=r.json() if r.status_code == 200 else r.text)


def main(argv: list[str]) -> None:
    cmd, args = argv[1], argv[2:]
    if cmd == "send":
        cmd_send(args[0], args[1], args[2] if len(args) > 2 else None)
    elif cmd == "watch":
        cmd_watch(
            args[0],
            float(args[1]) if len(args) > 1 else 240.0,
            float(args[2]) if len(args) > 2 else None,
        )
    elif cmd == "mine":
        cmd_mine(args[0])
    elif cmd == "queue":
        cmd_queue()
    elif cmd == "events":
        cmd_events(args[0])
    elif cmd == "release":
        cmd_release(args[0])
    else:
        raise SystemExit(f"unknown subcommand {cmd!r}")


if __name__ == "__main__":
    main(sys.argv)
