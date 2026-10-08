"""The week-8 gate walked clause by clause, against the GATE stack (a staging-equivalent built
from team/main). Runs inside the stack's docker network. Every subcommand prints raw results.

  onboard                    a family member invites a Telugu elder and a Hindi receiver
  inspect <msg_id> <viewer>  what `viewer` (elder|receiver) sees: rendering audio measured, the
                             original audio fetched as the receiver, the writing script of every
                             text counted
  moderation <msg_id>        the moderator's view: the queue and the message's event trail (direct
                             to the gateway: /moderation* is not routed by Caddy)
  circles                    an announcement circle on the Postgres backbone: who may post
"""

from __future__ import annotations

import io
import json
import math
import os
import struct
import sys
import unicodedata
import wave
from collections import Counter

import httpx

GATEWAY = "http://gateway:8000"  # direct: the unrouted paths live here
CADDY = "http://caddy:80"  # what a browser reaches
FAMILY = "00000000-0000-4000-8000-0000000000f1"
MODERATOR = "00000000-0000-4000-8000-0000000000a1"
# /moderation/* accepts only a SIGNED token: inspect.sh mints one and passes it in. With none
# in the environment this falls back to the bare UUID, which the gateway now refuses (401).
MODERATOR_TOKEN = os.environ.get("MODERATOR_TOKEN", MODERATOR)
STATE = "/state/gate_state.json"


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def show(label, value):
    print(f"{label}: {json.dumps(value, ensure_ascii=False)}", flush=True)


def load_state():
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def scripts_of(text):
    """Letters per Unicode script (the first word of each letter's character name)."""
    counts = Counter()
    for ch in text:
        if ch.isalpha():
            counts[unicodedata.name(ch, "UNKNOWN").split()[0]] += 1
    return counts


def script_report(label, text, expected):
    counts = scripts_of(text)
    letters = sum(counts.values()) or 1
    share = round(100 * counts.get(expected, 0) / letters)
    show(
        f"SCRIPT {label}",
        {
            "expected": expected,
            f"{expected.lower()}_letters_pct": share,
            "letters_by_script": dict(counts.most_common(4)),
            "text": text[:90],
        },
    )
    return share


