"""Bhajan rooms (Weeks 9 and 10): the elder app's room list and room screen.

A page of its own at `/bhajans`, so the main chat page is untouched except for one
`register(app)` line at the bottom of elder_app.py and one link on the home screen
(`rooms_link`). The screen itself is `assets/bhajan_rooms.js`: plain DOM calls against the
gateway's `/rooms` API, with LiveKit's browser library (vendored as
`assets/livekit-client.umd.min.js`, loaded only on this page) carrying the audio. The reasons it is
JavaScript and not Reflex state: the audio connection lives in the browser, the room state is
polled every two seconds, and the buttons must not be rebuilt under a finger mid-tap.

What the screen does is decided by the SERVER (contracts/chat/rooms.py `RoomState.you`): who may
take the floor, who leads, whether a token may publish. Only the lead's microphone is ever live, and
the gateway enforces that; the app just draws it.

The words are here, not in the JS, so both languages sit beside the rest of the app's strings and
the Telugu is in one place for a native reader to check.
"""

import json

import reflex as rx

# The main module is imported inside the functions that need it, not here: elder_app.py imports
# this module at its very end, so a top-level import the other way round would be circular and
# would break whichever of the two was imported first.

ROUTE = "/bhajans"

# English and Telugu. The page uses the language the person chose on first use (Telugu, or English
# for everyone else). {n} is filled in by the screen. The Telugu needs a native reader.
TEXTS = {
    "en": {
        "title": "Bhajan rooms",
        "back": "Back",
        "no_rooms": "No bhajan room is open right now.",
        "join": "Join",
        "connecting": "Joining the room...",
        "singing_now": "Singing now",
        "nobody_singing": "Nobody is singing yet",
        "you_are_live": "You are live. Everyone can hear you.",
        "listening": "Listening",
        "raise": "Raise hand: I want to sing",
        "lower": "Lower hand",
        "your_place": "You are number {n} in line",
        "take": "Take the next bhajan",
        "handover": "Hand over to the next singer",
        "pass": "I am done. Next singer",
        "waiting_list": "Waiting to sing",
        "here": "In the room",
        "you": "You",
        "skip": "Skip",
        "skip_name": "Skip {name}",
        "moderator_controls": "Moderator controls",
        "close_room": "Close the room",
        "confirm_close": "Close this room for everyone?",
        "leave": "Leave the room",
        "tap_to_hear": "Tap to hear the bhajan",
        "mic_blocked": "The microphone is blocked. Allow it in the browser to sing.",
        "reconnect": "Reconnect the audio",
        "start_title": "Start a bhajan room (moderators)",
        "room_name": "Name of the room",
        "circle": "Circle",
        "start_button": "Start the room",
        "fill_both": "Give the room a name and choose a circle.",
        "err_network": "No connection. Please check the network and try again.",
        "err_signin": "Please open the app from the home page first.",
        "err_forbidden": "Only a moderator can do that.",
        "err_notfound": "This room was not found.",
        "err_closed": "This room has closed.",
        "err_audio": "The audio is not available just now. Please try again.",
        "err_generic": "Something went wrong. Please try again.",
        "nav": "Bhajan rooms",
    },
    "te": {
        "title": "భజన గదులు",
        "back": "వెనక్కి",
        "no_rooms": "ప్రస్తుతం భజన గదులు ఏవీ తెరిచి లేవు.",
        "join": "చేరండి",
        "connecting": "గదిలో చేరుతున్నారు...",
        "singing_now": "ఇప్పుడు పాడుతున్నారు",
        "nobody_singing": "ఇంకా ఎవరూ పాడటం లేదు",
        "you_are_live": "మీరు ప్రత్యక్షంగా ఉన్నారు. అందరికీ మీ గొంతు వినిపిస్తోంది.",
        "listening": "వింటున్నారు",
        "raise": "చేయి ఎత్తండి: నేను పాడతాను",
        "lower": "చేయి దించండి",
        "your_place": "మీరు వరుసలో {n}వ స్థానంలో ఉన్నారు",
        "take": "తదుపరి భజన తీసుకోండి",
        "handover": "తదుపరి గాయకుడికి ఇవ్వండి",
        "pass": "నా భజన అయిపోయింది. తదుపరి గాయకుడు",
        "waiting_list": "పాడటానికి వేచి ఉన్నవారు",
        "here": "గదిలో ఉన్నవారు",
        "you": "మీరు",
        "skip": "దాటవేయి",
        "skip_name": "{name} ను దాటవేయి",
        "moderator_controls": "నిర్వాహకుల నియంత్రణలు",
        "close_room": "గదిని మూసివేయండి",
        "confirm_close": "ఈ గదిని అందరికీ మూసివేయాలా?",
        "leave": "గది నుండి వెళ్ళండి",
        "tap_to_hear": "భజన వినడానికి నొక్కండి",
        "mic_blocked": "మైక్రోఫోన్ నిరోధించబడింది. పాడటానికి బ్రౌజర్‌లో అనుమతించండి.",
        "reconnect": "ఆడియోను మళ్ళీ కలపండి",
        "start_title": "భజన గదిని ప్రారంభించండి (నిర్వాహకులకు)",
        "room_name": "గది పేరు",
        "circle": "సర్కిల్",
        "start_button": "గదిని ప్రారంభించండి",
        "fill_both": "గదికి పేరు ఇచ్చి, సర్కిల్‌ను ఎంచుకోండి.",
        "err_network": "కనెక్షన్ లేదు. నెట్‌వర్క్ చూసి మళ్ళీ ప్రయత్నించండి.",
        "err_signin": "దయచేసి ముందుగా హోమ్ పేజీ నుండి యాప్ తెరవండి.",
        "err_forbidden": "ఇది నిర్వాహకులు మాత్రమే చేయగలరు.",
        "err_notfound": "ఈ గది కనపడలేదు.",
        "err_closed": "ఈ గది మూసివేయబడింది.",
        "err_audio": "ఇప్పుడు ఆడియో అందుబాటులో లేదు. దయచేసి మళ్ళీ ప్రయత్నించండి.",
        "err_generic": "ఏదో తప్పు జరిగింది. దయచేసి మళ్ళీ ప్రయత్నించండి.",
        "nav": "భజన గదులు",
    },
}

