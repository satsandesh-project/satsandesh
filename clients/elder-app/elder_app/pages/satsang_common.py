"""What the elder's two satsang screens share: the words, the colours, the one request, and the
decision of which screen the elder is on (`phase_for`).

The screens are `satsang_upcoming.py` (`/satsang`: what is on and what is next) and
`satsang_listen.py` (`/satsang/<id>`: one big button and nothing else). They live in their own
files and import nothing from elder_app.py, which gains only an import, add_page lines and one
entry card.

**What the page may know.** It asks for `GET /sessions`, the list an elder is allowed to see
(`SatsangSessionPublic`): id, title, speaker_name, scheduled_start_at, status and `hls_url`, which
the contract's own validator makes present if and only if the session is live. It never sees a
stream key, the speaker's id, who scheduled it, or `reminder_sent_at`, so there is nothing of
those to hide. The elder's own id (from the same localStorage key the rest of the app uses) goes
in the Authorization header and nowhere else; the page writes nothing to the browser.

**There is no `GET /sessions/{id}` for an elder, only the list, and the list leaves a satsang out
the moment it ends.** So "ended" cannot be read; it is inferred: a satsang the elder saw live, or
was listening to, that has left the list has ended. An elder who opens a link to a satsang that is
simply not on the list is told "not on the list" rather than something false.

**The screens, one per `phase`** (see `phase_for`): loading, need_identity, load_error, gone,
ended, cancelled, scheduled, live_ready, connecting, connecting_long, playing, stalled_soft,
stalled_drop, stalled_long and four failures. Every one has words in two languages. The Telugu and
the English were both written by the author of this code; **no native reader has read the
Telugu**.

**How long before it says something.** A short hiccup says nothing (playing, until 8 s). From 8 s
the screen says it is waiting for the speaker; from 25 s that the speaker's connection may have
dropped and that we keep trying; from 120 s that the satsang may have ended. The media server
drops a silent publisher after 30 s and the list is read every 12 s, so the real "ended" usually
arrives long before the 120 s words.

**A late joiner.** The playlist holds about 32 s, so an elder who joins 20 minutes in hears from
now: the screen says that (`late_note`), and, always, that the sound is a few seconds behind the
speaker. The earlier part cannot be played again (nothing is recorded).

**The certificate.** A phone that does not trust this site's certificate cannot fetch the stream,
and the failure is opaque to a browser. On Android (hls.js, same origin as the page) a warning the
elder clicked through also covers the stream; on an iPhone the media stack fetches it and does not
honour that, so the stream fails while the page loaded. The player reports a stream that never
loaded as `unreachable`, and the screen says what that probably is and what to ask for."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

GATEWAY_PUBLIC_URL = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost")

# Seconds of silence before the screen says anything, in the order the elder needs them.
SOFT_WAIT_S = 8
DROP_WAIT_S = 25
LONG_WAIT_S = 120
CONNECT_LONG_S = 20
# A live satsang that began more than this long ago is one the elder is joining late.
LATE_AFTER_S = 120
TICK_MS = 3000  # the list screen's clock
LISTEN_TICK_MS = 1000  # the listen screen's clock: the tap is answered within a second
LIST_EVERY_TICKS = 12  # the listen screen re-reads the list every 12 s
UPCOMING_EVERY_TICKS = 10  # the list screen every 30 s

# The elder app's palette (same hex values), kept here so these files stand alone.
COLOR = {
    "canvas": "#F7F0E3",
    "card": "#FFFCF6",
    "ink": "#2A2118",
    "muted": "#4A3F2E",
    "primary": "#8A3E0F",
    "green": "#1F4034",
    "live": "#8A1C1C",
    "gold": "#FBEFD5",
    "border": "#C9B58E",
    "warn_bg": "#FBE9E7",
    "warn_ink": "#6B1111",
    "white": "#FFFFFF",
}

# (what it is, text colour, background, minimum contrast ratio); the tests compute each ratio.
CONTRAST_PAIRS = [
    ("body text on a card", COLOR["ink"], COLOR["card"], 7.0),
    ("body text on the canvas", COLOR["ink"], COLOR["canvas"], 7.0),
    ("secondary text on the canvas", COLOR["muted"], COLOR["canvas"], 7.0),
    ("the big listen button", COLOR["white"], COLOR["primary"], 7.0),
    ("the 'on now' badge", COLOR["white"], COLOR["live"], 7.0),
    ("the 'coming up' badge", COLOR["white"], COLOR["green"], 7.0),
    ("a note on its cream box", COLOR["ink"], COLOR["gold"], 7.0),
    ("secondary text on the cream box", COLOR["muted"], COLOR["gold"], 7.0),
    ("a problem message", COLOR["warn_ink"], COLOR["warn_bg"], 7.0),
    ("a quiet button", COLOR["ink"], COLOR["card"], 7.0),
]

_PHASE_TEXTS_EN = {
    "loading": ("Loading…", "Please wait a moment."),
    "need_identity": (
        "Please open SatSandesh first",
        "Open the SatSandesh home page and join, then come back to this page.",
    ),
    "load_error": (
        "Could not load the satsangs",
        "Check that your phone is connected to Wi-Fi or mobile data, then try again.",
    ),
    "gone": ("This satsang is not on the list", "It may have ended or been removed."),
    "ended": (
        "This satsang has ended",
        "Thank you for listening. It is not recorded, so it cannot be played again.",
    ),
    "cancelled": ("This satsang was cancelled", "Please look at the list for the next one."),
    "scheduled": (
        "This satsang has not started yet",
        "{speaker} will speak {when}. The big button will appear here when it starts.",
    ),
    "live_ready": ("Live now", "Press the big button to listen."),
    "connecting": ("Connecting…", "Please wait. This can take a few seconds."),
    "connecting_long": (
        "Still connecting…",
        "This is taking longer than usual. Check your Wi-Fi or mobile signal. We keep trying.",
    ),
    "playing": ("You are listening", "The speaker is talking now."),
    "stalled_soft": ("Waiting for the speaker's voice…", "Please stay on this page."),
    "stalled_drop": (
        "The sound has stopped",
        "The speaker's connection may have dropped. We keep trying. "
        "You do not need to do anything.",
    ),
    "stalled_long": ("Still no sound", "The satsang may have ended. You can check again."),
    "failed_unreachable": (
        "The sound could not be loaded",
        "If this phone shows a security warning for this site, the phone does not yet trust the "
        "site's certificate. Please ask the person who set up SatSandesh to install the "
        "certificate on this phone. Then try again.",
    ),
    "failed_unsupported": (
        "This phone cannot play the sound",
        "Please open this page in Chrome (Android) or Safari (iPhone).",
    ),
    "failed_blocked": (
        "Please press the big button again",
        "The phone did not start the sound the first time.",
    ),
    "failed_other": ("The sound stopped working", "Please try again."),
}

_PHASE_TEXTS_TE = {
    "loading": ("లోడ్ అవుతోంది…", "దయచేసి ఒక్క క్షణం ఆగండి."),
    "need_identity": (
        "ముందు యాప్ హోమ్ పేజీ తెరవండి",
        "యాప్ హోమ్ పేజీ తెరిచి చేరండి, ఆపై ఈ పేజీకి తిరిగి రండి.",
    ),
    "load_error": (
        "సత్సంగాల జాబితా లోడ్ కాలేదు",
        "మీ ఫోన్ వై-ఫై లేదా మొబైల్ డేటాకు కనెక్ట్ అయి ఉందో చూసి, మళ్లీ ప్రయత్నించండి.",
    ),
    "gone": ("ఈ సత్సంగం జాబితాలో లేదు", "అది ముగిసి ఉండవచ్చు లేదా తొలగించబడి ఉండవచ్చు."),
    "ended": (
        "ఈ సత్సంగం ముగిసింది",
        "విన్నందుకు ధన్యవాదాలు. ఇది రికార్డ్ చేయబడదు, కాబట్టి మళ్లీ వినలేరు.",
    ),
    "cancelled": ("ఈ సత్సంగం రద్దు చేయబడింది", "తదుపరి సత్సంగం కోసం జాబితా చూడండి."),
    "scheduled": (
        "ఈ సత్సంగం ఇంకా ప్రారంభం కాలేదు",
        "{when} {speaker} గారు మాట్లాడతారు. ప్రారంభమైనప్పుడు ఇక్కడ పెద్ద బటన్ కనిపిస్తుంది.",
    ),
    "live_ready": ("ఇప్పుడు ప్రత్యక్షంగా", "వినడానికి పెద్ద బటన్ నొక్కండి."),
    "connecting": ("కలుపుతున్నాము…", "దయచేసి ఆగండి. కొన్ని సెకన్లు పట్టవచ్చు."),
    "connecting_long": (
        "ఇంకా కలుపుతున్నాము…",
        "ఇది మామూలు కంటే ఎక్కువ సమయం తీసుకుంటోంది. మీ వై-ఫై లేదా మొబైల్ సిగ్నల్ చూడండి. మేము ప్రయత్నిస్తూనే ఉంటాము.",
    ),
    "playing": ("మీరు వింటున్నారు", "వక్త ఇప్పుడు మాట్లాడుతున్నారు."),
    "stalled_soft": ("వక్త గొంతు కోసం వేచి ఉన్నాము…", "దయచేసి ఈ పేజీలోనే ఉండండి."),
    "stalled_drop": (
        "శబ్దం ఆగిపోయింది",
        "వక్త కనెక్షన్ తెగిపోయి ఉండవచ్చు. మేము ప్రయత్నిస్తూనే ఉంటాము. మీరు ఏమీ చేయనవసరం లేదు.",
    ),
    "stalled_long": ("ఇంకా శబ్దం లేదు", "సత్సంగం ముగిసి ఉండవచ్చు. మీరు మళ్లీ చూడవచ్చు."),
    "failed_unreachable": (
        "శబ్దం లోడ్ కాలేదు",
        "ఈ సైట్ కోసం ఈ ఫోన్‌లో భద్రతా హెచ్చరిక కనిపిస్తే, ఈ ఫోన్ ఇంకా సైట్ "
        "సర్టిఫికెట్‌ను నమ్మడం లేదు. యాప్ ఏర్పాటు చేసిన వ్యక్తిని ఈ ఫోన్‌లో సర్టిఫికెట్ "
        "ఇన్‌స్టాల్ చేయమని అడగండి. తర్వాత మళ్లీ ప్రయత్నించండి.",
    ),
    "failed_unsupported": (
        "ఈ ఫోన్‌లో శబ్దం ప్లే కాదు",
        "దయచేసి ఈ పేజీని Chrome (Android) లేదా Safari (iPhone) లో తెరవండి.",
    ),
    "failed_blocked": (
        "దయచేసి పెద్ద బటన్‌ను మళ్లీ నొక్కండి",
        "మొదటిసారి ఫోన్ శబ్దాన్ని ప్రారంభించలేదు.",
    ),
    "failed_other": ("శబ్దం పని చేయడం ఆగిపోయింది", "దయచేసి మళ్లీ ప్రయత్నించండి."),
}

_COMMON_EN = {
    "upcoming_title": "Satsang",
    "on_now": "On now",
    "coming_up": "Coming up",
    "empty": "No satsang is planned yet. Please look again later.",
    "listen": "Listen",
    "speaker": "Speaker",
    "cancelled_badge": "Cancelled",
    "scheduled_badge": "Not started",
    "entry_title": "Satsang",
    "entry_hint": "Listen live and see what is coming",
    "play_label": "Listen",
    "stop_label": "Stop",
    "check_again": "Check again",
    "try_again": "Try again",
    "back_to_list": "All satsangs",
    "back_home": "Back",
    "go_home": "Open the home page",
    "late_note": (
        "You joined while the satsang was under way. You will hear it from now on; "
        "the part you missed cannot be played again."
    ),
    "delay_note": (
        "You hear the speaker a few seconds behind, because the sound is sent in small pieces."
    ),
}

_COMMON_TE = {
    "upcoming_title": "సత్సంగం",
    "on_now": "ఇప్పుడు జరుగుతోంది",
    "coming_up": "రాబోయేవి",
    "empty": "ఇంకా సత్సంగం ఏదీ నిర్ణయించలేదు. కొంత సేపటి తర్వాత మళ్లీ చూడండి.",
    "listen": "వినండి",
    "speaker": "వక్త",
    "cancelled_badge": "రద్దు చేయబడింది",
    "scheduled_badge": "ఇంకా ప్రారంభం కాలేదు",
    "entry_title": "సత్సంగం",
    "entry_hint": "ప్రత్యక్షంగా వినండి, రాబోయేవి చూడండి",
    "play_label": "వినండి",
    "stop_label": "ఆపండి",
    "check_again": "మళ్లీ చూడండి",
    "try_again": "మళ్లీ ప్రయత్నించండి",
    "back_to_list": "అన్ని సత్సంగాలు",
    "back_home": "వెనుకకు",
    "go_home": "హోమ్ పేజీ తెరవండి",
    "late_note": ("సత్సంగం మొదలైన తర్వాత మీరు చేరారు. ఇప్పటి నుండి మీకు వినిపిస్తుంది; మీరు కోల్పోయిన భాగాన్ని మళ్లీ వినలేరు."),
    "delay_note": ("శబ్దం చిన్న ముక్కలుగా పంపబడుతుంది, కాబట్టి వక్త కంటే కొన్ని సెకన్లు వెనుకగా మీకు వినిపిస్తుంది."),
}


def _texts(common: dict[str, str], phases: dict[str, tuple[str, str]]) -> dict[str, str]:
    out = dict(common)
    for phase, (title, body) in phases.items():
        out[f"{phase}_title"] = title
        out[f"{phase}_body"] = body
    return out


TEXTS: dict[str, dict[str, str]] = {
    "en": _texts(_COMMON_EN, _PHASE_TEXTS_EN),
    "te": _texts(_COMMON_TE, _PHASE_TEXTS_TE),
}


def text_for(lang: str) -> dict[str, str]:
    """The words in the elder's language; anything else reads English, like the rest of the app."""
    return TEXTS["te"] if lang == "te" else TEXTS["en"]