def audio_stats(token, uri):
    media_id = uri.split(":", 1)[1] if uri.startswith("media:") else uri
    r = httpx.get(f"{CADDY}/media/{media_id}", headers=auth(token), timeout=60)
    out = {"http": r.status_code, "content_type": r.headers.get("content-type")}
    if r.status_code != 200:
        return out
    out["bytes"] = len(r.content)
    out["magic"] = r.content[:4].hex()
    try:
        with wave.open(io.BytesIO(r.content)) as w:
            n, rate, width = w.getnframes(), w.getframerate(), w.getsampwidth()
            frames = w.readframes(n)
        out["seconds"] = round(n / rate, 2)
        if width == 2 and n:
            samples = struct.unpack("<%dh" % (len(frames) // 2), frames)
            out["rms"] = round(math.sqrt(sum(s * s for s in samples) / len(samples)))
    except Exception:  # noqa: BLE001 - not a WAV (e.g. the original webm): size and magic say enough
        pass
    return out


def cmd_onboard():
    results = {}
    for key, name, lang in (("elder", "Telugu Elder", "te"), ("receiver", "Hindi Receiver", "hi")):
        inv = httpx.post(
            f"{GATEWAY}/onboarding/invite",
            json={"display_name": name, "language": lang},
            headers=auth(FAMILY),
            timeout=30,
        )
        show(f"POST /onboarding/invite ({key}) -> {inv.status_code}", list(inv.json().keys()))
        token = inv.json()["invite_token"]
        act = httpx.post(f"{GATEWAY}/onboarding/activate", json={"invite_token": token}, timeout=30)
        body = act.json()
        show(
            f"POST /onboarding/activate ({key}) -> {act.status_code}",
            {
                "user_id": body["user_id"],
                "display_name": body["display_name"],
                "language": body["language"],
                "token (legacy bearer)": body["token"][:8] + "...",
                "access_token (signed)": body["access_token"][:12] + "...",
            },
        )
        again = httpx.post(
            f"{GATEWAY}/onboarding/activate", json={"invite_token": token}, timeout=30
        )
        show(f"  single use: second activate -> {again.status_code}", again.json())
        results[key] = {"user_id": body["user_id"], "token": body["token"]}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(results, f)
    print("--- the same endpoints through Caddy (what a browser can reach):")
    r = httpx.post(f"{CADDY}/onboarding/activate", json={"invite_token": "x"}, timeout=15)
    show(f"POST {CADDY}/onboarding/activate -> {r.status_code}", r.text[:60])
    r = httpx.get(f"{CADDY}/onboarding/qr/x", timeout=15)
    show(f"GET  {CADDY}/onboarding/qr/x -> {r.status_code}", r.text[:60])


def view_messages(viewer_token, other_id):
    r = httpx.get(
        f"{CADDY}/messages",
        params={"target_type": "user", "target_id": other_id},
        headers=auth(viewer_token),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["messages"]


def cmd_inspect(message_id, viewer):
    st = load_state()
    me, other = ("elder", "receiver") if viewer == "elder" else ("receiver", "elder")
    mine = [
        m for m in view_messages(st[me]["token"], st[other]["user_id"]) if m["id"] == message_id
    ]
    if not mine:
        show(f"INSPECT as {viewer}", "the message is NOT visible to this reader")
        return
    m = mine[0]
    show(
        f"INSPECT as {viewer}",
        {
            "status": m["status"],
            "kind": m["kind"],
            "transcript_language": m.get("transcript_language"),
            "has_media_ref": bool(m.get("media_ref")),
            "media_ref": m.get("media_ref"),
            "renderings": [
                {
                    "language": r["language"],
                    "degraded_reason": r["degraded_reason"],
                    "has_audio": bool(r.get("audio")),
                    "text": r["text"][:70],
                }
                for r in m.get("renderings", [])
            ],
            "moderation_notice": m.get("moderation_notice"),
        },
    )
    if m.get("transcript"):
        script_report(
            "transcript (labelled %s)" % m["transcript_language"], m["transcript"], "TELUGU"
        )
    for r in m.get("renderings", []):
        expected = {"hi": "DEVANAGARI", "en": "LATIN", "te": "TELUGU"}.get(r["language"], "LATIN")
        script_report(f"rendering[{r['language']}] text", r["text"], expected)
        if r.get("audio"):
            show(
                f"AUDIO of rendering[{r['language']}] fetched as {viewer}",
                audio_stats(st[me]["token"], r["audio"]["uri"]),
            )
    if m.get("media_ref"):
        show(
            f"ORIGINAL audio ({m['media_ref']['format']}) fetched as {viewer}, one request",
            audio_stats(st[me]["token"], m["media_ref"]["uri"]),
        )


def cmd_moderation(message_id):
    q = httpx.get(f"{GATEWAY}/moderation/queue", headers=auth(MODERATOR_TOKEN), timeout=30)
    body = q.json()
    show(
        f"GET /moderation/queue (direct) -> {q.status_code}",
        [
            {
                "message_id": i["message_id"][:8],
                "author": i["author_display_name"],
                "has_original_audio": bool(i.get("original_media_ref")),
                "transcript": i.get("transcript"),
                "pivot_text_en": i.get("pivot_text_en"),
                "latest_event": {
                    k: i["latest_event"][k] for k in ("actor_kind", "label", "action")
                },
                "event_count": i["event_count"],
            }
            for i in body.get("items", [])
        ],
    )
    ev = httpx.get(
        f"{GATEWAY}/moderation/messages/{message_id}/events",
        headers=auth(MODERATOR_TOKEN),
        timeout=30,
    )
    show(
        f"GET /moderation/messages/{message_id[:8]}../events (direct) -> {ev.status_code}",
        [
            {
                k: e.get(k)
                for k in (
                    "actor_kind",
                    "label",
                    "action",
                    "confidence",
                    "policy_version",
                    "model_version",
                    "degraded",
                    "rationale",
                    "created_at",
                )
            }
            for e in ev.json().get("events", [])
        ],
    )
    via_caddy = httpx.get(f"{CADDY}/moderation/queue", headers=auth(MODERATOR_TOKEN), timeout=15)
    show(
        f"GET {CADDY}/moderation/queue (what a browser reaches) -> {via_caddy.status_code}",
        via_caddy.text[:50],
    )
    as_elder = httpx.get(
        f"{GATEWAY}/moderation/queue", headers=auth(load_state()["elder"]["token"]), timeout=15
    )
    show(
        f"GET /moderation/queue as the ELDER (direct) -> {as_elder.status_code}", as_elder.text[:60]
    )


def cmd_circles():
    st = load_state()
    elder, receiver = st["elder"], st["receiver"]
    c = httpx.post(
        f"{CADDY}/circles",
        json={"name": "Gate announcements", "kind": "announcement"},
        headers=auth(elder["token"]),
        timeout=30,
    )
    show(
        f"POST /circles (announcement) as elder -> {c.status_code}",
        {k: c.json().get(k) for k in ("id", "name", "kind")},
    )
    cid = c.json()["id"]
    j = httpx.post(
        f"{CADDY}/circles/{cid}/members",
        json={"user_id": receiver["user_id"], "role": "member"},
        headers=auth(elder["token"]),
        timeout=30,
    )
    show(
        f"POST /circles/../members (add the Hindi receiver) -> {j.status_code}",
        j.json().get("role"),
    )

    def post(token, text):
        import uuid

        return httpx.post(
            f"{CADDY}/messages",
            headers=auth(token),
            timeout=30,
            json={
                "client_msg_id": str(uuid.uuid4()),
                "target_type": "circle",
                "target_id": cid,
                "kind": "text",
                "text": text,
                "source_lang": "te",
            },
        )

    p1 = post(elder["token"], "ఈ సాయంత్రం భజన ఉంది")
    show(
        f"admin posts to the announcement circle -> {p1.status_code}",
        {k: p1.json().get(k) for k in ("id", "status")},
    )
    p2 = post(receiver["token"], "a member tries to post")
    show(f"a plain member posts to the announcement circle -> {p2.status_code}", p2.json())
    lst = httpx.get(f"{CADDY}/circles", headers=auth(receiver["token"]), timeout=30)
    show("GET /circles as the member", [{"name": x["name"], "kind": x["kind"]} for x in lst.json()])


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    {
        "onboard": cmd_onboard,
        "inspect": cmd_inspect,
        "moderation": cmd_moderation,
        "circles": cmd_circles,
    }[cmd](*args)