# The keys the screen's JS reads, so a missing string is a test failure and not a blank button.
SCREEN_KEYS = tuple(k for k in TEXTS["en"] if k != "nav")

_COLOR_KEYS = (
    "ink",
    "muted_ink",
    "deep_green",
    "green_tint",
    "green_ink",
    "card_cream",
    "warm_border",
)

# Starts the screen once the script has loaded. The script is fetched here (not listed in the page's
# head) so no other page of the app downloads it.
BOOT_JS_TEMPLATE = """
(() => {
    const config = %(config)s;
    const run = () => window.SatBhajan.start(config);
    if (window.SatBhajan) { run(); return "ok"; }
    const s = document.createElement("script");
    s.src = "/bhajan_rooms.js";
    s.onload = run;
    document.head.appendChild(s);
    return "ok";
})()
"""


def boot_config() -> dict:
    from elder_app.elder_app import COLOR, GATEWAY_PUBLIC_URL

    return {
        "gateway": GATEWAY_PUBLIC_URL,
        "colors": {key: COLOR[key] for key in _COLOR_KEYS},
        "texts": TEXTS,
    }


def boot_js() -> str:
    return BOOT_JS_TEMPLATE % {"config": json.dumps(boot_config(), ensure_ascii=False)}


def bhajan_rooms_page() -> rx.Component:
    from elder_app.elder_app import COLOR, FONT_LATIN

    return rx.box(
        rx.box(
            id="bhajan-root",
            style={"padding": "16px 18px 32px"},
        ),
        on_mount=rx.call_script(boot_js()),
        style={
            "min_height": "100vh",
            "max_width": "560px",
            "margin": "0 auto",
            "background": COLOR["cream_canvas"],
            "font_family": FONT_LATIN,
            "color": COLOR["ink"],
            # The page paints its own cream and ink; without this a dark-mode phone turns the native
            # fields and drop-downs dark on cream (the console's browser pass found the same).
            "color_scheme": "light",
        },
    )


def rooms_link() -> rx.Component:
    """The one navigation entry: a large button on the home screen."""
    from elder_app.elder_app import COLOR, State

    return rx.box(
        rx.link(
            rx.button(
                rx.cond(State.language == "te", TEXTS["te"]["nav"], TEXTS["en"]["nav"]),
                style={
                    "width": "100%",
                    "min_height": "64px",
                    "border_radius": "16px",
                    "font_size": "1.25rem",
                    "font_weight": "700",
                    "cursor": "pointer",
                    "background": COLOR["green_tint"],
                    "color": COLOR["green_ink"],
                    "border": f"2px solid {COLOR['deep_green']}",
                },
            ),
            href=ROUTE,
            style={"width": "100%"},
        ),
        style={"margin": "12px 20px 0"},
    )


def register(app: rx.App) -> None:
    app.add_page(bhajan_rooms_page, route=ROUTE, title="Bhajan rooms")