# --- which screen -------------------------------------------------------------------------------

_FAILED = {
    "unreachable": "failed_unreachable",
    "unsupported": "failed_unsupported",
    "blocked": "failed_blocked",
}


def _player_phase(player: str, waiting_s: int, error_kind: str) -> str:
    if player == "connecting":
        return "connecting" if waiting_s < CONNECT_LONG_S else "connecting_long"
    if player == "stalled":
        if waiting_s < SOFT_WAIT_S:
            return "playing"  # a short hiccup is not worth a word
        if waiting_s < DROP_WAIT_S:
            return "stalled_soft"
        if waiting_s < LONG_WAIT_S:
            return "stalled_drop"
        return "stalled_long"
    if player == "failed":
        return _FAILED.get(error_kind, "failed_other")
    return "playing"


def phase_for(
    *,
    load_state: str,
    load_error: str = "",
    item_status: str | None = None,
    seen_live: bool = False,
    player: str = "idle",
    waiting_s: int = 0,
    error_kind: str = "",
) -> str:
    """Which screen the elder is looking at. The list outranks the player (a satsang that has left
    the list is over, whatever the player thinks), except that a FAILED REFRESH of the list never
    cuts off someone who is listening: a phone's signal blips, and the discourse is the point."""
    listening = player != "idle"
    if load_state == "loading":
        return "loading"
    if load_state == "error":
        if listening:
            return _player_phase(player, waiting_s, error_kind)
        return "need_identity" if load_error == "no_identity" else "load_error"
    if item_status is None:
        return "ended" if (seen_live or listening) else "gone"
    if item_status == "cancelled":
        return "cancelled"
    if item_status == "scheduled":
        return "scheduled"
    if not listening:
        return "live_ready"
    return _player_phase(player, waiting_s, error_kind)


