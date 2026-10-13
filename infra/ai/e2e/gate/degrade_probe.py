# Runs INSIDE the gateway container of the throwaway `degrade` stack.
#   seed | send_text | send_voice | watch <message_id> | inspect <message_id>
# Drives the REAL gateway over HTTP (127.0.0.1:8000) and reads the result from its own database.
# AUTH_MODE is legacy on this stack, so a bare UUID is that user (no token minting needed).
import io
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

from app.db.base import SessionLocal
from app.db.models import MessageRendering
from app.db.models import User as DbUser
from sqlalchemy import select, text

B = "http://127.0.0.1:8000"
AUTHOR = uuid.UUID("00000000-0000-4000-8000-00000000d001")  # speaks/writes Telugu
READER = uuid.UUID("00000000-0000-4000-8000-00000000d002")  # reads Hindi


def call(method, path, token=None, body=None, raw=None, ctype="application/json"):
    headers = {"Content-Type": ctype}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(B + path, method=method, headers=headers, data=data)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"body": e.read().decode()[:200]}


def tiny_wav() -> bytes:
    """One second of 16 kHz mono PCM16 near-silence. Only the SPEECH-DOWN case uses a voice note,
    and there the ASR is never reached, so the audio's content is irrelevant to what is proved."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x01" * 16000)
    return buf.getvalue()


def cmd_seed():
    db = SessionLocal()
    for uid, name, lang in ((AUTHOR, "Degrade Author", "te"), (READER, "Degrade Reader", "hi")):
        row = db.get(DbUser, uid)
        if row is None:
            db.add(DbUser(id=uid, name=name, preferred_language=lang, role="elder"))
        else:
            row.preferred_language = lang
    db.commit()
    print(f"seeded author={AUTHOR} reader={READER}")


def _send(kind, extra):
    body = {
        "client_msg_id": str(uuid.uuid4()),
        "target_type": "user",
        "target_id": str(READER),
        "kind": kind,
        "source_lang": "te",
        **extra,
    }
    code, out = call("POST", "/messages", str(AUTHOR), body)
    print(f"  POST /messages ({kind}) -> {code} {out.get('id', out)}")
    return out.get("id")


def cmd_send_text():
    print(_send("text", {"text": "ఈ సాయంత్రం ఏడు గంటలకు ఆలయంలో భజన ఉంటుంది."}) or "")


def cmd_send_voice():
    code, up = call(
        "POST",
        "/media?format=wav_pcm16&duration_ms=1000",
        str(AUTHOR),
        raw=tiny_wav(),
        ctype="application/octet-stream",
    )
    print(f"  POST /media -> {code} {up.get('id', up)}")
    ref = {
        "uri": up["uri"],
        "format": "wav_pcm16",
        "duration_ms": 1000,
    }  # the upload answers its own media: ref
    print(_send("voice", {"media_ref": ref}) or "")


def cmd_watch(message_id, limit=90):
    """Wait until the pipeline reaches a final state (or its job dies), then stop."""
    db = SessionLocal()
    deadline = time.monotonic() + float(limit)
    last = None
    while time.monotonic() < deadline:
        db.expire_all()
        row = db.execute(
            text(
                "select m.status, coalesce(m.pipeline_state,'-'),"
                " coalesce((select j.status from jobs j where j.payload->>'message_id' = :mid"
                "   order by j.created_at desc limit 1),'-'),"
                " coalesce((select j.attempts from jobs j where j.payload->>'message_id' = :mid"
                "   order by j.created_at desc limit 1),0)"
                " from messages m where m.id = :mid"
            ),
            {"mid": message_id},
        ).first()
        if row is None:
            break
        status, pstate, job, attempts = row
        now = (status, pstate, job, attempts)
        if now != last:
            print(
                f"    t+{time.monotonic() - (deadline - float(limit)):5.1f}s  "
                f"message={status}/{pstate}  job={job} attempts={attempts}"
            )
            last = now
        if pstate in ("complete", "failed") and job in ("done", "dead", "-"):
            break
        time.sleep(1)
    print("    (watch ended)")


def cmd_inspect(message_id):
    db = SessionLocal()
    row = db.execute(
        text(
            "select status, coalesce(pipeline_state,'-'), (transcript is not null),"
            " (pivot_text_en is not null) from messages where id = :mid"
        ),
        {"mid": message_id},
    ).first()
    print(f"  message: status={row[0]} pipeline_state={row[1]} transcript={row[2]} pivot={row[3]}")
    for actor, action, degraded, rationale in db.execute(
        text(
            "select actor_kind, action, degraded, left(rationale, 150) from moderation_events"
            " where message_id = :mid order by created_at"
        ),
        {"mid": message_id},
    ):
        print(f"  event:   {actor} {action} degraded={degraded} | {rationale}")
    rends = db.scalars(
        select(MessageRendering).where(MessageRendering.message_id == uuid.UUID(message_id))
    ).all()
    print(
        "  renderings: "
        + (
            ", ".join(
                f"{r.language}(reason={r.degraded_reason or 'ok'},"
                f"audio={'yes' if r.audio_media_object_id else 'no'})"
                for r in rends
            )
            or "NONE"
        )
    )
    for status, attempts, err in db.execute(
        text(
            "select status, attempts||'/'||max_attempts, left(coalesce(last_error,''), 170)"
            " from jobs where payload->>'message_id' = :mid order by created_at"
        ),
        {"mid": message_id},
    ):
        print(f"  job:     {status} attempts={attempts} last_error={err}")
    code, out = call("GET", f"/messages?target_type=user&target_id={AUTHOR}", str(READER))
    mine = [m for m in out.get("messages", []) if m.get("id") == message_id]
    if not mine:
        print(
            f"  THE READER SEES: nothing (GET /messages -> {code}, "
            f"{len(out.get('messages', []))} other messages)"
        )
    else:
        m = mine[0]
        langs = [r["language"] for r in (m.get("renderings") or [])]
        has_transcript = "yes" if m.get("transcript") else "no"
        has_media = "yes" if m.get("media_ref") else "no"
        print(
            f"  THE READER SEES: status={m['status']} renderings={langs or 'none'} "
            f"transcript={has_transcript} media={has_media}"
        )


def facts(message_id):
    """What happened to one message, from the database and from the reader's own request."""
    db = SessionLocal()
    mid = uuid.UUID(message_id)
    status, pstate = db.execute(
        text("select status, coalesce(pipeline_state,'-') from messages where id = :m"),
        {"m": message_id},
    ).one()
    events = db.execute(
        text(
            "select actor_kind, action from moderation_events "
            "where message_id = :m order by created_at"
        ),
        {"m": message_id},
    ).all()
    rends = {
        r.language: r.degraded_reason
        for r in db.scalars(select(MessageRendering).where(MessageRendering.message_id == mid))
    }
    code, out = call("GET", f"/messages?target_type=user&target_id={AUTHOR}", str(READER))
    reader_sees = any(m.get("id") == message_id for m in out.get("messages", []))
    return {
        "status": status,
        "pstate": pstate,
        "events": events,
        "renderings": rends,
        "reader_sees": reader_sees,
    }


