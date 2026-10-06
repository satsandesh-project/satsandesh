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
    burst <circle_id> <timeout_s> <n> <webm:label:lang> [<webm:label:lang> ...]
        N notes dispatched TOGETHER (every upload is done first, then all N POST /messages
        are released at once from a barrier), cycling through the given samples. One poller
        per reader records when each note first became visible to that reader and measures
        its rendering audio. Prints per-note events and one `burst_summary` (p50/p90/worst of
        the time to visible, an audit for lost / duplicated / unexpected messages, and a
        verdict per rendering audio). The single-note `run` above is unchanged.
"""

from __future__ import annotations

import io
import json
import math
import struct
import sys
import threading
import time
import uuid
import wave

import httpx
from load_stats import audio_verdict, audit, summarize

BASE = "http://gateway:8000"
ALICE = "00000000-0000-4000-8000-000000000001"
RECIPIENTS = {
    "bob(te)": "00000000-0000-4000-8000-000000000002",
    "carol(en)": "00000000-0000-4000-8000-000000000003",
    "dan(hi)": "00000000-0000-4000-8000-000000000004",
}


def _auth(token: str) -> dict:
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


def cmd_burst(circle_id: str, timeout_s: float, n: int, specs: list[str]) -> None:
    samples = []
    for spec in specs:
        path, label, lang = spec.rsplit(":", 2)
        with open(path, "rb") as f:
            samples.append((f.read(), label, lang))

    # Every upload first, so the burst measures the pipeline and not the upload.
    notes = []
    for i in range(n):
        data, label, lang = samples[i % len(samples)]
        up = httpx.post(
            f"{BASE}/media",
            params={"format": "webm_opus", "duration_ms": 5000},
            content=data,
            headers=_auth(ALICE),
            timeout=60,
        )
        up.raise_for_status()
        notes.append({"i": i, "label": label, "lang": lang, "media": up.json()})
    emit(event="burst_start", n=n, samples=[s[1] for s in samples], readers=list(RECIPIENTS))

    # What the circle already holds (earlier bursts reuse it): known, not "unexpected".
    before = sorted({m["id"] for uid in RECIPIENTS.values() for m in _view(uid, circle_id)})
    barrier = threading.Barrier(n)

    def send(note: dict) -> None:
        body = {
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "circle",
            "target_id": circle_id,
            "kind": "voice",
            "source_lang": note["lang"],
            "media_ref": {
                "uri": note["media"]["uri"],
                "format": note["media"]["format"],
                "duration_ms": 5000,
            },
        }
        barrier.wait()
        note["sent_at"] = time.time()
        try:
            r = httpx.post(f"{BASE}/messages", json=body, headers=_auth(ALICE), timeout=60)
        except httpx.HTTPError as e:
            note["send_error"] = repr(e)
            return
        note["ack_s"] = round(time.time() - note["sent_at"], 3)
        note["http"] = r.status_code
        if r.status_code in (200, 201):
            note["id"] = r.json()["id"]
            note["status"] = r.json()["status"]

    threads = [threading.Thread(target=send, args=(note,)) for note in notes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    accepted = [note for note in notes if "id" in note]
    for note in notes:
        emit(
            event="sent" if "id" in note else "send_failed",
            index=note["i"],
            label=note["label"],
            message_id=note.get("id"),
            http=note.get("http"),
            status=note.get("status"),
            ack_s=note.get("ack_s"),
            error=note.get("send_error"),
        )
    if not accepted:
        emit(event="burst_summary", verdict="no note was accepted: nothing to measure")
        return
    starts = [note["sent_at"] for note in notes if "sent_at" in note]
    emit(event="dispatch_spread_s", value=round(max(starts) - min(starts), 3))
    by_id = {note["id"]: note for note in accepted}

    lock = threading.Lock()
    visible_at: dict[tuple[str, str], dict] = {}  # (reader, message id) -> what it saw
    final_views: dict[str, list[str]] = {}
    deadline = time.time() + timeout_s

    def poll(reader: str, uid: str) -> None:
        todo = set(by_id)
        while time.time() < deadline:
            try:
                view = _view(uid, circle_id)
            except httpx.HTTPError:
                time.sleep(1.0)
                continue
            now = time.time()
            for m in view:
                if m["id"] not in todo:
                    continue
                renderings = []
                for r in m.get("renderings", []):
                    audio = r.get("audio")
                    stats = _audio_stats(uid, audio["uri"]) if audio else None
                    renderings.append(
                        {
                            "language": r["language"],
                            "text": r["text"],
                            "degraded_reason": r["degraded_reason"],
                            "audio": stats,
                            "audio_verdict": audio_verdict(stats),
                        }
                    )
                with lock:
                    visible_at[(reader, m["id"])] = {
                        "after_send_s": round(now - by_id[m["id"]]["sent_at"], 2),
                        "status": m["status"],
                        "transcript": m.get("transcript"),
                        "renderings": renderings,
                    }
                todo.discard(m["id"])
            if not todo:
                break
            time.sleep(1.0)
        # One last look at the whole view, which is what the duplicate audit reads.
        ids_seen: list[str] = []
        try:
            ids_seen = [m["id"] for m in _view(uid, circle_id)]
        except httpx.HTTPError:
            pass
        with lock:
            final_views[reader] = ids_seen

    pollers = [threading.Thread(target=poll, args=(r, u)) for r, u in RECIPIENTS.items()]
    t0 = time.time()
    for t in pollers:
        t.start()
    for t in pollers:
        t.join()

    for (reader, mid), info in sorted(visible_at.items(), key=lambda kv: kv[1]["after_send_s"]):
        emit(
            event="visible",
            recipient=reader,
            index=by_id[mid]["i"],
            label=by_id[mid]["label"],
            message_id=mid,
            **info,
        )
    for reader in RECIPIENTS:
        for mid, note in by_id.items():
            if (reader, mid) not in visible_at:
                emit(event="never_visible", recipient=reader, index=note["i"], message_id=mid)

    per_note: dict[str, list[float]] = {}
    for (_, mid), info in visible_at.items():
        per_note.setdefault(mid, []).append(info["after_send_s"])
    # A note is "done" when its LAST reader could see it; only notes every reader saw count.
    wall = [max(times) for times in per_note.values() if len(times) == len(RECIPIENTS)]
    by_reader: dict[str, list[float]] = {reader: [] for reader in RECIPIENTS}
    for (reader, _), info in visible_at.items():
        by_reader[reader].append(info["after_send_s"])
    verdicts: dict[str, int] = {}
    for info in visible_at.values():
        for r in info["renderings"]:
            if r["audio"] is None and r["degraded_reason"]:
                key = "text_only:" + str(r["degraded_reason"])
            else:
                key = r["audio_verdict"]
            verdicts[key] = verdicts.get(key, 0) + 1
    emit(
        event="burst_summary",
        n_sent=n,
        n_accepted=len(accepted),
        wall_s=summarize(wall),
        completion_s_sorted=sorted(round(w, 1) for w in wall),
        per_reader_s={reader: summarize(by_reader[reader]) for reader in RECIPIENTS},
        rendering_audio=verdicts,
        audit=audit(list(by_id), final_views, known=before),
        observation_window_s=round(time.time() - t0, 1),
    )


def main(argv: list[str]) -> None:
    cmd, args = argv[1], argv[2:]
    if cmd == "circle":
        cmd_circle()
    elif cmd == "run":
        cmd_run(args[0], args[1], args[2], args[3], float(args[4]) if len(args) > 4 else 300.0)
    elif cmd == "burst":
        cmd_burst(args[0], float(args[1]), int(args[2]), args[3:])
    else:
        raise SystemExit(f"unknown subcommand {cmd!r}")


if __name__ == "__main__":
    main(sys.argv)