# --- reading the list ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _error_kind(status: int, detail: str) -> str:
    if detail == "no_identity":
        return "no_identity"
    if detail == "network":
        return "network"
    if status in (401, 403):
        return "unauthorised"
    return "server"


def parse_load(raw: str, now: datetime | None = None) -> dict[str, Any]:
    """What the browser script answered, as the screens use it. Anything unreadable is a plain
    failure, never an exception, and only what a screen shows is kept."""
    now = now or _now()
    failed = {"ok": False, "error": "server", "lang": "en", "items": []}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return failed
    if not isinstance(data, dict):
        return failed
    lang = "te" if data.get("lang") == "te" else "en"
    status = data.get("status") if isinstance(data.get("status"), int) else 0
    if not data.get("ok"):
        detail = data.get("detail") if isinstance(data.get("detail"), str) else ""
        return {"ok": False, "error": _error_kind(status, detail), "lang": lang, "items": []}
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        return {**failed, "lang": lang}
    items = []
    for entry in raw_items:
        if not isinstance(entry, dict) or entry.get("status") not in (
            "live",
            "scheduled",
            "cancelled",
        ):
            continue
        if not isinstance(entry.get("id"), str) or not isinstance(entry.get("title"), str):
            continue
        live = entry["status"] == "live"
        hls = entry.get("hls_url")
        # Only this gateway's own HLS path is ever handed to the player.
        stream = (
            GATEWAY_PUBLIC_URL + hls
            if live and isinstance(hls, str) and hls.startswith("/hls/")
            else ""
        )
        started = _parse_time(entry.get("scheduled_start_at"))
        items.append(
            {
                "id": entry["id"],
                "title": entry["title"],
                "speaker_name": str(entry.get("speaker_name") or ""),
                "status": entry["status"],
                "when": str(entry.get("when") or ""),
                "stream_url": stream,
                "late": bool(live and started and started <= now - timedelta(seconds=LATE_AFTER_S)),
                "_start": started or datetime.max.replace(tzinfo=UTC),
            }
        )
    items.sort(key=lambda i: (i["status"] != "live", i["_start"]))
    for i in items:
        del i["_start"]
    return {"ok": True, "error": "", "lang": lang, "items": items}