# What each case must show. Section numbers are services/gateway/ORCHESTRATOR_DESIGN.md.
EXPECT = {
    # section 4: moderation / MT / ASR unavailable after retries -> HELD for a human
    "held": lambda f: (
        f["status"] == "held"
        and ("system", "HOLD") in f["events"]
        and not f["renderings"].get("hi")
        and not f["reader_sees"],
        "held, with a SYSTEM HOLD event, and the reader sees nothing",
    ),
    # section 4: render unavailable AFTER an ALLOW -> delivered with whatever exists (possibly none)
    "delivered_bare": lambda f: (
        f["status"] == "sent"
        and ("classifier", "ALLOW") in f["events"]
        and "hi" not in f["renderings"]
        and f["reader_sees"],
        "delivered after an ALLOW, with no Hindi rendering",
    ),
    # nothing wrong: delivered with its Hindi rendering and audio
    "delivered_full": lambda f: (
        f["status"] == "sent"
        and f["renderings"].get("hi") is None
        and "hi" in f["renderings"]
        and f["reader_sees"],
        "delivered with a Hindi rendering that has its audio (no degraded reason)",
    ),
    # section 7: an unreadable audio file -> the text is kept, flagged tts_skipped
    "delivered_text_only": lambda f: (
        f["status"] == "sent" and f["renderings"].get("hi") == "tts_skipped" and f["reader_sees"],
        "delivered with the Hindi TEXT kept and degraded_reason=tts_skipped",
    ),
}


def cmd_check(message_id, expect_key):
    ok, what = EXPECT[expect_key](facts(message_id))
    print(f"  VERDICT {'PASS' if ok else 'FAIL'}: expected {what}")


if __name__ == "__main__":
    command = sys.argv[1]
    if command in ("seed", "send_text", "send_voice"):
        {"seed": cmd_seed, "send_text": cmd_send_text, "send_voice": cmd_send_voice}[command]()
    elif command == "watch":
        cmd_watch(sys.argv[2], *(sys.argv[3:4]))
    elif command == "check":
        cmd_check(sys.argv[2], sys.argv[3])
    else:
        cmd_inspect(sys.argv[2])
