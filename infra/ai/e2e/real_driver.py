"""HTTP half of the REAL-services proof (see run_proof_real.sh).

Runs INSIDE the compose network (in the gateway image). Identities (the stub
auth treats any UUID bearer token as a user; run_proof_real.sh inserts the rows
and sets the preferred languages in the database):

    alice  the author                         ...0001
    bob    reads Telugu   (preferred te)      ...0002
    carol  reads English  (preferred en)      ...0003
    dan    reads Hindi    (preferred hi)      ...0004

Subcommands (each prints JSON lines):

    circle                                    alice creates a circle and adds the three
    run <webm> <label> <source_lang> <circle_id> [timeout_s]
        alice sends a voice note to the circle, then each recipient's sync is polled
        until the message is visible to them; for every rendering that has audio, the
        audio is fetched THROUGH THE GATEWAY as that recipient and measured
        (size, duration, loudness), because "has_audio: true" does not mean it plays.
"""

from __future__ import annotations

import io
import json
import math
import os
import struct
import sys
import time
import uuid
import wave

import httpx

BASE = "http://gateway:8000"
ALICE = "00000000-0000-4000-8000-000000000001"
RECIPIENTS = {
    "bob(te)": "00000000-0000-4000-8000-000000000002",
    "carol(en)": "00000000-0000-4000-8000-000000000003",
    "dan(hi)": "00000000-0000-4000-8000-000000000004",
}
TOKEN_ENV = {
    ALICE: "TOKEN_ALICE",
    "00000000-0000-4000-8000-000000000002": "TOKEN_BOB",
    "00000000-0000-4000-8000-000000000003": "TOKEN_CAROL",
    "00000000-0000-4000-8000-000000000004": "TOKEN_DAN",
}


def _auth(user_id: str) -> dict:
    """Bearer header for one of this proof's users. The run script mints a SIGNED token per
    user (`python -m app.tokens`) and passes it as TOKEN_<NAME>, so the proof exercises the
    real verification. With no token in the environment it falls back to the bare UUID, which
    only the gateway's default `legacy` mode accepts."""
    token = os.environ.get(TOKEN_ENV.get(user_id, ""), user_id)
    return {"Authorization": f"Bearer {token}"}


def emit(**fields) -> None:
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def cmd_circle() -> None:
    r = httpx.post(f"{BASE}/circles", json={"name": "real-proof"}, headers=_auth(ALICE), timeout=30)
    r.raise_for_status()
    circle_id = r.json()["id"]
    for name, uid in RECIPIENTS.items():
        m = httpx.post(
            f"{BASE}/circles/{circle_id}/members",
            json={"user_id": uid, "role": "member"},
            headers=_auth(ALICE),
            timeout=30,
        )
        emit(event="member_added", who=name, http=m.status_code)
    emit(event="circle", circle_id=circle_id)


def _audio_stats(token: str, uri: str) -> dict:
    """Fetch a rendering's audio as the recipient and measure what came back."""
    media_id = uri.split(":", 1)[1] if uri.startswith("media:") else uri
    r = httpx.get(f"{BASE}/media/{media_id}", headers=_auth(token), timeout=60)
    stats = {"http": r.status_code, "content_type": r.headers.get("content-type")}
    if r.status_code != 200:
        return stats
    stats["bytes"] = len(r.content)
    try:
        with wave.open(io.BytesIO(r.content)) as w:
            n, rate, width = w.getnframes(), w.getframerate(), w.getsampwidth()
            frames = w.readframes(n)
        stats["seconds"] = round(n / rate, 2)
        if width == 2 and n:
            samples = struct.unpack("<%dh" % (len(frames) // 2), frames)
            rms = math.sqrt(sum(s * s for s in samples) / len(samples))
            stats["rms"] = round(rms)  # 0 would be silence; speech is in the thousands
    except Exception as e:  # noqa: BLE001
        stats["not_a_wav"] = str(e)[:80]
    return stats


def _view(token: str, circle_id: str) -> list[dict]:
    r = httpx.get(
        f"{BASE}/messages",
        params={"target_type": "circle", "target_id": circle_id},
        headers=_auth(token),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["messages"]


def cmd_run(path: str, label: str, source_lang: str, circle_id: str, timeout_s: float) -> None:
    with open(path, "rb") as f:
        data = f.read()
    up = httpx.post(
        f"{BASE}/media",
        params={"format": "webm_opus", "duration_ms": 5000},
        content=data,
        headers=_auth(ALICE),
        timeout=60,
    )
    up.raise_for_status()
    media = up.json()
    resp = httpx.post(
        f"{BASE}/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "circle",
            "target_id": circle_id,
            "kind": "voice",
            "source_lang": source_lang,
            "media_ref": {"uri": media["uri"], "format": media["format"], "duration_ms": 5000},
        },
        headers=_auth(ALICE),
        timeout=60,
    )
    resp.raise_for_status()
    ack = resp.json()
    sent_at = time.time()
    emit(
        event="sent",
        label=label,
        source_lang=source_lang,
        message_id=ack["id"],
        status=ack["status"],
    )

    pending = dict(RECIPIENTS)
    seen_not_visible = {n: 0 for n in RECIPIENTS}
    while pending and time.time() - sent_at < timeout_s:
        for name, uid in list(pending.items()):
            mine = [m for m in _view(uid, circle_id) if m["id"] == ack["id"]]
            if not mine:
                seen_not_visible[name] += 1
                continue
            m = mine[0]
            renderings = []
            for r in m.get("renderings", []):
                audio = r.get("audio")
                renderings.append(
                    {
                        "language": r["language"],
                        "text": r["text"],
                        "degraded_reason": r["degraded_reason"],
                        "audio": _audio_stats(uid, audio["uri"]) if audio else None,
                    }
                )
            emit(
                event="visible",
                recipient=name,
                after_send_s=round(time.time() - sent_at, 2),
                polls_not_visible=seen_not_visible[name],
                status=m["status"],
                transcript=m.get("transcript"),
                transcript_language=m.get("transcript_language"),
                renderings=renderings,
            )
            del pending[name]
        time.sleep(0.5)
    for name in pending:
        emit(event="never_visible", recipient=name, waited_s=round(time.time() - sent_at, 1))


def main(argv: list[str]) -> None:
    cmd, args = argv[1], argv[2:]
    if cmd == "circle":
        cmd_circle()
    elif cmd == "run":
        cmd_run(args[0], args[1], args[2], args[3], float(args[4]) if len(args) > 4 else 300.0)
    else:
        raise SystemExit(f"unknown subcommand {cmd!r}")


if __name__ == "__main__":
    main(sys.argv)