# --- the browser scripts ------------------------------------------------------------------------

_JS_LOAD = """
(async () => {
    const GATEWAY = __GATEWAY__;
    let prefs = {};
    try { prefs = JSON.parse(localStorage.getItem("sat_prefs") || "{}"); } catch (e) { prefs = {}; }
    const lang = prefs && prefs.lang === "te" ? "te" : "en";
    const locale = lang === "te" ? "te-IN" : "en-IN";
    const done = (ok, status, detail, items) =>
        JSON.stringify({
            ok: ok, status: status, detail: detail || "", lang: lang, items: items || [],
        });
    const id = localStorage.getItem("satsandesh_my_id") || "";
    if (!id) return done(false, 0, "no_identity");
    try {
        const resp = await fetch(GATEWAY + "/sessions", {
            headers: { Authorization: "Bearer " + id },
        });
        if (!resp.ok) return done(false, resp.status, "");
        const payload = await resp.json();
        const fmt = new Intl.DateTimeFormat(locale, {
            weekday: "long", day: "numeric", month: "long", hour: "numeric", minute: "2-digit",
        });
        const list = payload && Array.isArray(payload.items) ? payload.items : [];
        const items = list.map((i) => {
            const d = new Date(i.scheduled_start_at);
            return {
                id: i.id, title: i.title, speaker_name: i.speaker_name, status: i.status,
                scheduled_start_at: i.scheduled_start_at, hls_url: i.hls_url,
                when: isNaN(d.getTime()) ? "" : fmt.format(d),
            };
        });
        return done(true, 200, "", items);
    } catch (e) {
        return done(false, 0, "network");
    }
})()
"""

JS_STATUS = (
    "JSON.stringify(window.satsang ? window.satsang.status() : "
    "{status: 'idle', waiting_s: 0, error_kind: ''})"
)
JS_STOP = "window.satsang && window.satsang.stop()"


def js_load_sessions() -> str:
    return _JS_LOAD.replace("__GATEWAY__", json.dumps(GATEWAY_PUBLIC_URL))
