"""SatSandesh elder chat UI shell (Week 3/4 M1).

Visual design lifted from a Claude Design handoff (see
docs/design/satsandesh-home-screen-design/ for the source spec): warm
devotional palette, Mulish/Noto Sans Telugu/Lora type system on a 20px
rem root (so a 200% OS text setting scales the whole screen), and named
interaction states with documented WCAG contrast ratios.

The mic button does real browser-side recording (MediaRecorder via
navigator.mediaDevices.getUserMedia, wired through rx.call_script) --
genuine permission prompt, genuine capture, genuine local playback. It
does not send anywhere: there is no backend endpoint for voice notes yet,
and the design handoff itself doesn't specify a recipient-routing flow
for a home-screen-level mic, so recording surfaces as a local "recorded,
here it is" banner rather than an invented send flow.

Design principles (Section 7.5 of the project proposal + the handoff):
  - two-taps-to-anything: Home -> tap a contact -> chat screen. One tap,
    not a menu tree.
  - large targets: every tappable element is >=88px (roughly double the
    standard 44px minimum), per the handoff's own accessibility notes.
  - faces-before-names: each contact row shows a large avatar first; the
    name is secondary. Recognition before reading.
  - bilingual (Telugu / English): a single toggle switches all UI chrome
    and contact names/metadata. Free-typed message text is left as typed.
  - voice-first: a large press-and-hold mic button, reachable without
    navigating anywhere.

Backend-reality update (2026-09-02): the team's real backend is now
`services/gateway/` (M3's), restored to `main` and, as of PR #23, fully
functional -- true per-contact 1:1 messaging (`target_type: "user"`),
not one shared broadcast circle, with real pending -> delivered status.
This screen is rewired to that real protocol, replacing the earlier
shared-circle-only version:

  - Identity: `services/gateway`'s own auth stub (app/auth.py) has no
    login step at all -- any token that parses as a UUID *is* that
    user's permanent id (a DB row is lazily provisioned for it on first
    use). So "join" no longer POSTs anywhere; it generates a UUID once,
    persists it in localStorage, and that's the elder's identity for
    every future visit. The display name typed at join is local-only
    cosmetic labelling ("You" in the chat feed) -- the gateway's `/me`
    always reports the same hardcoded "Test Elder" for every identity
    (see auth.py's own docstring: real per-user profile data is not
    part of the stub yet), so there is nothing to send it to.

  - Contacts: there is no user directory or invite-link service yet
    (only a QR-onboarding branch, not merged, is heading that
    direction), so a "contact" here is a locally-stored (name, id)
    pair the elder builds by exchanging raw ids out of band -- share
    "Your ID" from Home, paste someone else's into "+ Add someone".
    This is an honest stand-in for a future pairing flow, not a design
    choice: showing invented contacts backed by fake threads would be
    the UI lie the Week 4 version of this file explicitly called out
    and avoided for the shared circle case.

  - Messaging: one persistent WebSocket connection (established once,
    right after identity is ready, not per-contact) carries every
    frame -- `message.send` / `message.ack` / `message.new` /
    `message.delivered` / `message.status` / `sync.request` /
    `sync.batch`, per contracts/chat/envelope.py's FrameType. Opening a
    contact's chat screen sends `sync.request` for that
    (target_type=user, target_id) pair to catch up on history,
    including anything that arrived while the elder was elsewhere. On
    reconnect (not just first connect), any message still carrying only
    a client_msg_id -- no server id, meaning its ack never came back --
    is resent automatically, not left stuck at "Sending..." forever.
    Found live (2026-09-03): a mid-send WS connection dying under real
    staging-server instability left exactly this stuck. Safe to resend
    blindly: client_msg_id's uniqueness constraint on the server makes a
    genuine retry idempotent, recovering the original row rather than
    creating a duplicate.

  - Status UI reflects the real, and only the real, wire signals: a
    sent message shows "Sending..." (with a genuine Undo / DELETE
    /messages/{id} button) while it's still within the server's real
    undo window, then "Sent" once that window would have elapsed
    server-side (settings.UNDO_WINDOW_SECONDS -- mirrored here as a
    client-side timer since the server pushes no explicit
    pending->sent event to the sender's own socket, only the eventual
    `message.status` on delivery), then "Delivered" the moment a real
    `message.status` frame confirms the recipient's own
    `message.delivered` ack. No status is ever synthesized past what
    the gateway actually confirmed.
"""

import json
import os

import reflex as rx
from rxconfig import config

GATEWAY_PUBLIC_URL = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost")

# Mirrors services/gateway/app/config.py's Settings.UNDO_WINDOW_SECONDS
# default exactly -- the server never pushes a "now sent" event to the
# sender's own socket (message.new fan-out explicitly excludes the
# originating websocket; see app/messages.py::fan_out_message's
# `exclude` param), so the client times the same real window itself to
# move a message from "Sending... (cancelable)" to "Sent" once the
# server-side window would genuinely have closed. If this constant ever
# drifts from the server's, the only symptom is the Undo button staying
# enabled a little too long or too briefly right at the boundary -- the
# server's own DELETE /messages/{id} 409 response is still the actual
# authority, not this timer.
UNDO_WINDOW_SECONDS = 30

# ---------------------------------------------------------------------------
# Design tokens (verbatim from the Claude Design handoff spec)
# ---------------------------------------------------------------------------

COLOR = {
    "cream_canvas": "#F7F0E3",
    "card_cream": "#FFFCF6",
    "ink": "#2A2118",
    "muted_ink": "#6E6047",
    "saffron": "#B4531A",
    "saffron_pressed": "#8A3E0F",
    "deep_green": "#2F5D50",
    "green_tint": "#EAF1EC",
    "green_ink": "#1F4034",
    "gold_sand": "#FBEFD5",
    "gold_border": "#C68A22",
    "gold_pressed": "#F3DFAE",
    "warm_border": "#EADCC4",
    "halo_ring": "#F6D68A",
}

FONT_LATIN = "Mulish, 'Noto Sans Telugu', system-ui, sans-serif"
FONT_SERIF = "Lora, Georgia, serif"
FONT_MONO = "ui-monospace, Menlo, monospace"

TINTS = ["#F2E3C9", "#E7EFE6", "#F7E2D6", "#EFE7D2", "#E9E6DE"]

STYLESHEETS = [
    "https://fonts.googleapis.com/css2?family=Mulish:wght@400;600;700;800"
    "&family=Noto+Sans+Telugu:wght@400;600;700&family=Lora:wght@600&display=swap",
    # Responsive root font-size (assets/global.css) -- see that file's own
    # comment for why this is needed: every rem-based size below is
    # relative to the root, which stays browser-default 16px on every
    # device without this, making the app read smaller on a phone's
    # much smaller physical screen than on the laptop it was built on.
    "/global.css",
]

# ---------------------------------------------------------------------------
# Copy (bilingual)
# ---------------------------------------------------------------------------

# Each language in its own script. The choice screen shows these (not the translated names in
# TEXTS), so a person who cannot read the app's current language can still find their own.
LANGUAGE_NATIVE = {"te": "తెలుగు", "hi": "हिन्दी", "en": "English"}
LANGUAGE_CODES = tuple(LANGUAGE_NATIVE)

TEXTS = {
    "en": {
        "app_name": "SatSandesh",
        "tagline": "Your circle, in your language",
        "lang_switch_label": "తెలుగు",
        "lang_aria": "Switch to Telugu",
        "thought_label": "Thought for the day",
        "thought_text": "Speak kindly, and the whole hall grows quiet enough to listen.",
        "listen_aria": "Listen to the thought for the day",
        "heading": "Your People",
        "mic_aria": "Hold to speak a message",
        "tab_people": "People",
        "tab_satsang": "Satsang",
        "create_circle": "+ Create a circle",
        "join_circle": "Join a circle",
        "create_circle_title": "Create a circle",
        "create_circle_name_placeholder": "Circle name",
        "join_circle_title": "Join a circle",
        "join_circle_id_placeholder": "Circle ID (ask an admin to share it)",
        "create_button": "Create",
        "join_button_circle": "Join",
        "circle_error_invalid_id": "That doesn't look like a valid circle ID.",
        "circle_error_not_found": "No circle found with that ID.",
        "circle_error_generic": "Something went wrong. Try again.",
        "no_circles_yet": "No circles yet. Create one, or join with an ID.",
        "announcement_toggle_label": "Announcement channel (only admins post)",
        "announcement_badge": "Announcement",
        "send_error_not_member": "You're no longer a member of this circle.",
        "send_error_announcement_only": "Only a moderator or admin can post here.",
        "recording_label": "Recording...",
        "recorded_label": "Voice message recorded",
        "discard": "Discard",
        "voice_send": "Send",
        "voice_uploading": "Sending voice message...",
        "voice_send_failed": "Couldn't send. Tap to try again.",
        "mic_permission_denied": "Microphone access was denied. "
        "Allow microphone access to record a voice message.",
        "back": "< Back",
        "type_placeholder": "Type a message...",
        "send": "Send",
        "no_messages": "No messages yet. Say hello!",
        "join_prompt": "What should we call you?",
        "join_placeholder": "Your name",
        "join_button": "Continue",
        "connecting": "Connecting...",
        "your_id_label": "Your ID -- share this with family so they can add you",
        "copy_id": "Copy",
        "copied_id": "Copied!",
        "copy_failed": "Not copied",
        "circle_id_label": "Circle ID -- share this so others can join",
        "add_person": "+ Add someone",
        "add_person_title": "Add someone",
        "add_person_name_placeholder": "Their name",
        "add_person_id_placeholder": "Their ID (ask them to share it)",
        "add_button": "Add",
        "cancel_add_button": "Cancel",
        "add_error_invalid_id": "That doesn't look like a valid ID. Ask them to copy it exactly.",
        "add_error_self": "That's your own ID.",
        "add_error_duplicate": "Already added.",
        "no_contacts_yet": 'No one added yet. Tap "+ Add someone" and enter their ID.',
        "tap_to_chat": "Tap to open chat",
        "status_pending": "Sending... (tap to cancel)",
        "status_sent": "Sent",
        "status_delivered": "Delivered",
        "status_cancelled": "Cancelled",
        "status_held": "Waiting for review",
        "status_processing": "Processing...",
        "status_blocked": "Not sent",
        "quiet_hours_title": "Quiet hours",
        "quiet_hours_hint": "No message sounds or push alerts between these times.",
        "quiet_hours_start_label": "Starts",
        "quiet_hours_end_label": "Ends",
        "quiet_hours_save": "Save",
        "quiet_hours_saved": "Saved",
        "quiet_hours_off_hint": "Leave both blank for no quiet hours.",
        "content_language_title": "Language for voice messages",
        "content_language_hint": "What language should voice notes be translated into for you?",
        "lang_option_en": "English",
        "lang_option_hi": "Hindi",
        "lang_option_te": "Telugu",
        "choose_language_title": "Choose your language",
        "choose_language_hint": (
            "Pick the language you speak and read. Messages you get are translated into it, "
            "and your voice notes are understood in it. You choose once; for now it cannot be "
            "changed."
        ),
        "confirm_language_title": "Is this right?",
        "confirm_language_hint": "You will not be able to change this for now.",
        "confirm_language_yes": "Yes, continue",
        "confirm_language_back": "Go back",
        "your_language_title": "Your language",
        "your_language_locked_hint": (
            "You chose this when you started. It cannot be changed here for now."
        ),
        "tts_title": "Read messages aloud",
        "tts_on_label": "On",
        "tts_off_label": "Off",
        "autoplay_title": "Play new voice messages automatically",
        "recv_show_original": "Show original",
        "recv_show_english": "Show original (English)",
        "recv_show_translation": "Show translation",
        "recv_audio_failed": "Could not load the audio. Tap to try again.",
        "recv_speed": "Speed",
        "recv_transcript": "Text",
        "recv_no_audio": "No audio for this message",
        "recv_approximate": "Approximate translation",
    },
    "te": {
        "app_name": "సత్‌సందేశ్",
        "tagline": "మీ వాళ్ళు, మీ భాషలో",
        "lang_switch_label": "English",
        "lang_aria": "Switch to English",
        "thought_label": "ఈ రోజు ఆలోచన",
        "thought_text": "మృదువుగా మాట్లాడండి — సభ అంతా వినడానికి నిశ్శబ్దమవుతుంది.",
        "listen_aria": "ఈ రోజు ఆలోచన వినండి",
        "heading": "మీ వాళ్ళు",
        "mic_aria": "మాట్లాడటానికి నొక్కి పట్టుకోండి",
        "tab_people": "మీ వాళ్ళు",
        "tab_satsang": "సత్సంగం",
        "create_circle": "+ సర్కిల్ సృష్టించండి",
        "join_circle": "సర్కిల్‌లో చేరండి",
        "create_circle_title": "సర్కిల్ సృష్టించండి",
        "create_circle_name_placeholder": "సర్కిల్ పేరు",
        "join_circle_title": "సర్కిల్‌లో చేరండి",
        "join_circle_id_placeholder": "సర్కిల్ ఐడీ (అడ్మిన్‌ని పంచుకోమని అడగండి)",
        "create_button": "సృష్టించు",
        "join_button_circle": "చేరండి",
        "circle_error_invalid_id": "ఇది సరైన సర్కిల్ ఐడీలా లేదు.",
        "circle_error_not_found": "ఆ ఐడీతో సర్కిల్ కనుగొనబడలేదు.",
        "circle_error_generic": "ఏదో తప్పు జరిగింది. మళ్ళీ ప్రయత్నించండి.",
        "no_circles_yet": "ఇంకా సర్కిల్‌లు లేవు. ఒకటి సృష్టించండి, లేదా ఐడీతో చేరండి.",
        "announcement_toggle_label": "ప్రకటన ఛానెల్ (అడ్మిన్లు మాత్రమే పోస్ట్ చేయగలరు)",
        "announcement_badge": "ప్రకటన",
        "send_error_not_member": "మీరు ఇకపై ఈ సర్కిల్‌లో సభ్యులు కాదు.",
        "send_error_announcement_only": "ఇక్కడ మోడరేటర్ లేదా అడ్మిన్ మాత్రమే పోస్ట్ చేయగలరు.",
        "recording_label": "రికార్డ్ అవుతోంది...",
        "recorded_label": "వాయిస్ సందేశం రికార్డ్ చేయబడింది",
        "discard": "తొలగించు",
        "voice_send": "పంపండి",
        "voice_uploading": "వాయిస్ సందేశం పంపుతోంది...",
        "voice_send_failed": "పంపలేకపోయాం. మళ్ళీ ప్రయత్నించడానికి నొక్కండి.",
        "mic_permission_denied": "మైక్రోఫోన్ యాక్సెస్ నిరాకరించబడింది. దయచేసి అనుమతించండి.",
        "back": "< వెనుకకు",
        "type_placeholder": "సందేశం టైప్ చేయండి...",
        "send": "పంపండి",
        "no_messages": "ఇంకా సందేశాలు లేవు. హలో చెప్పండి!",
        "join_prompt": "మిమ్మల్ని ఏమని పిలవాలి?",
        "join_placeholder": "మీ పేరు",
        "join_button": "కొనసాగించు",
        "connecting": "కనెక్ట్ అవుతోంది...",
        "your_id_label": "మీ ఐడీ — కుటుంబంతో పంచుకోండి, వారు మిమ్మల్ని చేర్చుకోవచ్చు",
        "copy_id": "కాపీ చేయి",
        "copied_id": "కాపీ అయ్యింది!",
        "copy_failed": "కాపీ కాలేదు",
        "circle_id_label": "సర్కిల్ ఐడీ — ఇతరులు చేరడానికి దీన్ని పంచుకోండి",
        "add_person": "+ ఎవరినైనా చేర్చు",
        "add_person_title": "ఎవరినైనా చేర్చు",
        "add_person_name_placeholder": "వారి పేరు",
        "add_person_id_placeholder": "వారి ఐడీ (వారిని పంచుకోమని అడగండి)",
        "add_button": "చేర్చు",
        "cancel_add_button": "రద్దు చేయి",
        "add_error_invalid_id": "ఇది సరైన ఐడీలా లేదు. సరిగ్గా కాపీ చేయమని అడగండి.",
        "add_error_self": "అది మీ స్వంత ఐడీ.",
        "add_error_duplicate": "ఇప్పటికే చేర్చబడింది.",
        "no_contacts_yet": '"+ ఎవరినైనా చేర్చు" నొక్కి వారి ఐడీని నమోదు చేయండి.',
        "tap_to_chat": "చాట్ తెరవడానికి నొక్కండి",
        "status_pending": "పంపుతోంది... (రద్దు చేయడానికి నొక్కండి)",
        "status_sent": "పంపబడింది",
        "status_delivered": "అందింది",
        "status_cancelled": "రద్దు చేయబడింది",
        "status_held": "సమీక్ష కోసం వేచి ఉంది",
        "status_processing": "ప్రాసెస్ అవుతోంది...",
        "status_blocked": "పంపబడలేదు",
        "quiet_hours_title": "నిశ్శబ్ద సమయం",
        "quiet_hours_hint": "ఈ సమయాల మధ్య సందేశ శబ్దాలు లేదా పుష్ నోటిఫికేషన్లు రావు.",
        "quiet_hours_start_label": "మొదలు",
        "quiet_hours_end_label": "ముగింపు",
        "quiet_hours_save": "సేవ్ చేయి",
        "quiet_hours_saved": "సేవ్ అయ్యింది",
        "quiet_hours_off_hint": "నిశ్శబ్ద సమయం వద్దంటే రెండూ ఖాళీగా ఉంచండి.",
        "content_language_title": "వాయిస్ సందేశాల భాష",
        "content_language_hint": "మీ కోసం వాయిస్ నోట్స్ ఏ భాషలో అనువదించాలి?",
        "lang_option_en": "ఇంగ్లీష్",
        "lang_option_hi": "హిందీ",
        "lang_option_te": "తెలుగు",
        "choose_language_title": "మీ భాషను ఎంచుకోండి",
        "choose_language_hint": (
            "మీరు మాట్లాడే, చదివే భాషను ఎంచుకోండి. మీకు వచ్చే సందేశాలు ఈ "
            "భాషలోకి అనువదించబడతాయి, మీ వాయిస్ నోట్స్ ఈ భాషలో "
            "అర్థమవుతాయి. ఒక్కసారి మాత్రమే ఎంచుకోవచ్చు; ప్రస్తుతం "
            "మార్చలేరు."
        ),
        "confirm_language_title": "ఇది సరైనదేనా?",
        "confirm_language_hint": "ప్రస్తుతం దీన్ని మార్చలేరు.",
        "confirm_language_yes": "అవును, కొనసాగించు",
        "confirm_language_back": "వెనక్కి",
        "your_language_title": "మీ భాష",
        "your_language_locked_hint": "మీరు ప్రారంభంలో దీన్ని ఎంచుకున్నారు. ప్రస్తుతం ఇక్కడ మార్చలేరు.",
        "tts_title": "సందేశాలను చదివి వినిపించు",
        "tts_on_label": "ఆన్",
        "tts_off_label": "ఆఫ్",
        "autoplay_title": "కొత్త వాయిస్ సందేశాలను ఆటోమేటిక్‌గా ప్లే చేయి",
        "recv_show_original": "అసలు చూపించు",
        "recv_show_english": "అసలు (ఇంగ్లీష్) చూపించు",
        "recv_show_translation": "అనువాదం చూపించు",
        "recv_audio_failed": "ఆడియో లోడ్ కాలేదు. మళ్లీ ప్రయత్నించడానికి నొక్కండి.",
        "recv_speed": "వేగం",
        "recv_transcript": "వచనం",
        "recv_no_audio": "ఈ సందేశానికి ఆడియో లేదు",
        "recv_approximate": "సుమారు అనువాదం",
    },
}

# ---------------------------------------------------------------------------
# Client-side JS: real identity bootstrap + a persistent WebSocket carrying
# services/gateway's real per-contact protocol (contracts/chat/envelope.py's
# FrameType values). Pattern (on_mount + rx.call_script, not rx.script;
# DOM-rendered feed via a plain node rather than Reflex state) inherited
# from the earlier shared-circle version and, before that, from
# elder_app/gateway_ws_proof.py -- a persistent socket pushing many async
# updates still doesn't fit rx.call_script's one-shot call/callback shape.
# ---------------------------------------------------------------------------

# crypto.randomUUID() only exists in a secure context (HTTPS, or
# localhost) -- confirmed live: it throws "crypto.randomUUID is not a
# function" when this app is loaded over plain http:// at a LAN IP, which
# is exactly how the team runs it before TLS/Caddy is in front of a real
# domain. This fallback (Math.random-seeded, not cryptographically
# strong, but only ever used as a client-side correlation id, never a
# security token) keeps identity bootstrap and message sending working
# in that real, common case.
_UUID_V4_JS_FALLBACK = """
    function satUuidV4() {
        if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
            const r = (Math.random() * 16) | 0;
            const v = c === "x" ? r : (r & 0x3) | 0x8;
            return v.toString(16);
        });
    }
"""

ENSURE_IDENTITY_JS = (
    """
(() => {
"""
    + _UUID_V4_JS_FALLBACK
    + """
    let id = localStorage.getItem("satsandesh_my_id");
    if (!id) {
        id = satUuidV4();
        localStorage.setItem("satsandesh_my_id", id);
    }
    window.__satUserId = id;
    window.__satToken = id;
    return id;
})()
"""
)

LOAD_NAME_JS = """
(() => localStorage.getItem("satsandesh_my_name") || "")()
"""

SAVE_NAME_JS_TEMPLATE = """
(() => {
    localStorage.setItem("satsandesh_my_name", %(name)s);
    return "ok";
})()
"""

LOAD_CONTACTS_JS = """
(() => {
    try {
        return localStorage.getItem("satsandesh_contacts") || "[]";
    } catch (err) {
        return "[]";
    }
})()
"""

ADD_CONTACT_JS_TEMPLATE = """
(() => {
    const name = (%(name)s || "").trim();
    const rawId = (%(target_id)s || "").trim();
    const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!uuidRe.test(rawId)) return "error:invalid_id";
    if (rawId.toLowerCase() === (window.__satUserId || "").toLowerCase()) return "error:self";
    let contacts = [];
    try {
        contacts = JSON.parse(localStorage.getItem("satsandesh_contacts") || "[]");
    } catch (err) {
        contacts = [];
    }
    if (contacts.some((c) => c.id.toLowerCase() === rawId.toLowerCase())) {
        return "error:duplicate";
    }
    const tints = %(tints)s;
    const label = name || rawId.slice(0, 8);
    contacts.push({
        id: rawId,
        name: label,
        initial: label.trim().charAt(0).toUpperCase(),
        tint: tints[contacts.length %% tints.length],
    });
    localStorage.setItem("satsandesh_contacts", JSON.stringify(contacts));
    return JSON.stringify(contacts);
})()
"""

# `navigator.clipboard` exists only on a secure page (HTTPS or localhost). On plain HTTP --
# which is what staging is today -- it is undefined, so the old code threw, was caught, and
# the Copy button did nothing and said nothing. The fallback is the older path: put the text
# in an off-screen textarea, select it, and run the browser's copy command, which works on
# any page when it follows a tap.
COPY_ID_JS_TEMPLATE = """
(async () => {
    const text = %(my_id)s;
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
            return "ok";
        }
    } catch (err) {}
    try {
        const box = document.createElement("textarea");
        box.value = text;
        box.setAttribute("readonly", "");
        box.style.position = "fixed";
        box.style.top = "0";
        box.style.left = "0";
        box.style.opacity = "0";
        document.body.appendChild(box);
        box.focus();
        box.select();
        box.setSelectionRange(0, text.length);
        const copied = document.execCommand("copy");
        document.body.removeChild(box);
        return copied ? "ok" : "error";
    } catch (err) {
        return "error";
    }
})()
"""

# The persistent connection + all shared rendering/state-tracking helpers.
# Idempotent via window.__satsandeshWsInit -- connect_chat is called once,
# right after identity is ready, and stays up for the whole session; a
# contact's chat screen only needs to (re)render from the already-live
# window.__satConversations cache and issue a sync.request, both handled by
# OPEN_CHAT_JS_TEMPLATE below, not by reconnecting.
CHAT_CONNECT_JS_TEMPLATE = """
if (window.__satsandeshWsInit) {
  console.log("[satsandesh-ws] already initialized, skipping");
} else {
window.__satsandeshWsInit = true;
(function () {
  const GATEWAY_URL = %(gateway_url)s;
  const WS_URL = GATEWAY_URL.replace(/^http/, "ws");
  const UNDO_WINDOW_MS = %(undo_window_seconds)s * 1000;
  %(uuid_v4_fallback)s
  window.__satUuidV4 = satUuidV4;

  // Which language a typed message is in, from the characters themselves. The
  // gateway's pipeline translates from `source_lang`, and without it falls back
  // to the author's STORED language (Telugu for everyone today), so a Hindi
  // message would be translated as if it were Telugu. Telugu and Devanagari are
  // unambiguous by script; Latin letters mean English. Whichever script has the
  // most letters wins, and a tie (or no letters at all) declares nothing rather
  // than guessing. Only values the pipeline supports are ever returned.
  window.__satDetectLang = function (text) {
    let te = 0, hi = 0, en = 0;
    for (const ch of String(text || "")) {
      const c = ch.codePointAt(0);
      if (c >= 0x0C00 && c <= 0x0C7F) te++;
      else if (c >= 0x0900 && c <= 0x097F) hi++;
      else if ((c >= 0x41 && c <= 0x5A) || (c >= 0x61 && c <= 0x7A)) en++;
    }
    const best = Math.max(te, hi, en);
    if (best === 0) return null;
    const winners = [["te", te], ["hi", hi], ["en", en]].filter((x) => x[1] === best);
    return winners.length === 1 ? winners[0][0] : null;
  };

  window.__satConversations = window.__satConversations || {};
  window.__satCurrentContactId = window.__satCurrentContactId || "";
  window.__satPendingTimers = window.__satPendingTimers || {};

  let ws = null;
  let backoffMs = 1000;
  const MAX_BACKOFF_MS = 30000;
  let reconnectAttempt = 0;

  function setStatus(text) {
    const el = document.getElementById("live-chat-status");
    if (el) el.textContent = text;
    console.log("[satsandesh-ws] status:", text);
  }

  function threadFor(contactId) {
    if (!window.__satConversations[contactId]) window.__satConversations[contactId] = [];
    return window.__satConversations[contactId];
  }

  // The message's own status words, in the UI language (this JS is built once, so it is
  // handed both languages and picks by the current one -- it used to be English only).
  const STATUS_TEXT = %(status_text)s;
  function statusWord(key) {
    const ui = (window.__satPrefs && window.__satPrefs.ui) || "en";
    return (STATUS_TEXT[ui] || STATUS_TEXT.en)[key];
  }

  // How long ago the sender's own device heard the server accept this message. A message the
  // server still calls `pending` after the undo window is not 'sending' any more and cannot be
  // undone: with the pipeline on, delivery waits for transcription, translation and moderation,
  // which can take a minute (it was measured at 6 to 60 s for one Telugu note), and a held
  // message can stay pending until a person decides. Locally-sent messages are timed from the
  // ack; ones loaded from the server are timed from its created_at.
  function pastUndoWindow(msg) {
    const since = msg.ack_at || (msg.created_at ? Date.parse(msg.created_at) : 0);
    return !!since && Date.now() > since + UNDO_WINDOW_MS;
  }

  function statusLabel(msg) {
    if (msg.status === "cancelled") return statusWord("status_cancelled");
    // Set aside by the pipeline or a moderator. The sender is told only that it did not go
    // out yet or at all -- the gateway has no wire surface for a reason, and the label must
    // not invite a tap (only a pending message can be cancelled).
    if (msg.status === "held") return statusWord("status_held");
    if (msg.status === "blocked") return statusWord("status_blocked");
    // Set by handleFrame's "error" branch below when the server rejects a
    // message.send -- correlated back to this specific bubble via
    // client_msg_id, since without that the bubble would otherwise sit
    // stuck at "Sending..." forever, indistinguishable from a slow network.
    if (msg.status === "failed") return msg.error_text || %(send_error_not_member)s;
    // Circle messages: an aggregate count is more informative than a
    // single delivered/not-delivered boolean the moment even one member
    // has confirmed -- shown in preference to the plain status labels
    // below, same reasoning as contracts/chat/messages.py's
    // MessageStatusOut docstring (a partial count, not a status value).
    if (
      msg.target_type === "circle" &&
      typeof msg.delivered_count === "number" &&
      msg.delivered_count > 0
    ) {
      return msg.delivered_count + "/" + msg.member_count;
    }
    if (msg.status === "delivered") return statusWord("status_delivered");
    if (msg.status === "sent") return statusWord("status_sent");
    if (msg.status === "pending" && pastUndoWindow(msg)) return statusWord("status_processing");
    return statusWord("status_pending");
  }

  // ---- Week 7: the receiver experience ----------------------------------
  // What a message shows its receiver: the translation into their own language
  // (text, plus audio when speech was made and they have it on), the original
  // always one tap away, 0.8x-1.2x speed, and opt-in autoplay. A message with no
  // rendering in their language just shows the original, as before.
  //
  // Audio is fetched WITH the login token and played from a blob URL: GET
  // /media/{id} needs the bearer header, and an <audio src=...> cannot send one,
  // so the old direct src was refused by the real gateway.
  const RECV_TEXT = %(recv_text)s;
  const SPEEDS = [0.8, 1, 1.2];
  window.__satShowOriginal = window.__satShowOriginal || {};
  window.__satAudioUrls = window.__satAudioUrls || {};
  window.__satAudioPending = window.__satAudioPending || {};

  function recvText() {
    const ui = (window.__satPrefs && window.__satPrefs.ui) || "en";
    return RECV_TEXT[ui] || RECV_TEXT.en;
  }

  function currentSpeed() {
    if (typeof window.__satSpeed !== "number") {
      let saved = 1;
      try { saved = parseFloat(localStorage.getItem("sat_speed")) || 1; } catch (e) {}
      window.__satSpeed = SPEEDS.indexOf(saved) !== -1 ? saved : 1;
    }
    return window.__satSpeed;
  }

  function styleChoice(button, on) {
    button.style.background = on ? "#2F5D50" : "#FFFCF6";
    button.style.color = on ? "#FFFCF6" : "#4A3A24";
    button.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function makeButton(label, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.style.minHeight = "44px";
    b.style.minWidth = "44px";
    b.style.padding = "0 12px";
    b.style.borderRadius = "12px";
    b.style.fontWeight = "700";
    b.style.fontSize = "16px";
    b.style.cursor = "pointer";
    b.style.border = "2px solid #EADCC4";
    b.style.fontFamily = "inherit";
    styleChoice(b, false);
    b.onclick = onClick;
    return b;
  }

  function applySpeed(speed) {
    window.__satSpeed = speed;
    try { localStorage.setItem("sat_speed", String(speed)); } catch (e) {}
    document.querySelectorAll("#live-chat-messages audio").forEach((a) => {
      a.playbackRate = speed;
    });
    document.querySelectorAll("#live-chat-messages [data-speed]").forEach((b) => {
      styleChoice(b, parseFloat(b.dataset.speed) === speed);
    });
  }

  function mediaIdOf(ref) {
    return String(ref.uri || "").replace(/^media:/, "");
  }

  function loadAudioUrl(ref) {
    const id = mediaIdOf(ref);
    if (window.__satAudioUrls[id]) return Promise.resolve(window.__satAudioUrls[id]);
    if (!window.__satAudioPending[id]) {
      window.__satAudioPending[id] = fetch(GATEWAY_URL + "/media/" + id, {
        headers: { Authorization: "Bearer " + window.__satToken },
      })
        .then((resp) => {
          if (!resp.ok) throw new Error("audio fetch failed: " + resp.status);
          return resp.blob();
        })
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          window.__satAudioUrls[id] = url;
          return url;
        })
        .finally(() => { delete window.__satAudioPending[id]; });
    }
    return window.__satAudioPending[id];
  }

  // `resume` is what was playing before this re-render (renderCurrentThread
  // rebuilds the whole thread on every update): without it a status tick or a
  // new message would cut off a voice note the receiver is listening to.
  function audioPlayer(ref, key, resume) {
    const t = recvText();
    const wrap = document.createElement("div");
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "auto";
    audio.dataset.key = key;
    audio.style.width = "100%%";
    audio.style.maxWidth = "260px";
    audio.defaultPlaybackRate = currentSpeed();
    audio.playbackRate = currentSpeed();
    wrap.appendChild(audio);

    const failed = document.createElement("button");
    failed.type = "button";
    failed.textContent = t.recv_audio_failed;
    failed.style.display = "none";
    failed.style.minHeight = "44px";
    failed.style.padding = "6px 10px";
    failed.style.border = "none";
    failed.style.background = "transparent";
    failed.style.color = "#8A3E0F";
    failed.style.fontWeight = "700";
    failed.style.fontFamily = "inherit";
    failed.style.textAlign = "left";
    wrap.appendChild(failed);

    function attach() {
      failed.style.display = "none";
      loadAudioUrl(ref).then((url) => {
        audio.src = url;
        audio.playbackRate = currentSpeed();
        const wantsAutoplay =
          window.__satAutoplayKey && key.indexOf(window.__satAutoplayKey + ":") === 0;
        if (wantsAutoplay) window.__satAutoplayKey = null;
        if (resume && resume.key === key) {
          audio.addEventListener("loadedmetadata", () => {
            audio.currentTime = resume.time;
            audio.playbackRate = currentSpeed();
            audio.play().catch(() => {});
          }, { once: true });
        } else if (wantsAutoplay) {
          audio.play().catch(() => {});
        }
      }).catch(() => { failed.style.display = "block"; });
    }
    failed.onclick = attach;
    attach();

    const speedRow = document.createElement("div");
    speedRow.style.display = "flex";
    speedRow.style.alignItems = "center";
    speedRow.style.gap = "6px";
    speedRow.style.marginTop = "6px";
    const speedLabel = document.createElement("span");
    speedLabel.textContent = t.recv_speed;
    speedLabel.style.fontSize = "14px";
    speedLabel.style.color = "#6E6047";
    speedRow.appendChild(speedLabel);
    for (const speed of SPEEDS) {
      const b = makeButton(speed + "×", () => applySpeed(speed));
      b.dataset.speed = String(speed);
      styleChoice(b, speed === currentSpeed());
      speedRow.appendChild(b);
    }
    wrap.appendChild(speedRow);
    return wrap;
  }

  function textBlock(text, size, color) {
    const el = document.createElement("div");
    el.style.fontSize = size || "20px";
    el.style.color = color || "#2A2118";
    el.textContent = text || "";
    return el;
  }

  function noteLine(text) {
    const el = document.createElement("div");
    el.style.fontSize = "14px";
    el.style.color = "#6E6047";
    el.style.margin = "4px 0";
    el.textContent = text;
    return el;
  }

  function findRendering(msg, lang) {
    if (!Array.isArray(msg.renderings)) return null;
    return msg.renderings.find((r) => r.language === lang) || null;
  }

  // The reader's chosen language L decides what they see:
  //  - L is English: the English text, no 'show original' button, and for a voice note a play
  //    button for the real recording (English has no synthesized voice).
  //  - L is another language: the message in L, with a button that swaps to the English
  //    version of what was said. If the gateway has not sent an English version, the button
  //    shows the sender's own words instead (the real recording and its text), as before.
  function buildMessageBody(bubble, msg, isOwn, key, resume) {
    const prefs = window.__satPrefs || {};
    const t = recvText();
    const lang = prefs.lang || "en";
    const rendering = isOwn ? null : findRendering(msg, lang);
    const english = rendering && lang !== "en" ? findRendering(msg, "en") : null;
    const canToggle = !!rendering && lang !== "en";
    const showOriginal = !rendering || (canToggle && !!window.__satShowOriginal[key]);

    if (rendering && !showOriginal) {
      bubble.appendChild(textBlock(rendering.text));
      const playRealRecording = lang === "en" && msg.kind === "voice" && msg.media_ref;
      if (playRealRecording) {
        bubble.appendChild(audioPlayer(msg.media_ref, key + ":o", resume));
      } else if (prefs.tts !== false) {
        if (rendering.audio) {
          bubble.appendChild(audioPlayer(rendering.audio, key + ":t", resume));
        } else if (
          rendering.degraded_reason === "text_only" ||
          rendering.degraded_reason === "tts_skipped"
        ) {
          bubble.appendChild(noteLine(t.recv_no_audio));
        }
      }
      if (rendering.degraded_reason === "model_fallback") {
        bubble.appendChild(noteLine(t.recv_approximate));
      }
    } else if (rendering && english) {
      bubble.appendChild(textBlock(english.text));
    } else if (msg.kind === "voice") {
      if (msg.media_ref) bubble.appendChild(audioPlayer(msg.media_ref, key + ":o", resume));
      else if (!msg.transcript) bubble.appendChild(noteLine(t.recv_no_audio));
      if (msg.transcript) {
        bubble.appendChild(noteLine(t.recv_transcript));
        bubble.appendChild(textBlock(msg.transcript));
      }
    } else {
      bubble.appendChild(textBlock(msg.text));
    }

    if (canToggle) {
      const toggle = makeButton(
        showOriginal
          ? t.recv_show_translation
          : english
            ? t.recv_show_english
            : t.recv_show_original,
        () => {
          window.__satShowOriginal[key] = !showOriginal;
          renderCurrentThread();
        }
      );
      toggle.style.marginTop = "8px";
      bubble.appendChild(toggle);
    }
  }

  function renderCurrentThread() {
    const messagesEl = document.getElementById("live-chat-messages");
    const contactId = window.__satCurrentContactId;
    if (!messagesEl || !contactId) return;
    const thread = threadFor(contactId);
    const playingNow = Array.from(messagesEl.querySelectorAll("audio")).find(
      (a) => !a.paused && !a.ended && a.dataset.key
    );
    const resume = playingNow
      ? { key: playingNow.dataset.key, time: playingNow.currentTime }
      : null;
    messagesEl.innerHTML = "";
    if (thread.length === 0) {
      const empty = document.createElement("div");
      empty.style.margin = "8px 0";
      empty.style.fontSize = "14px";
      empty.style.color = "#6E6047";
      empty.style.textAlign = "center";
      empty.textContent = %(no_messages)s;
      messagesEl.appendChild(empty);
      return;
    }
    for (const msg of thread) {
      const isOwn = msg.author_id === window.__satUserId;
      const row = document.createElement("div");
      row.style.margin = "10px 0";
      row.style.display = "flex";
      row.style.justifyContent = isOwn ? "flex-end" : "flex-start";
      const bubble = document.createElement("div");
      bubble.style.maxWidth = "80%%";
      bubble.style.padding = "12px 16px";
      bubble.style.borderRadius = isOwn ? "16px 16px 4px 16px" : "16px 16px 16px 4px";
      bubble.style.background = isOwn ? "#EAF1EC" : "#FFFCF6";
      bubble.style.border = isOwn ? "none" : "1px solid #EADCC4";
      bubble.style.fontFamily = "Mulish, 'Noto Sans Telugu', system-ui, sans-serif";
      bubble.style.cursor =
        isOwn && msg.status === "pending" && !pastUndoWindow(msg) ? "pointer" : "default";
      if (!isOwn && msg.target_type === "circle") {
        // No user directory exists yet (see module docstring's
        // "Contacts" section) -- a circle has no way to resolve another
        // member's id to a name, so this shows the honest thing it can,
        // a short id fragment, rather than inventing one.
        const who = document.createElement("div");
        who.style.fontSize = "12px";
        who.style.fontWeight = "700";
        who.style.color = "#6E6047";
        who.style.marginBottom = "2px";
        who.textContent = (msg.author_id || "").slice(0, 8);
        bubble.appendChild(who);
      }
      buildMessageBody(
        bubble,
        msg,
        isOwn,
        msg.id || msg.client_msg_id || String(thread.indexOf(msg)),
        resume
      );
      if (isOwn) {
        const status = document.createElement("div");
        status.style.fontSize = "12px";
        status.style.marginTop = "4px";
        const isCancelledOrFailed =
          msg.status === "cancelled" || msg.status === "failed" || msg.status === "blocked";
        status.style.color = isCancelledOrFailed ? "#8A3E0F" : "#6E6047";
        status.style.fontStyle = isCancelledOrFailed ? "italic" : "normal";
        status.textContent = statusLabel(msg);
        bubble.appendChild(status);
        if (msg.status === "pending" && msg.id && !pastUndoWindow(msg)) {
          bubble.onclick = () => cancelMessage(msg.id, contactId);
        }
      }
      row.appendChild(bubble);
      messagesEl.appendChild(row);
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function upsertMessage(contactId, msg) {
    const thread = threadFor(contactId);
    const idx = thread.findIndex(
      (m) =>
        (msg.id && m.id === msg.id) ||
        (msg.client_msg_id && m.client_msg_id === msg.client_msg_id)
    );
    if (idx === -1) {
      thread.push(msg);
    } else {
      thread[idx] = Object.assign({}, thread[idx], msg);
    }
    if (window.__satCurrentContactId === contactId) renderCurrentThread();
  }

  // After the undo window, ask the server what became of the message instead of assuming it was
  // sent: the window ending only means the SERVER is now free to deliver it. Delivery waits for
  // the pipeline when it is on, and a message can be held for a person or fail closed, so a blind
  // local 'Sent' told the sender something untrue. Polling GET /messages (the author sees their
  // own messages in every status) and merging only the status avoids replacing the whole thread,
  // which the sync frame does and which would drop messages still in flight.
  // Quick while an answer is likely (a note is usually through the pipeline within a minute or
  // two), then slow and never given up on: a message a person has to rule on can stay pending for
  // hours, and stopping after a few minutes left 'Processing...' on screen until a reload even
  // after the server had decided. One small request a minute per such message is cheap.
  const POLL_EVERY_MS = 5000;
  const POLL_QUICK_FOR_MS = 5 * 60 * 1000;
  const POLL_SLOW_EVERY_MS = 60 * 1000;

  async function pollMessageStatus(contactId, messageId, startedAt) {
    const thread = threadFor(contactId);
    const local = thread.find((x) => x.id === messageId);
    if (!local || local.status !== "pending") return;
    try {
      const params = new URLSearchParams({
        target_type: local.target_type || "user",
        target_id: contactId,
        limit: "200",
      });
      const resp = await fetch(GATEWAY_URL + "/messages?" + params.toString(), {
        headers: { Authorization: "Bearer " + window.__satToken },
      });
      if (resp.ok) {
        const batch = await resp.json();
        const remote = (batch.messages || []).find((m) => m.id === messageId);
        if (remote && remote.status !== "pending") {
          upsertMessage(contactId, { id: messageId, status: remote.status });
          return;
        }
      }
    } catch (err) {
      // Offline or a blip: keep showing 'Processing' and try again.
    }
    const quick = Date.now() - startedAt < POLL_QUICK_FOR_MS;
    window.__satPendingTimers[messageId] = setTimeout(
      () => pollMessageStatus(contactId, messageId, startedAt),
      quick ? POLL_EVERY_MS : POLL_SLOW_EVERY_MS
    );
  }

  function scheduleSentTransition(contactId, messageId) {
    if (window.__satPendingTimers[messageId]) clearTimeout(window.__satPendingTimers[messageId]);
    window.__satPendingTimers[messageId] = setTimeout(() => {
      // The window is over: the label changes from 'Sending... (tap to cancel)' to 'Processing...'
      // and the server is asked for the real status until it answers.
      renderIfCurrent(contactId);
      pollMessageStatus(contactId, messageId, Date.now());
    }, UNDO_WINDOW_MS + 1000);
  }

  function renderIfCurrent(contactId) {
    if (window.__satCurrentContactId === contactId) renderCurrentThread();
  }

  function sendDeliveredAck(messageId) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "message.delivered", data: { message_id: messageId } }));
    }
  }

  function otherPartyId(msg) {
    return msg.author_id === window.__satUserId ? msg.target_id : msg.author_id;
  }

  function cancelMessage(messageId, contactId) {
    fetch(GATEWAY_URL + "/messages/" + messageId, {
      method: "DELETE",
      headers: { Authorization: "Bearer " + window.__satToken },
    }).then((resp) => {
      if (resp.status === 204) {
        if (window.__satPendingTimers[messageId]) {
          clearTimeout(window.__satPendingTimers[messageId]);
          delete window.__satPendingTimers[messageId];
        }
        upsertMessage(contactId, { id: messageId, status: "cancelled" });
      }
    });
  }
  window.__satCancelMessage = cancelMessage;

  function sendMessageFrame(contactId, payload, clientMsgId, targetType) {
    // payload carries whatever varies by message kind (text: {kind, text};
    // voice: {kind, media_ref}) -- client_msg_id/target_type/target_id are
    // the same for every kind, so they're filled in here once rather than
    // duplicated at each call site.
    window.__satPendingSendTarget = window.__satPendingSendTarget || {};
    window.__satPendingSendTarget[clientMsgId] = contactId;
    const data = Object.assign(
      { client_msg_id: clientMsgId, target_type: targetType || "user", target_id: contactId },
      payload
    );
    ws.send(JSON.stringify({ type: "message.send", data: data }));
  }
  window.__satSendMessage = sendMessageFrame;

  function resendUnacked() {
    // A message still carrying only client_msg_id (no server id) is one
    // whose ack never arrived -- most likely because the connection it
    // was sent on died before the reply came back (see the healthwatch
    // incident this session: a mid-flight WS getting cut by a server
    // restart). The reconnect logic below already re-establishes the
    // socket; this is the other half -- re-sending anything still
    // unacknowledged once that socket is open again, rather than leaving
    // it stuck at "Sending..." forever. Safe to resend blindly: the
    // server's client_msg_id uniqueness constraint
    // (services/gateway/app/db/repository.py's _create_message_impl)
    // makes this idempotent -- a genuine retry recovers the same row and
    // returns the same ack, it does not create a duplicate message.
    // target_type defaults to "user" for a message stored before circle
    // support existed, matching sendMessageFrame's own default.
    //
    // status !== "failed" matters: a message the server explicitly
    // rejected (e.g. posting to an announcement circle without
    // moderator/admin rights, see handleFrame's "error" branch) also has
    // no id, same as a genuinely dropped send -- but resending it blindly
    // on every reconnect would just get rejected again forever. Only a
    // message that never got *any* response (connection dropped, not
    // authorization denied) belongs in a real retry.
    for (const contactId of Object.keys(window.__satConversations)) {
      for (const msg of window.__satConversations[contactId]) {
        if (
          msg.author_id === window.__satUserId &&
          !msg.id &&
          msg.client_msg_id &&
          msg.status !== "failed"
        ) {
          const payload =
            msg.kind === "voice"
              ? msg.source_lang
                ? { kind: "voice", media_ref: msg.media_ref, source_lang: msg.source_lang }
                : { kind: "voice", media_ref: msg.media_ref }
              : msg.source_lang
                ? { kind: "text", text: msg.text, source_lang: msg.source_lang }
                : { kind: "text", text: msg.text };
          sendMessageFrame(contactId, payload, msg.client_msg_id, msg.target_type);
        }
      }
    }
  }

  function handleFrame(frame) {
    if (frame.type === "message.ack") {
      const data = frame.data;
      const contactId =
        window.__satPendingSendTarget && window.__satPendingSendTarget[data.client_msg_id];
      if (contactId) {
        upsertMessage(contactId, {
          client_msg_id: data.client_msg_id,
          id: data.id,
          status: data.status,
          ack_at: Date.now(),
        });
        scheduleSentTransition(contactId, data.id);
        delete window.__satPendingSendTarget[data.client_msg_id];
      }
    } else if (frame.type === "message.new") {
      const data = frame.data;
      // A circle message's thread key is the circle's own id, regardless
      // of who sent it -- otherPartyId's "the other side of a DM" logic
      // doesn't apply once there can be more than two parties.
      const contactId = data.target_type === "circle" ? data.target_id : otherPartyId(data);
      if (
        data.author_id !== window.__satUserId &&
        data.kind === "voice" &&
        window.__satPrefs &&
        window.__satPrefs.autoplay
      ) {
        window.__satAutoplayKey = data.id;
      }
      upsertMessage(contactId, {
        id: data.id,
        author_id: data.author_id,
        target_id: data.target_id,
        target_type: data.target_type,
        kind: data.kind,
        text: data.text,
        media_ref: data.media_ref,
        renderings: data.renderings || [],
        transcript: data.transcript || null,
        transcript_language: data.transcript_language || null,
        status: data.status,
      });
      if (data.author_id !== window.__satUserId) sendDeliveredAck(data.id);
    } else if (frame.type === "message.status") {
      const data = frame.data;
      for (const contactId of Object.keys(window.__satConversations)) {
        const thread = window.__satConversations[contactId];
        if (thread.some((m) => m.id === data.id)) {
          upsertMessage(contactId, {
            id: data.id,
            status: data.status,
            delivered_count: data.delivered_count,
            member_count: data.member_count,
          });
          break;
        }
      }
    } else if (frame.type === "sync.batch") {
      const data = frame.data;
      const contactId = data.target_id;
      window.__satConversations[contactId] = data.messages.map((m) => ({
        id: m.id,
        author_id: m.author_id,
        target_id: m.target_id,
        target_type: m.target_type,
        kind: m.kind,
        text: m.text,
        media_ref: m.media_ref,
        renderings: m.renderings || [],
        transcript: m.transcript || null,
        transcript_language: m.transcript_language || null,
        created_at: m.created_at,
        status: m.status,
      }));
      for (const m of data.messages) {
        if (m.author_id !== window.__satUserId && m.status === "sent") sendDeliveredAck(m.id);
        // My own message the server still calls pending: ask again when its undo window is over
        // (at once if it already is), so a message that was processing when the page closed does
        // not stay on 'Processing...' forever.
        if (m.author_id === window.__satUserId && m.status === "pending" && m.id) {
          const wait = Math.max(
            0,
            Date.parse(m.created_at) + UNDO_WINDOW_MS + 1000 - Date.now()
          );
          if (window.__satPendingTimers[m.id]) clearTimeout(window.__satPendingTimers[m.id]);
          window.__satPendingTimers[m.id] = setTimeout(
            () => pollMessageStatus(contactId, m.id, Date.now()),
            wait
          );
        }
      }
      if (window.__satCurrentContactId === contactId) renderCurrentThread();
    } else if (frame.type === "error") {
      console.log("[satsandesh-ws] error frame:", frame.data);
      // A rejected message.send (e.g. posting to an announcement circle
      // without moderator/admin rights) carries the failed send's own
      // client_msg_id in detail -- correlate it back to the specific
      // "Sending..." bubble already rendered optimistically and mark it
      // failed, rather than leaving it stuck at "Sending..." forever with
      // no explanation. Frames with no such detail (a malformed-payload
      // rejection, say, with no message ever pushed locally) have nothing
      // to correlate to and are left as console-only, same as before.
      const failedClientMsgId = frame.data.detail && frame.data.detail.client_msg_id;
      if (failedClientMsgId) {
        for (const contactId of Object.keys(window.__satConversations)) {
          const thread = window.__satConversations[contactId];
          const msg = thread.find((m) => m.client_msg_id === failedClientMsgId && !m.id);
          if (msg) {
            msg.status = "failed";
            msg.error_text = frame.data.message;
            if (window.__satCurrentContactId === contactId) renderCurrentThread();
            break;
          }
        }
      }
    }
  }

  window.__satRenderCurrentThread = renderCurrentThread;

  function connect() {
    setStatus(%(connecting)s);
    ws = new WebSocket(WS_URL + "/ws?token=" + encodeURIComponent(window.__satToken));
    window.__satWs = ws;

    ws.onopen = function () {
      setStatus("");
      backoffMs = 1000;
      reconnectAttempt = 0;
      resendUnacked();
    };

    ws.onmessage = function (event) {
      handleFrame(JSON.parse(event.data));
    };

    ws.onclose = function (event) {
      if (event.code === 1008) {
        setStatus("");
        return;
      }
      reconnectAttempt += 1;
      const jitter = Math.random() * 300;
      setStatus("Reconnecting... (" + reconnectAttempt + ")");
      setTimeout(connect, backoffMs + jitter);
      backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
    };

    ws.onerror = function () {};
  }

  connect();

  window.addEventListener("beforeunload", function () {
    if (ws) ws.close();
  });
})();
}
"""

OPEN_CHAT_JS_TEMPLATE = """
(() => {
    const targetId = %(target_id)s;
    const targetType = %(target_type)s;
    window.__satCurrentContactId = targetId;
    window.__satCurrentTargetType = targetType;
    if (!window.__satConversations) window.__satConversations = {};
    if (!window.__satConversations[targetId]) window.__satConversations[targetId] = [];
    if (window.__satRenderCurrentThread) window.__satRenderCurrentThread();
    if (window.__satWs && window.__satWs.readyState === WebSocket.OPEN) {
        window.__satWs.send(JSON.stringify({
            type: "sync.request",
            data: { target_type: targetType, target_id: targetId, limit: 200 },
        }));
    }
    return "ok";
})()
"""

CHAT_SEND_JS = """
(() => {
    const input = document.getElementById("live-chat-input");
    const text = (input.value || "").trim();
    const contactId = window.__satCurrentContactId;
    const targetType = window.__satCurrentTargetType || "user";
    if (!text || !contactId || !window.__satWs || window.__satWs.readyState !== WebSocket.OPEN) {
        return "";
    }
    const clientMsgId = window.__satUuidV4();
    // Declared only when the characters make it clear; see __satDetectLang.
    const sourceLang = window.__satDetectLang ? window.__satDetectLang(text) : null;
    if (!window.__satConversations[contactId]) window.__satConversations[contactId] = [];
    window.__satConversations[contactId].push({
        client_msg_id: clientMsgId,
        author_id: window.__satUserId,
        target_id: contactId,
        target_type: targetType,
        kind: "text",
        text: text,
        source_lang: sourceLang,
        status: "pending",
    });
    if (window.__satRenderCurrentThread) window.__satRenderCurrentThread();
    const payload = sourceLang
        ? { kind: "text", text: text, source_lang: sourceLang }
        : { kind: "text", text: text };
    window.__satSendMessage(contactId, payload, clientMsgId, targetType);
    input.value = "";
    return "sent";
})()
"""

# ---------------------------------------------------------------------------
# Client-side JS: circles -- list, create, self-service join (real endpoints,
# see services/gateway/app/circles.py). No user-directory equivalent exists
# for circles yet (same honest gap as DM contacts): joining requires knowing
# a circle's real id, shared out of band, same as sharing "Your ID" for DMs.
# ---------------------------------------------------------------------------

LOAD_CIRCLES_JS_TEMPLATE = """
(async () => {
    try {
        const resp = await fetch(%(gateway_url)s + "/circles", {
            headers: { Authorization: "Bearer " + window.__satToken },
        });
        if (!resp.ok) return "[]";
        const circles = await resp.json();
        return JSON.stringify(circles);
    } catch (err) {
        return "[]";
    }
})()
"""

CREATE_CIRCLE_JS_TEMPLATE = """
(async () => {
    try {
        const resp = await fetch(%(gateway_url)s + "/circles", {
            method: "POST",
            headers: {
                Authorization: "Bearer " + window.__satToken,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ name: %(name)s, kind: %(kind)s }),
        });
        if (!resp.ok) return "error:" + resp.status;
        return "ok";
    } catch (err) {
        return "error:network";
    }
})()
"""

JOIN_CIRCLE_JS_TEMPLATE = """
(async () => {
    const rawId = (%(circle_id)s || "").trim();
    const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!uuidRe.test(rawId)) return "error:invalid_id";
    try {
        const resp = await fetch(%(gateway_url)s + "/circles/" + rawId + "/join", {
            method: "POST",
            headers: { Authorization: "Bearer " + window.__satToken },
        });
        if (resp.status === 404) return "error:not_found";
        if (!resp.ok) return "error:" + resp.status;
        return "ok";
    } catch (err) {
        return "error:network";
    }
})()
"""

# ---------------------------------------------------------------------------
# Client-side JS for real MediaRecorder-backed voice capture
# ---------------------------------------------------------------------------

START_RECORDING_JS = """
(async () => {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const recorder = new MediaRecorder(stream);
        window.__satChunks = [];
        recorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) window.__satChunks.push(e.data);
        };
        recorder.start();
        window.__satRecorder = recorder;
        window.__satStream = stream;
        window.__satRecordingStartedAt = Date.now();
        return "started";
    } catch (err) {
        return "error:" + (err && err.message ? err.message : "unknown");
    }
})()
"""

STOP_RECORDING_JS = """
(async () => {
    const recorder = window.__satRecorder;
    if (!recorder || recorder.state === "inactive") return "";
    const done = new Promise((resolve) => {
        recorder.onstop = () => {
            const blob = new Blob(window.__satChunks || [], { type: "audio/webm" });
            // Kept as a real Blob for upload (Week 6: POST /media wants raw
            // bytes, not a data URL) alongside the data URL below, which
            // stays purely for the local <audio> preview -- two different
            // consumers, no reason to make one derive from the other.
            window.__satLastRecordingBlob = blob;
            window.__satLastRecordingDurationMs = window.__satRecordingStartedAt
                ? Date.now() - window.__satRecordingStartedAt
                : 0;
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result || "");
            reader.readAsDataURL(blob);
        };
    });
    recorder.stop();
    if (window.__satStream) {
        window.__satStream.getTracks().forEach((t) => t.stop());
    }
    return await done;
})()
"""

DISCARD_RECORDING_JS = """
(() => {
    window.__satLastRecordingBlob = null;
    window.__satLastRecordingDurationMs = 0;
})()
"""

# Week 6: upload the recorded blob (POST /media, services/gateway/app/media.py)
# then send it as a real voice message over the same WS the text path already
# uses (window.__satSendMessage, CHAT_CONNECT_JS_TEMPLATE) -- reusing that
# function rather than a parallel send path is why sendMessageFrame's
# signature changed from a bare `text` argument to a `payload` object above.
UPLOAD_AND_SEND_VOICE_JS_TEMPLATE = """
(async () => {
    const blob = window.__satLastRecordingBlob;
    if (!blob) return "error:no_recording";
    const targetId = window.__satCurrentContactId;
    const targetType = window.__satCurrentTargetType || "user";
    if (!targetId || !window.__satSendMessage) return "error:no_target";
    const durationMs = window.__satLastRecordingDurationMs || 0;

    // The progress bar (voice_upload_progress() below) is plain DOM, driven
    // from here rather than from Reflex state: XMLHttpRequest's upload
    // progress fires many times a second and a Python round-trip per event
    // would be slower than the upload itself. It may not be mounted yet on
    // the first event, hence the null checks.
    function showProgress(pct) {
        const fill = document.getElementById("sat-upload-fill");
        const bar = document.getElementById("sat-upload-bar");
        const label = document.getElementById("sat-upload-pct");
        if (fill) fill.style.width = pct + "%%";
        if (bar) bar.setAttribute("aria-valuenow", String(pct));
        if (label) label.textContent = pct + "%%";
    }

    // XMLHttpRequest, not fetch: fetch cannot report upload progress.
    function attemptUpload() {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open(
                "POST",
                %(gateway_url)s + "/media?format=webm_opus&duration_ms=" + durationMs
            );
            xhr.setRequestHeader("Authorization", "Bearer " + window.__satToken);
            xhr.timeout = 60000;
            xhr.upload.onprogress = (e) => {
                if (e.lengthComputable) showProgress(Math.round((e.loaded / e.total) * 100));
            };
            xhr.onload = () => {
                if (xhr.status < 200 || xhr.status >= 300) {
                    reject(new Error("upload failed: " + xhr.status));
                    return;
                }
                try {
                    resolve(JSON.parse(xhr.responseText));
                } catch (err) {
                    reject(new Error("upload failed: bad response"));
                }
            };
            xhr.onerror = () => reject(new Error("upload failed: network"));
            xhr.ontimeout = () => reject(new Error("upload failed: timeout"));
            xhr.send(blob);
        });
    }

    let uploaded = null;
    let lastErr = null;
    // Up to three attempts with a growing pause (1s, then 2s) -- the "weak
    // network" case this feature exists for -- before surfacing a real
    // failure the elder has to explicitly retry. The upload is idempotent
    // server-side (the same bytes recover the same media row), so a retry
    // after a response that was lost on the way back cannot store it twice.
    for (let attempt = 0; attempt < 3; attempt++) {
        try {
            showProgress(0);
            uploaded = await attemptUpload();
            break;
        } catch (err) {
            lastErr = err;
            if (attempt < 2) await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
        }
    }
    if (!uploaded) {
        return "error:" + (lastErr ? lastErr.message : "upload_failed");
    }

    const clientMsgId = window.__satUuidV4();
    const mediaRef = {
        uri: uploaded.uri,
        format: uploaded.format,
        duration_ms: uploaded.duration_ms,
    };
    // The language the person chose and spoke. Declared so the speech step does not have to
    // guess (it guessed Norwegian, Sinhala and Urdu on staging, and the note was held), and
    // so a per-language speech service can be picked. Only once they have chosen: the
    // default before that is not something they said.
    const prefsNow = window.__satPrefs || {};
    const spoken = prefsNow.locked && ["en", "hi", "te"].indexOf(prefsNow.lang) !== -1
        ? prefsNow.lang : null;
    if (!window.__satConversations[targetId]) window.__satConversations[targetId] = [];
    window.__satConversations[targetId].push({
        client_msg_id: clientMsgId,
        author_id: window.__satUserId,
        target_id: targetId,
        target_type: targetType,
        kind: "voice",
        media_ref: mediaRef,
        source_lang: spoken,
        text: null,
        status: "pending",
    });
    if (window.__satCurrentContactId === targetId && window.__satRenderCurrentThread) {
        window.__satRenderCurrentThread();
    }
    window.__satSendMessage(
        targetId,
        spoken ? { kind: "voice", media_ref: mediaRef, source_lang: spoken }
               : { kind: "voice", media_ref: mediaRef },
        clientMsgId,
        targetType
    );
    window.__satLastRecordingBlob = null;
    window.__satLastRecordingDurationMs = 0;
    return "ok";
})()
"""

# ---------------------------------------------------------------------------
# Client-side JS: quiet hours (GET/PATCH /me/settings -- Week 5's
# accessibility pass). The DB columns and the push-suppression read side
# (app/push.py's is_quiet_hours) have existed since Month 1; app/users.py
# is the write path that never did, until now.
# ---------------------------------------------------------------------------

# Receiver preferences (content language, speech on/off, autoplay) are mirrored to
# `window.__satPrefs`, which the chat JS reads when it draws a message, and to
# localStorage. The localStorage copy is what makes a choice "hold" across a reload
# today: the gateway's GET/PATCH /me/settings does not exist yet (M2's), so
# without it every reload would reset the language the elder picked. Once that
# endpoint exists the server values still win (on_settings_loaded runs after).
SYNC_PREFS_JS_TEMPLATE = """
(() => {
    const prefs = %(prefs)s;
    window.__satPrefs = prefs;
    try {
        // The language lock only ever turns on: a sync from before the saved prefs were read
        // (locked still false) must not erase a lock stored earlier.
        let wasLocked = false;
        try {
            wasLocked = !!JSON.parse(localStorage.getItem("sat_prefs") || "{}").locked;
        } catch (e) {}
        localStorage.setItem("sat_prefs", JSON.stringify({
            lang: prefs.lang, tts: prefs.tts, autoplay: prefs.autoplay,
            locked: !!prefs.locked || wasLocked,
        }));
    } catch (e) {}
    if (window.__satRenderCurrentThread) window.__satRenderCurrentThread();
})()
"""

LOAD_PREFS_JS = """
(() => {
    try {
        return localStorage.getItem("sat_prefs") || "";
    } catch (e) {
        return "";
    }
})()
"""

# The words the chat JS needs when it draws a message (it is built once, outside
# Reflex, so it gets both languages up front and picks by the current UI language).
STATUS_TEXT_KEYS = (
    "status_pending",
    "status_sent",
    "status_delivered",
    "status_cancelled",
    "status_held",
    "status_blocked",
    "status_processing",
)

RECEIVER_TEXT_KEYS = (
    "recv_show_original",
    "recv_show_english",
    "recv_show_translation",
    "recv_audio_failed",
    "recv_speed",
    "recv_transcript",
    "recv_no_audio",
    "recv_approximate",
)

LOAD_SETTINGS_JS_TEMPLATE = """
(async () => {
    try {
        const resp = await fetch(%(gateway_url)s + "/me/settings", {
            headers: { Authorization: "Bearer " + window.__satToken },
        });
        if (!resp.ok) return "";
        return JSON.stringify(await resp.json());
    } catch (err) {
        return "";
    }
})()
"""

# quiet_hours_start/end travel as "HH:MM:SS" (Pydantic's `time` JSON
# encoding) but <input type="time"> wants "HH:MM" -- and an empty input
# must PATCH as an explicit `null`, not an omitted field, so a value the
# elder clears actually clears server-side rather than leaving the old
# one in place (see contracts/chat/users.py::QuietHoursUpdate and
# app/db/repository.py::update_quiet_hours's set_start/set_end split).
#
# Week 7: also saves preferred_language (en/hi/te, contracts/ai's
# LanguageCode values -- the content-rendering language a receiver wants
# incoming voice notes translated into, a different concept from
# State.language, which only ever controlled this app's own UI chrome
# text and stays en/te-only) and tts_on. One PATCH, one Save button,
# since all three already live in the one settings card -- no reason to
# round-trip three times for three fields a person sets together.
SAVE_SETTINGS_JS_TEMPLATE = """
(async () => {
    const toWire = (hhmm) => (hhmm ? hhmm + ":00" : null);
    // Quiet hours are only enforced for a user who has a timezone (the gateway will not
    // assume UTC), so a saved window with none would be stored and never applied.
    let zone = null;
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch (e) {}
    const body = {
        quiet_hours_start: toWire(%(start)s),
        quiet_hours_end: toWire(%(end)s),
        preferred_language: %(preferred_language)s,
        tts_on: %(tts_on)s,
    };
    if (zone) body.timezone = zone;
    const send = () => fetch(%(gateway_url)s + "/me/settings", {
        method: "PATCH",
        headers: {
            Authorization: "Bearer " + window.__satToken,
            "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
    });
    try {
        let resp = await send();
        // A timezone name the gateway does not know is a 422 for the WHOLE request, which
        // would throw away the quiet hours / language / speech the person just saved too.
        // Retry once without it: the rest is worth saving even if the zone is not.
        if (resp.status === 422 && body.timezone) {
            delete body.timezone;
            resp = await send();
        }
        if (!resp.ok) return "error:" + resp.status;
        return "ok";
    } catch (err) {
        return "error:network";
    }
})()
"""

# Saves the content language or speech on/off the moment it is changed. They used to reach
# the server only when the Save button was pressed, and now that the gateway's /me/settings
# exists its values win on load -- so an elder who picked Hindi and did not press Save would
# be put back to Telugu on the next visit. Only the field that just changed is sent (never
# the other one from current state: two quick taps arriving out of order could otherwise store
# the older value), and never quiet hours (a half-typed time must not be saved). A failure is
# silent: the choice is already applied and kept in localStorage, and Save still works.
SAVE_PREFS_JS_TEMPLATE = """
(async () => {
    const body = %(fields)s;
    let zone = null;
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch (e) {}
    if (zone) body.timezone = zone;
    const send = () => fetch(%(gateway_url)s + "/me/settings", {
        method: "PATCH",
        headers: {
            Authorization: "Bearer " + window.__satToken,
            "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
    });
    try {
        let resp = await send();
        // An unknown timezone name is a 422 for the whole request and would lose the change
        // being saved; retry once without it.
        if (resp.status === 422 && body.timezone) {
            delete body.timezone;
            resp = await send();
        }
        return resp.ok ? "ok" : "error:" + resp.status;
    } catch (err) {
        return "error:network";
    }
})()
"""

# ---------------------------------------------------------------------------
# Client-side JS: audio labels (tap-and-hold to hear a button read aloud --
# GET /audio-labels/{label}, services/gateway/app/audio_labels.py, built
# Week 4 but never wired into any button until this Week 5 pass). The
# hold timer lives entirely in JS, same reasoning as
# START_RECORDING_JS/STOP_RECORDING_JS above: a Python round-trip per
# press/release would be slower and no more reliable than a plain
# setTimeout, and nothing here needs to touch Reflex state at all.
#
# Second round of review (PR #45): an earlier version of this removed
# on_click entirely and routed the real action through on_mouse_up
# instead, gated on whether the hold fired. That broke keyboard
# activation -- pressing Enter/Space on a focused <button> fires a native
# click with no mousedown/mouseup at all, so a button with no on_click
# does nothing for a keyboard user. This version keeps on_click as the
# one real action for both mouse and keyboard, and instead suppresses
# *only* the specific click that follows a completed mouse hold, at the
# DOM level, before Reflex's own click handler ever sees it -- a
# keyboard-triggered click never touches mousedown, so it's never a
# candidate for suppression in the first place.
# ---------------------------------------------------------------------------

_AUDIO_LABEL_HOLD_MS = 450

AUDIO_LABEL_HOLD_START_JS_TEMPLATE = """
(() => {
    // Installed once, lazily, on the first hold anywhere on the page --
    // a single document-level capture-phase listener, not one per
    // button, since only one hold can be in progress at a time anyway
    // (the timer/flag below are already page-global for the same
    // reason). Capture phase means this runs before the click ever
    // reaches the button's own on_click handler.
    if (!window.__satAudioHoldGuardInstalled) {
        window.__satAudioHoldGuardInstalled = true;
        document.addEventListener("click", (e) => {
            if (window.__satAudioHoldJustFired) {
                window.__satAudioHoldJustFired = false;
                e.preventDefault();
                e.stopImmediatePropagation();
            }
        }, true);
    }
    if (window.__satAudioHoldTimer) clearTimeout(window.__satAudioHoldTimer);
    window.__satAudioHoldTimer = setTimeout(async () => {
        window.__satAudioHoldTimer = null;
        // Set synchronously, the instant the hold threshold is reached --
        // this is what the click-guard above checks, and it must be true
        // before the eventual mouseup's click can arrive, not after.
        window.__satAudioHoldJustFired = true;
        try {
            const resp = await fetch(
                %(gateway_url)s + "/audio-labels/" + %(label)s + "?lang=" + %(lang)s
            );
            if (!resp.ok) return;
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            // Release the blob URL once playback actually finishes, or
            // immediately if it never started -- URL.createObjectURL
            // otherwise leaks for the page's lifetime (flagged in review,
            // PR #45).
            audio.addEventListener("ended", () => URL.revokeObjectURL(url));
            audio.play().catch(() => URL.revokeObjectURL(url));
        } catch (err) {
            // Best-effort accessibility aid -- a failed fetch/playback here
            // must never surface as a visible error mid-tap.
        }
    }, %(hold_ms)s);
})()
"""

# Cancels a still-pending hold (released or left before the threshold),
# and always clears the "just fired" flag too, not only the timer --
# without that second part, holding past the threshold and then dragging
# off the button (mouseleave, no click ever arrives to consume the flag)
# would leave it set for whatever the *next*, unrelated click on the page
# happens to be.
AUDIO_LABEL_HOLD_CANCEL_JS = """
(() => {
    if (window.__satAudioHoldTimer) {
        clearTimeout(window.__satAudioHoldTimer);
        window.__satAudioHoldTimer = null;
    }
    window.__satAudioHoldJustFired = false;
})()
"""

# Reflex 0.9 has no touch or pointer event triggers, and a phone or tablet
# fires `mousedown` only AFTER a tap is released -- so on a touch screen the
# mic button (hold-to-record) recorded for zero seconds and the audio-label
# buttons (tap-and-hold) never saw a hold. This translates touch into the mouse
# events those buttons already handle. `data-sat-hold="press"` (the mic) takes
# over the gesture completely (no scroll, no emulated mouse events, no
# long-press menu); `"label"` leaves the browser's own tap/click alone and
# only adds the press-and-release the hold timer needs.
TOUCH_HOLD_SHIM_JS = """
(() => {
    if (window.__satTouchHoldInstalled) return;
    window.__satTouchHoldInstalled = true;
    const holdTarget = (node) =>
        node && node.closest ? node.closest("[data-sat-hold]") : null;
    const fire = (el, type) =>
        el.dispatchEvent(
            new MouseEvent(type, { bubbles: true, cancelable: true, view: window })
        );
    let active = null;
    document.addEventListener("touchstart", (e) => {
        const el = holdTarget(e.target);
        if (!el || e.touches.length !== 1) return;
        active = el;
        if (el.dataset.satHold === "press") e.preventDefault();
        fire(el, "mousedown");
    }, { passive: false });
    const release = (e) => {
        if (!active) return;
        const el = active;
        active = null;
        if (el.dataset.satHold === "press") e.preventDefault();
        fire(el, "mouseup");
    };
    document.addEventListener("touchend", release, { passive: false });
    document.addEventListener("touchcancel", release, { passive: false });
    document.addEventListener("contextmenu", (e) => {
        if (holdTarget(e.target)) e.preventDefault();
    });
})()
"""


class State(rx.State):
    language: str = "en"
    current_contact_id: str = ""
    active_tab: str = "people"
    contacts: list[dict[str, str]] = []

    # Real circles (services/gateway/'s app/circles.py) -- create, list,
    # self-join (POST /circles/{id}/join, PR #32). Loaded once identity is
    # ready, same timing as contacts, since GET /circles needs a real
    # Bearer token but not the "joined" (named) state.
    circles: list[dict[str, str]] = []
    current_circle_id: str = ""
    create_circle_open: bool = False
    create_circle_name_input: str = ""
    # contracts/chat/circles.py::CircleKind -- "group" (default, any member
    # posts) vs "announcement" (moderator/admin-only posting). A checkbox
    # in create_circle_form, not a separate flow: creating either kind is
    # the same POST /circles call, just a different kind value.
    create_circle_is_announcement: bool = False
    join_circle_open: bool = False
    join_circle_id_input: str = ""
    circle_error: str = ""

    # Real gateway identity: a client-generated UUID persisted in
    # localStorage (ENSURE_IDENTITY_JS), not a server-issued session --
    # services/gateway's auth stub takes any UUID-shaped token as that
    # user's real, permanent id. The typed display name never leaves the
    # device (see module docstring's "Identity" section).
    my_user_id: str = ""
    display_name_input: str = ""
    joined: bool = False
    my_display_name: str = ""

    add_contact_open: bool = False
    add_name_input: str = ""
    add_id_input: str = ""
    add_error: str = ""
    copied_id: bool = False
    copied_circle_id: bool = False
    copy_id_failed: bool = False
    copy_circle_id_failed: bool = False

    mic_recording: bool = False
    mic_permission_denied: bool = False
    last_recording_data_url: str = ""

    # Voice send (Week 6): "" (idle/preview), "uploading", or "failed".
    # Never "sent" -- once the upload+send call returns "ok", the recording
    # banner closes (last_recording_data_url reset) and the message itself
    # is tracked the same way a text message is, by the existing WS
    # pending/sent/delivered machinery, not by this flag.
    voice_send_status: str = ""

    # Settings (GET/PATCH /me/settings). Quiet hours: Week 5 accessibility
    # pass. "HH:MM" strings matching <input type="time">'s own value
    # format, not the wire's "HH:MM:SS" -- SAVE_SETTINGS_JS_TEMPLATE does
    # that conversion at the JS boundary, same split as every other
    # client-shape-vs-wire-shape field in this file.
    quiet_hours_start_input: str = ""
    quiet_hours_end_input: str = ""

    # Week 7: preferred_language is the content-rendering language (what
    # an incoming voice note gets translated into) -- deliberately not
    # the same field as `language` above (this app's own UI chrome,
    # en/te only). Defaults to whatever `language` currently is at
    # onboarding time (see join_circle) so picking a UI language once
    # sets a sensible starting content preference too, without the two
    # ever being forced to stay in lockstep afterward -- a receiver can
    # read the app in Telugu and still ask for English audio.
    preferred_language_input: str = "en"
    # The one-time language choice. `lang_locked` turns on once, when the person confirms
    # (and is kept in localStorage with the other prefs); nothing in the app turns it off, so the
    # language is fixed for now. A settings control to change it is a later step.
    # `prefs_loaded` stops the choice screen flashing for someone who already chose, before
    # their saved prefs have been read. `pending_language` is a tapped-but-not-yet-confirmed
    # choice: a wrong tap on a fixed setting would otherwise be permanent.
    lang_locked: bool = False
    prefs_loaded: bool = False
    pending_language: str = ""
    tts_on_input: bool = True
    # Whether a newly arrived voice message plays by itself. Off by default: sound
    # nobody asked for is a worse surprise than a tap -- the elder opts in.
    autoplay_input: bool = False

    settings_saved: bool = False
    settings_error: bool = False

    @rx.var
    def t(self) -> dict[str, str]:
        return TEXTS[self.language]

    @rx.var
    def chosen_language_name(self) -> str:
        return LANGUAGE_NATIVE.get(self.preferred_language_input, "")

    @rx.var
    def pending_language_name(self) -> str:
        return LANGUAGE_NATIVE.get(self.pending_language, "")

    @rx.var
    def current_contact(self) -> dict[str, str]:
        for c in self.contacts:
            if c["id"] == self.current_contact_id:
                return c
        return {}

    @rx.var
    def current_circle(self) -> dict[str, str]:
        for c in self.circles:
            if c["id"] == self.current_circle_id:
                return c
        return {}

    @rx.var
    def chat_title(self) -> str:
        # One chat screen serves both DMs and circles (current_contact_id
        # and current_circle_id are mutually exclusive) -- the header
        # just needs to know which kind is actually open.
        if self.current_circle_id:
            for c in self.circles:
                if c["id"] == self.current_circle_id:
                    return c["name"]
            return ""
        for c in self.contacts:
            if c["id"] == self.current_contact_id:
                return c["name"]
        return ""

    def open_chat(self, contact_id: str):
        self.current_contact_id = contact_id

    def open_circle_chat(self, circle_id: str):
        self.current_circle_id = circle_id

    def go_home(self):
        self.current_contact_id = ""
        self.current_circle_id = ""

    def toggle_language(self):
        self.language = "te" if self.language == "en" else "en"
        return self._sync_prefs()

    def _sync_prefs(self):
        """Push the receiver preferences to the chat JS and localStorage."""
        prefs = {
            "lang": self.preferred_language_input,
            "tts": self.tts_on_input,
            "autoplay": self.autoplay_input,
            "ui": self.language,
            "locked": self.lang_locked,
        }
        return rx.call_script(SYNC_PREFS_JS_TEMPLATE % {"prefs": json.dumps(prefs)})

    def on_prefs_loaded(self, result: str):
        try:
            saved = json.loads(result) if result else {}
        except (json.JSONDecodeError, TypeError):
            saved = {}
        if saved.get("lang") in ("en", "hi", "te"):
            self.preferred_language_input = saved["lang"]
        if isinstance(saved.get("tts"), bool):
            self.tts_on_input = saved["tts"]
        if isinstance(saved.get("autoplay"), bool):
            self.autoplay_input = saved["autoplay"]
        if saved.get("locked") is True and saved.get("lang") in LANGUAGE_CODES:
            self.lang_locked = True
        self.prefs_loaded = True
        return self._sync_prefs()

    def set_active_tab(self, tab: str):
        self.active_tab = tab

    # -- Bootstrap: identity, saved name, saved contacts, then (if already
    # joined before) the persistent WS connection -- all on the app's own
    # on_mount, once per load, not per screen. --------------------------

    def bootstrap(self):
        return [
            rx.call_script(TOUCH_HOLD_SHIM_JS),
            rx.call_script(ENSURE_IDENTITY_JS, callback=State.on_identity_ready),
        ]

    def on_identity_ready(self, user_id: str):
        self.my_user_id = user_id
        return [
            rx.call_script(LOAD_NAME_JS, callback=State.on_name_loaded),
            rx.call_script(LOAD_CONTACTS_JS, callback=State.on_contacts_loaded),
            self.load_circles(),
            rx.call_script(LOAD_PREFS_JS, callback=State.on_prefs_loaded),
            self.load_settings(),
        ]

    def on_name_loaded(self, name: str):
        if not name:
            return None
        self.my_display_name = name
        self.joined = True
        return self.connect_chat()

    def on_contacts_loaded(self, contacts_json: str):
        try:
            parsed = json.loads(contacts_json)
        except (json.JSONDecodeError, TypeError):
            parsed = []
        self.contacts = parsed

    def connect_chat(self):
        js = CHAT_CONNECT_JS_TEMPLATE % {
            "gateway_url": json.dumps(GATEWAY_PUBLIC_URL),
            "undo_window_seconds": UNDO_WINDOW_SECONDS,
            "connecting": json.dumps(TEXTS["en"]["connecting"]),
            "no_messages": json.dumps(TEXTS["en"]["no_messages"]),
            "status_text": json.dumps(
                {lang: {key: TEXTS[lang][key] for key in STATUS_TEXT_KEYS} for lang in ("en", "te")}
            ),
            "status_pending": json.dumps(TEXTS["en"]["status_pending"]),
            "status_sent": json.dumps(TEXTS["en"]["status_sent"]),
            "status_delivered": json.dumps(TEXTS["en"]["status_delivered"]),
            "status_cancelled": json.dumps(TEXTS["en"]["status_cancelled"]),
            "send_error_not_member": json.dumps(TEXTS["en"]["send_error_not_member"]),
            "send_error_announcement_only": json.dumps(TEXTS["en"]["send_error_announcement_only"]),
            "recv_text": json.dumps(
                {
                    lang: {key: TEXTS[lang][key] for key in RECEIVER_TEXT_KEYS}
                    for lang in ("en", "te")
                }
            ),
            "uuid_v4_fallback": _UUID_V4_JS_FALLBACK,
        }
        return rx.call_script(js)

    def set_display_name_input(self, value: str):
        self.display_name_input = value

    def join_circle(self):
        name = self.display_name_input.strip()
        if not name:
            return None
        self.my_display_name = name
        self.joined = True
        return [
            rx.call_script(SAVE_NAME_JS_TEMPLATE % {"name": json.dumps(name)}),
            self.connect_chat(),
            # Persists the language picked on this same screen -- fails
            # soft like every other /me/settings call until M2 ships the
            # endpoint (see save_settings's own docstring note).
            self.save_settings(),
        ]

    def enter_chat(self):
        if self.current_circle_id:
            js = OPEN_CHAT_JS_TEMPLATE % {
                "target_id": json.dumps(self.current_circle_id),
                "target_type": json.dumps("circle"),
            }
            return rx.call_script(js)
        if not self.current_contact_id:
            return None
        js = OPEN_CHAT_JS_TEMPLATE % {
            "target_id": json.dumps(self.current_contact_id),
            "target_type": json.dumps("user"),
        }
        return rx.call_script(js)

    def send_live_message(self):
        return rx.call_script(CHAT_SEND_JS)

    def handle_chat_key_down(self, key: str):
        if key == "Enter":
            return rx.call_script(CHAT_SEND_JS)
        return None

    # -- Add a real contact (locally stored id/name pair) ----------------

    def open_add_contact(self):
        self.add_contact_open = True
        self.add_name_input = ""
        self.add_id_input = ""
        self.add_error = ""

    def close_add_contact(self):
        self.add_contact_open = False

    def set_add_name_input(self, value: str):
        self.add_name_input = value

    def set_add_id_input(self, value: str):
        self.add_id_input = value

    def submit_add_contact(self):
        self.add_error = ""
        js = ADD_CONTACT_JS_TEMPLATE % {
            "name": json.dumps(self.add_name_input.strip()),
            "target_id": json.dumps(self.add_id_input.strip()),
            "tints": json.dumps(TINTS),
        }
        return rx.call_script(js, callback=State.on_contact_added)

    def on_contact_added(self, result: str):
        if result == "error:invalid_id":
            self.add_error = self.t["add_error_invalid_id"]
        elif result == "error:self":
            self.add_error = self.t["add_error_self"]
        elif result == "error:duplicate":
            self.add_error = self.t["add_error_duplicate"]
        else:
            try:
                self.contacts = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
            self.add_contact_open = False

    # -- Circles: list, create, self-service join (real endpoints) -------

    def load_circles(self):
        js = LOAD_CIRCLES_JS_TEMPLATE % {"gateway_url": json.dumps(GATEWAY_PUBLIC_URL)}
        return rx.call_script(js, callback=State.on_circles_loaded)

    def on_circles_loaded(self, circles_json: str):
        try:
            parsed = json.loads(circles_json)
        except (json.JSONDecodeError, TypeError):
            parsed = []
        # GET /circles returns only id/name/created_by/created_at -- the
        # avatar initial and tint are purely a client-side display detail
        # (same as ADD_CONTACT_JS_TEMPLATE computes for DM contacts),
        # computed here once rather than re-derived in every render.
        for i, circle in enumerate(parsed):
            name = circle.get("name", "")
            circle["initial"] = name[:1].upper() if name else "?"
            circle["tint"] = TINTS[i % len(TINTS)]
        self.circles = parsed

    def open_create_circle(self):
        self.create_circle_open = True
        self.join_circle_open = False
        self.create_circle_name_input = ""
        self.create_circle_is_announcement = False
        self.circle_error = ""

    def close_create_circle(self):
        self.create_circle_open = False

    def set_create_circle_name_input(self, value: str):
        self.create_circle_name_input = value

    def set_create_circle_is_announcement(self, value: bool):
        self.create_circle_is_announcement = value

    def submit_create_circle(self):
        name = self.create_circle_name_input.strip()
        if not name:
            return None
        self.circle_error = ""
        kind = "announcement" if self.create_circle_is_announcement else "group"
        js = CREATE_CIRCLE_JS_TEMPLATE % {
            "gateway_url": json.dumps(GATEWAY_PUBLIC_URL),
            "name": json.dumps(name),
            "kind": json.dumps(kind),
        }
        return rx.call_script(js, callback=State.on_circle_created)

    def on_circle_created(self, result: str):
        if result.startswith("error:"):
            self.circle_error = self.t["circle_error_generic"]
            return None
        self.create_circle_open = False
        return self.load_circles()

    def open_join_circle(self):
        self.join_circle_open = True
        self.create_circle_open = False
        self.join_circle_id_input = ""
        self.circle_error = ""

    def close_join_circle(self):
        self.join_circle_open = False

    def set_join_circle_id_input(self, value: str):
        self.join_circle_id_input = value

    def submit_join_circle(self):
        self.circle_error = ""
        js = JOIN_CIRCLE_JS_TEMPLATE % {
            "gateway_url": json.dumps(GATEWAY_PUBLIC_URL),
            "circle_id": json.dumps(self.join_circle_id_input.strip()),
        }
        return rx.call_script(js, callback=State.on_circle_join_result)

    def on_circle_join_result(self, result: str):
        if result == "error:invalid_id":
            self.circle_error = self.t["circle_error_invalid_id"]
        elif result == "error:not_found":
            self.circle_error = self.t["circle_error_not_found"]
        elif result.startswith("error:"):
            self.circle_error = self.t["circle_error_generic"]
        else:
            self.join_circle_open = False
            # Join only returns a Membership, no circle name -- refresh
            # the full list so the newly-joined circle's real name shows
            # up, rather than round-tripping a second lookup for just it.
            return self.load_circles()
        return None

    def copy_my_id(self):
        self.copied_id = False
        self.copy_id_failed = False
        js = COPY_ID_JS_TEMPLATE % {"my_id": json.dumps(self.my_user_id)}
        return rx.call_script(js, callback=State.on_id_copied)

    def on_id_copied(self, result: str):
        self.copied_id = result == "ok"
        self.copy_id_failed = not self.copied_id

    def copy_circle_id(self):
        self.copied_circle_id = False
        self.copy_circle_id_failed = False
        js = COPY_ID_JS_TEMPLATE % {"my_id": json.dumps(self.current_circle_id)}
        return rx.call_script(js, callback=State.on_circle_id_copied)

    def on_circle_id_copied(self, result: str):
        self.copied_circle_id = result == "ok"
        self.copy_circle_id_failed = not self.copied_circle_id

    def start_recording(self):
        self.mic_permission_denied = False
        self.last_recording_data_url = ""
        return rx.call_script(START_RECORDING_JS, callback=State.on_recording_started)

    def on_recording_started(self, result: str):
        if result.startswith("error:"):
            self.mic_permission_denied = True
            self.mic_recording = False
        else:
            self.mic_recording = True

    def stop_recording(self):
        if not self.mic_recording:
            return
        return rx.call_script(STOP_RECORDING_JS, callback=State.on_recording_stopped)

    def on_recording_stopped(self, data_url: str):
        self.mic_recording = False
        if data_url:
            self.last_recording_data_url = data_url

    def discard_recording(self):
        self.last_recording_data_url = ""
        self.voice_send_status = ""
        return rx.call_script(DISCARD_RECORDING_JS)

    def send_voice_recording(self):
        self.voice_send_status = "uploading"
        js = UPLOAD_AND_SEND_VOICE_JS_TEMPLATE % {"gateway_url": json.dumps(GATEWAY_PUBLIC_URL)}
        return rx.call_script(js, callback=State.on_voice_send_result)

    def on_voice_send_result(self, result: str):
        if result == "ok":
            self.voice_send_status = ""
            self.last_recording_data_url = ""
        else:
            self.voice_send_status = "failed"

    # -- Settings: quiet hours, preferred content language, TTS on/off ----
    # (GET/PATCH /me/settings) -- NOTE: /me/settings doesn't exist on
    # services/gateway/ yet, still tracked as M2's (Veerendra's) side to
    # build, flagged first in Week 5's PR #45. This client side is written
    # against the contract shape now so nothing here needs to change once
    # he ships it; until then LOAD fails soft (fields stay at their
    # defaults) and SAVE reports the error rather than pretending it
    # worked.

    def load_settings(self):
        js = LOAD_SETTINGS_JS_TEMPLATE % {"gateway_url": json.dumps(GATEWAY_PUBLIC_URL)}
        return rx.call_script(js, callback=State.on_settings_loaded)

    def on_settings_loaded(self, result: str):
        resave = None
        if not result:
            return
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return
        # Wire shape is "HH:MM:SS" (or null); <input type="time"> wants
        # "HH:MM" -- slice rather than round-trip through a datetime parse
        # for a format this fixed.
        start = parsed.get("quiet_hours_start")
        end = parsed.get("quiet_hours_end")
        self.quiet_hours_start_input = start[:5] if start else ""
        self.quiet_hours_end_input = end[:5] if end else ""
        # Only overwrite these if the server actually sent them -- an
        # older/not-yet-updated /me/settings response (or the endpoint
        # still not existing at all, per the note above) must leave
        # today's join-time default standing, not silently reset it.
        if "preferred_language" in parsed and parsed["preferred_language"]:
            if self.lang_locked:
                # Fixed for this person: the server must not move it. If it holds another
                # value (an earlier save never arrived), send the chosen one again.
                if parsed["preferred_language"] != self.preferred_language_input:
                    resave = self._save_prefs("preferred_language")
            else:
                self.preferred_language_input = parsed["preferred_language"]
        if "tts_on" in parsed and parsed["tts_on"] is not None:
            self.tts_on_input = parsed["tts_on"]
        return [self._sync_prefs(), resave] if resave is not None else self._sync_prefs()

    def set_quiet_hours_start_input(self, value: str):
        self.quiet_hours_start_input = value
        self.settings_saved = False

    def set_quiet_hours_end_input(self, value: str):
        self.quiet_hours_end_input = value
        self.settings_saved = False

    def set_preferred_language_input(self, value: str):
        self.preferred_language_input = value
        self.settings_saved = False
        return [self._sync_prefs(), self._save_prefs("preferred_language")]

    # -- The one-time language choice: tap, confirm, fixed. -----------------------------

    def pick_language(self, code: str):
        if self.lang_locked or code not in LANGUAGE_CODES:
            return
        self.pending_language = code

    def cancel_pick_language(self):
        self.pending_language = ""

    def confirm_language(self):
        code = self.pending_language
        if self.lang_locked or code not in LANGUAGE_CODES:
            return None
        self.preferred_language_input = code
        self.lang_locked = True
        self.pending_language = ""
        self.settings_saved = False
        # Someone who chose Telugu should not have to read the app in English to carry on.
        if code == "te":
            self.language = "te"
        return [self._sync_prefs(), self._save_prefs("preferred_language")]

    def set_tts_on_input(self, value: bool):
        self.tts_on_input = value
        self.settings_saved = False
        return [self._sync_prefs(), self._save_prefs("tts_on")]

    def _save_prefs(self, field: str):
        """Save ONE changed setting (`preferred_language` or `tts_on`) to the gateway now."""
        fields = {"preferred_language": self.preferred_language_input, "tts_on": self.tts_on_input}
        js = SAVE_PREFS_JS_TEMPLATE % {
            "gateway_url": json.dumps(GATEWAY_PUBLIC_URL),
            "fields": json.dumps({field: fields[field]}),
        }
        return rx.call_script(js)

    def set_autoplay_input(self, value: bool):
        self.autoplay_input = value
        return self._sync_prefs()

    def save_settings(self):
        self.settings_saved = False
        self.settings_error = False
        js = SAVE_SETTINGS_JS_TEMPLATE % {
            "gateway_url": json.dumps(GATEWAY_PUBLIC_URL),
            "start": json.dumps(self.quiet_hours_start_input),
            "end": json.dumps(self.quiet_hours_end_input),
            "preferred_language": json.dumps(self.preferred_language_input),
            "tts_on": json.dumps(self.tts_on_input),
        }
        return rx.call_script(js, callback=State.on_settings_saved)

    def on_settings_saved(self, result: str):
        if result == "ok":
            self.settings_saved = True
        else:
            self.settings_error = True

    # -- Audio labels: tap-and-hold to hear a button read aloud -----------
    # (GET /audio-labels/{label}, services/gateway/app/audio_labels.py --
    # built Week 4, never wired to a button until this Week 5 pass.)

    def start_audio_label_hold(self, label: str):
        js = AUDIO_LABEL_HOLD_START_JS_TEMPLATE % {
            "gateway_url": json.dumps(GATEWAY_PUBLIC_URL),
            "label": json.dumps(label),
            "lang": json.dumps(self.language),
            "hold_ms": _AUDIO_LABEL_HOLD_MS,
        }
        return rx.call_script(js)

    def cancel_audio_label_hold(self):
        return rx.call_script(AUDIO_LABEL_HOLD_CANCEL_JS)


# ---------------------------------------------------------------------------
# Shared style helpers
# ---------------------------------------------------------------------------


def hold_to_hear(label: str) -> dict:
    """Tap-and-hold-to-hear-it-read-aloud event props, shared by all five
    buttons that carry this affordance. Deliberately does NOT touch
    on_click -- that stays each button's own, unchanged, real action, for
    both mouse and keyboard use. Double-firing on a genuine mouse hold is
    prevented at the DOM level instead (see
    AUDIO_LABEL_HOLD_START_JS_TEMPLATE's click-guard), specifically so
    keyboard activation (Enter/Space on a focused button, which never
    touches mousedown at all) keeps working -- an earlier version routed
    the real action through on_mouse_up instead and broke exactly that.
    Replaces a 3-line on_mouse_down/up/leave block that was hand-copied
    across all five buttons -- flagged in review, PR #45."""
    return {
        "on_mouse_down": lambda: State.start_audio_label_hold(label),
        "on_mouse_up": State.cancel_audio_label_hold,
        "on_mouse_leave": State.cancel_audio_label_hold,
        "custom_attrs": {"data-sat-hold": "label"},
    }


def pill_button_style(active: bool) -> dict:
    if active:
        return {
            "background": COLOR["deep_green"],
            "color": COLOR["card_cream"],
            "border": f"2px solid {COLOR['deep_green']}",
        }
    return {
        "background": COLOR["green_tint"],
        "color": COLOR["green_ink"],
        "border": f"2px solid {COLOR['deep_green']}",
    }


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


def app_header() -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.hstack(
                rx.box(
                    rx.box(
                        style={
                            "width": "20px",
                            "height": "20px",
                            "border_radius": "50%",
                            "background": COLOR["halo_ring"],
                        }
                    ),
                    style={
                        "width": "52px",
                        "height": "52px",
                        "border_radius": "16px",
                        "background": COLOR["saffron"],
                        "display": "flex",
                        "align_items": "center",
                        "justify_content": "center",
                        "flex_shrink": "0",
                    },
                ),
                rx.vstack(
                    rx.text(
                        State.t["app_name"],
                        style={
                            "font_family": FONT_SERIF,
                            "font_weight": "600",
                            "font_size": "1.4rem",
                            "line_height": "1.15",
                            "color": COLOR["ink"],
                        },
                    ),
                    rx.text(
                        State.t["tagline"],
                        style={
                            "font_family": FONT_LATIN,
                            "font_weight": "600",
                            "font_size": "0.8rem",
                            "line_height": "1.25",
                            "color": COLOR["muted_ink"],
                        },
                    ),
                    spacing="0",
                    align_items="flex-start",
                ),
                spacing="3",
                align="center",
            ),
            language_toggle(),
            width="100%",
            align="center",
            justify="between",
        ),
        style={
            "padding": "20px 20px 16px",
            "background": COLOR["card_cream"],
            "border_bottom": f"1px solid {COLOR['warm_border']}",
        },
    )


def language_toggle() -> rx.Component:
    return rx.button(
        rx.hstack(
            rx.box(
                "A⇄",
                style={
                    "width": "36px",
                    "height": "36px",
                    "border_radius": "50%",
                    "background": COLOR["deep_green"],
                    "color": COLOR["card_cream"],
                    "display": "flex",
                    "align_items": "center",
                    "justify_content": "center",
                    "font_weight": "700",
                    "font_size": "15px",
                    "flex_shrink": "0",
                },
            ),
            rx.text(
                State.t["lang_switch_label"], style={"font_size": "1rem", "font_weight": "700"}
            ),
            spacing="2",
            align="center",
        ),
        on_click=State.toggle_language,
        aria_label=State.t["lang_aria"],
        style={
            "min_height": "88px",
            "padding": "14px 20px",
            "border_radius": "22px",
            "font_family": FONT_LATIN,
            "cursor": "pointer",
            **pill_button_style(False),
        },
    )


def thought_card() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.text(
                State.t["thought_label"],
                style={
                    "font_weight": "800",
                    "font_size": "0.8rem",
                    "letter_spacing": ".08em",
                    "text_transform": "uppercase",
                    "color": "#8A5B12",
                },
            ),
            rx.text(
                State.t["thought_text"],
                style={
                    "font_family": FONT_LATIN,
                    "font_weight": "600",
                    "font_size": "1.05rem",
                    "line_height": "1.45",
                    "color": COLOR["ink"],
                },
            ),
            spacing="1",
            align_items="flex-start",
            flex="1",
        ),
        rx.button(
            rx.box(
                style={
                    "width": "0",
                    "height": "0",
                    "border_left": "22px solid #8A5B12",
                    "border_top": "14px solid transparent",
                    "border_bottom": "14px solid transparent",
                    "margin_left": "6px",
                }
            ),
            aria_label=State.t["listen_aria"],
            style={
                "flex_shrink": "0",
                "width": "88px",
                "height": "88px",
                "border_radius": "50%",
                "border": f"2px solid {COLOR['gold_border']}",
                "background": COLOR["card_cream"],
                "display": "flex",
                "align_items": "center",
                "justify_content": "center",
                "cursor": "pointer",
            },
        ),
        style={
            "margin": "20px 20px 0",
            "padding": "20px",
            "background": COLOR["gold_sand"],
            "border": "1px solid #EFD9A8",
            "border_radius": "24px",
        },
        align="center",
        spacing="4",
    )


def your_id_card() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.text(
                State.t["your_id_label"],
                style={
                    "font_family": FONT_LATIN,
                    "font_weight": "600",
                    "font_size": "0.9rem",
                    "color": COLOR["green_ink"],
                },
            ),
            rx.text(
                State.my_user_id,
                style={
                    "font_family": FONT_MONO,
                    "font_size": "0.85rem",
                    "color": COLOR["muted_ink"],
                    "word_break": "break-all",
                    "user_select": "all",
                },
            ),
            spacing="1",
            align_items="flex-start",
            flex="1",
            min_width="0",
        ),
        rx.button(
            rx.cond(
                State.copied_id,
                State.t["copied_id"],
                rx.cond(State.copy_id_failed, State.t["copy_failed"], State.t["copy_id"]),
            ),
            on_click=State.copy_my_id,
            style={
                "flex_shrink": "0",
                "min_height": "56px",
                "padding": "0 18px",
                "border_radius": "14px",
                "font_weight": "700",
                "cursor": "pointer",
                **pill_button_style(True),
            },
        ),
        style={
            "margin": "16px 20px 0",
            "padding": "16px 18px",
            "background": COLOR["green_tint"],
            "border_radius": "18px",
        },
        align="center",
        spacing="3",
    )


def language_pick_button(code: str) -> rx.Component:
    """One big button on the language choice screen, named in its own script."""
    return rx.button(
        LANGUAGE_NATIVE[code],
        on_click=lambda: State.pick_language(code),
        style={
            "width": "100%",
            "min_height": "76px",
            "border_radius": "16px",
            "font_size": "1.6rem",
            "font_weight": "700",
            "cursor": "pointer",
            **pill_button_style(False),
        },
    )


def language_screen() -> rx.Component:
    """Shown once, to anyone who has not yet chosen. The choice is fixed afterwards (for now),
    so a tap only selects; a second, clearly worded step confirms it."""
    return rx.center(
        rx.vstack(
            rx.text(
                State.t["app_name"],
                style={
                    "font_family": FONT_SERIF,
                    "font_weight": "600",
                    "font_size": "2rem",
                    "color": COLOR["ink"],
                },
            ),
            rx.cond(
                State.pending_language == "",
                rx.vstack(
                    rx.text(
                        State.t["choose_language_title"],
                        style={
                            "font_family": FONT_LATIN,
                            "font_weight": "700",
                            "font_size": "1.4rem",
                            "color": COLOR["green_ink"],
                            "text_align": "center",
                        },
                    ),
                    rx.text(
                        State.t["choose_language_hint"],
                        style={
                            "font_size": "1.05rem",
                            "color": COLOR["muted_ink"],
                            "text_align": "center",
                        },
                    ),
                    language_pick_button("te"),
                    language_pick_button("hi"),
                    language_pick_button("en"),
                    spacing="4",
                    width="100%",
                ),
                rx.vstack(
                    rx.text(
                        State.t["confirm_language_title"],
                        style={
                            "font_family": FONT_LATIN,
                            "font_weight": "700",
                            "font_size": "1.4rem",
                            "color": COLOR["green_ink"],
                            "text_align": "center",
                        },
                    ),
                    rx.text(
                        State.pending_language_name,
                        style={
                            "font_size": "2.2rem",
                            "font_weight": "700",
                            "color": COLOR["ink"],
                            "text_align": "center",
                        },
                    ),
                    rx.text(
                        State.t["confirm_language_hint"],
                        style={
                            "font_size": "1.05rem",
                            "color": COLOR["muted_ink"],
                            "text_align": "center",
                        },
                    ),
                    rx.button(
                        State.t["confirm_language_yes"],
                        on_click=State.confirm_language,
                        style={
                            "width": "100%",
                            "min_height": "76px",
                            "border_radius": "16px",
                            "font_size": "1.4rem",
                            "font_weight": "700",
                            "cursor": "pointer",
                            "background": COLOR["deep_green"],
                            "color": COLOR["card_cream"],
                            "border": "none",
                        },
                    ),
                    rx.button(
                        State.t["confirm_language_back"],
                        on_click=State.cancel_pick_language,
                        style={
                            "width": "100%",
                            "min_height": "64px",
                            "border_radius": "16px",
                            "font_size": "1.2rem",
                            "font_weight": "700",
                            "cursor": "pointer",
                            **pill_button_style(False),
                        },
                    ),
                    spacing="4",
                    width="100%",
                ),
            ),
            spacing="5",
            width="100%",
            align="center",
            style={"max_width": "420px", "padding": "24px"},
        ),
        style={"min_height": "100vh", "background": COLOR["cream_canvas"]},
    )


def settings_card() -> rx.Component:
    """Week 5 accessibility pass (quiet hours) plus Week 7 (content
    language, TTS on/off) -- all three live in one card against the same
    GET/PATCH /me/settings, one Save button. The DB columns and
    push-suppression read side for quiet hours have existed since Month 1
    (app/db/models.py, app/push.py's is_quiet_hours) but nothing let an
    elder actually set any of this until Week 5 built the write path.
    The Save button also doubles as the "settings" tap-and-hold-to-hear
    anchor (services/gateway/app/audio_labels.py's catalog has no
    dedicated settings screen to attach to yet)."""
    return rx.vstack(
        rx.text(
            State.t["your_language_title"],
            style={
                "font_family": FONT_LATIN,
                "font_weight": "700",
                "font_size": "1.05rem",
                "color": COLOR["green_ink"],
            },
        ),
        rx.text(
            State.chosen_language_name,
            style={"font_size": "1.5rem", "font_weight": "700", "color": COLOR["ink"]},
        ),
        rx.text(
            State.t["your_language_locked_hint"],
            style={"font_size": "0.85rem", "color": COLOR["muted_ink"]},
        ),
        rx.hstack(
            rx.text(
                State.t["tts_title"],
                style={"font_weight": "700", "color": COLOR["ink"], "flex": "1"},
            ),
            rx.button(
                rx.cond(State.tts_on_input, State.t["tts_on_label"], State.t["tts_off_label"]),
                on_click=lambda: State.set_tts_on_input(~State.tts_on_input),
                style={
                    "min_height": "44px",
                    "padding": "0 20px",
                    "border_radius": "12px",
                    "font_weight": "700",
                    "cursor": "pointer",
                    **pill_button_style(True),
                },
            ),
            width="100%",
            align="center",
            style={"margin_top": "4px"},
        ),
        rx.hstack(
            rx.text(
                State.t["autoplay_title"],
                style={"font_weight": "700", "color": COLOR["ink"], "flex": "1"},
            ),
            rx.button(
                rx.cond(State.autoplay_input, State.t["tts_on_label"], State.t["tts_off_label"]),
                on_click=lambda: State.set_autoplay_input(~State.autoplay_input),
                style={
                    "min_height": "44px",
                    "padding": "0 20px",
                    "border_radius": "12px",
                    "font_weight": "700",
                    "cursor": "pointer",
                    **pill_button_style(True),
                },
            ),
            width="100%",
            align="center",
            style={"margin_top": "4px"},
        ),
        rx.text(
            State.t["quiet_hours_title"],
            style={
                "font_family": FONT_LATIN,
                "font_weight": "700",
                "font_size": "1.05rem",
                "color": COLOR["green_ink"],
                "margin_top": "8px",
            },
        ),
        rx.text(
            State.t["quiet_hours_hint"],
            style={"font_size": "0.85rem", "color": COLOR["muted_ink"]},
        ),
        rx.hstack(
            rx.vstack(
                rx.text(
                    State.t["quiet_hours_start_label"],
                    style={"font_size": "0.8rem", "color": COLOR["muted_ink"]},
                ),
                rx.input(
                    type="time",
                    value=State.quiet_hours_start_input,
                    on_change=State.set_quiet_hours_start_input,
                    style={
                        "min_height": "52px",
                        "font_size": "18px",
                        "padding": "0 12px",
                        "border_radius": "10px",
                        "border": f"1px solid {COLOR['warm_border']}",
                    },
                ),
                spacing="1",
                align_items="flex-start",
            ),
            rx.vstack(
                rx.text(
                    State.t["quiet_hours_end_label"],
                    style={"font_size": "0.8rem", "color": COLOR["muted_ink"]},
                ),
                rx.input(
                    type="time",
                    value=State.quiet_hours_end_input,
                    on_change=State.set_quiet_hours_end_input,
                    style={
                        "min_height": "52px",
                        "font_size": "18px",
                        "padding": "0 12px",
                        "border_radius": "10px",
                        "border": f"1px solid {COLOR['warm_border']}",
                    },
                ),
                spacing="1",
                align_items="flex-start",
            ),
            spacing="3",
            width="100%",
        ),
        rx.text(
            State.t["quiet_hours_off_hint"],
            style={"font_size": "0.78rem", "color": COLOR["muted_ink"]},
        ),
        rx.button(
            rx.cond(
                State.settings_saved, State.t["quiet_hours_saved"], State.t["quiet_hours_save"]
            ),
            on_click=State.save_settings,
            **hold_to_hear("settings"),
            style={
                "min_height": "52px",
                "padding": "0 20px",
                "border_radius": "12px",
                "font_weight": "700",
                "cursor": "pointer",
                "align_self": "flex-start",
                **pill_button_style(True),
            },
        ),
        spacing="2",
        align_items="flex-start",
        style={
            "margin": "16px 20px 0",
            "padding": "16px 18px",
            "background": COLOR["card_cream"],
            "border": f"1px solid {COLOR['warm_border']}",
            "border_radius": "18px",
        },
    )


def section_heading() -> rx.Component:
    return rx.heading(
        State.t["heading"],
        style={
            "margin": "28px 20px 12px",
            "font_family": FONT_LATIN,
            "font_weight": "700",
            "font_size": "1.4rem",
            "line_height": "1.25",
            "color": COLOR["ink"],
        },
    )


def contact_row(contact: rx.Var[dict]) -> rx.Component:
    return rx.button(
        rx.hstack(
            rx.box(
                rx.text(
                    contact["initial"],
                    style={
                        "font_family": FONT_LATIN,
                        "font_weight": "700",
                        "font_size": "1.5rem",
                        "color": "#4A3A24",
                    },
                ),
                style={
                    "flex_shrink": "0",
                    "width": "76px",
                    "height": "76px",
                    "border_radius": "50%",
                    "display": "flex",
                    "align_items": "center",
                    "justify_content": "center",
                    "background": contact["tint"],
                },
            ),
            rx.vstack(
                rx.text(
                    contact["name"],
                    style={
                        "font_family": FONT_LATIN,
                        "font_weight": "700",
                        "font_size": "1.2rem",
                        "line_height": "1.3",
                        "color": COLOR["ink"],
                    },
                ),
                rx.text(
                    State.t["tap_to_chat"],
                    style={
                        "font_family": FONT_LATIN,
                        "font_weight": "400",
                        "font_size": "0.85rem",
                        "line_height": "1.35",
                        "color": COLOR["muted_ink"],
                    },
                ),
                spacing="1",
                align_items="flex-start",
                flex="1",
                min_width="0",
            ),
            rx.box(
                style={
                    "width": "14px",
                    "height": "14px",
                    "border_top": "3px solid #B08E5C",
                    "border_right": "3px solid #B08E5C",
                    "transform": "rotate(45deg)",
                    "opacity": ".8",
                    "flex_shrink": "0",
                }
            ),
            spacing="5",
            align="center",
            width="100%",
        ),
        on_click=lambda: State.open_chat(contact["id"]),
        style={
            "width": "100%",
            "min_height": "108px",
            "padding": "16px 20px",
            "background": COLOR["card_cream"],
            "border": f"1px solid {COLOR['warm_border']}",
            "border_radius": "28px",
            "box_shadow": "0 2px 6px rgba(90,66,32,.07)",
            "cursor": "pointer",
            "text_align": "left",
        },
    )


def add_contact_form() -> rx.Component:
    return rx.vstack(
        rx.text(
            State.t["add_person_title"],
            style={"font_weight": "700", "font_size": "1.1rem", "color": COLOR["ink"]},
        ),
        rx.input(
            placeholder=State.t["add_person_name_placeholder"],
            value=State.add_name_input,
            on_change=State.set_add_name_input,
            style={
                "min_height": "56px",
                "font_size": "18px",
                "padding": "0 14px",
                "border_radius": "12px",
                "border": f"1px solid {COLOR['warm_border']}",
                "width": "100%",
            },
        ),
        rx.input(
            placeholder=State.t["add_person_id_placeholder"],
            value=State.add_id_input,
            on_change=State.set_add_id_input,
            style={
                "min_height": "56px",
                "font_size": "16px",
                "font_family": FONT_MONO,
                "padding": "0 14px",
                "border_radius": "12px",
                "border": f"1px solid {COLOR['warm_border']}",
                "width": "100%",
            },
        ),
        rx.cond(
            State.add_error != "",
            rx.text(State.add_error, style={"color": "#8A3E0F", "font_weight": "600"}),
            rx.fragment(),
        ),
        rx.hstack(
            rx.button(
                State.t["cancel_add_button"],
                on_click=State.close_add_contact,
                style={
                    "flex": "1",
                    "min_height": "56px",
                    "border_radius": "14px",
                    "font_weight": "700",
                    "cursor": "pointer",
                    "background": "transparent",
                    "border": f"2px solid {COLOR['warm_border']}",
                    "color": COLOR["muted_ink"],
                },
            ),
            rx.button(
                State.t["add_button"],
                on_click=State.submit_add_contact,
                style={
                    "flex": "1",
                    "min_height": "56px",
                    "border_radius": "14px",
                    "font_weight": "700",
                    "cursor": "pointer",
                    **pill_button_style(True),
                },
            ),
            width="100%",
            spacing="3",
        ),
        spacing="3",
        width="100%",
        style={
            "margin": "0 20px 16px",
            "padding": "18px",
            "background": COLOR["card_cream"],
            "border": f"1px solid {COLOR['warm_border']}",
            "border_radius": "20px",
        },
    )


def add_person_button() -> rx.Component:
    return rx.button(
        State.t["add_person"],
        on_click=State.open_add_contact,
        **hold_to_hear("new_message"),
        style={
            "width": "100%",
            "min_height": "72px",
            "border_radius": "20px",
            "font_family": FONT_LATIN,
            "font_weight": "700",
            "font_size": "1.05rem",
            "cursor": "pointer",
            "background": "transparent",
            "border": f"2px dashed {COLOR['gold_border']}",
            "color": "#8A5B12",
        },
    )


def contact_list() -> rx.Component:
    return rx.vstack(
        rx.cond(
            State.add_contact_open,
            add_contact_form(),
            add_person_button(),
        ),
        rx.cond(
            State.contacts.length() == 0,
            rx.center(
                rx.text(
                    State.t["no_contacts_yet"],
                    style={
                        "font_family": FONT_LATIN,
                        "font_size": "1rem",
                        "color": COLOR["muted_ink"],
                        "text_align": "center",
                        "padding": "20px 20px",
                    },
                ),
            ),
            rx.fragment(),
        ),
        rx.foreach(State.contacts, contact_row),
        spacing="3",
        width="100%",
        style={"padding": "16px 20px 28px"},
    )


def circle_row(circle: rx.Var[dict]) -> rx.Component:
    return rx.button(
        rx.hstack(
            rx.box(
                rx.text(
                    circle["initial"],
                    style={
                        "font_family": FONT_LATIN,
                        "font_weight": "700",
                        "font_size": "1.5rem",
                        "color": "#1F4034",
                    },
                ),
                style={
                    "flex_shrink": "0",
                    "width": "76px",
                    "height": "76px",
                    "border_radius": "50%",
                    "display": "flex",
                    "align_items": "center",
                    "justify_content": "center",
                    "background": circle["tint"],
                },
            ),
            rx.vstack(
                rx.hstack(
                    rx.text(
                        circle["name"],
                        style={
                            "font_family": FONT_LATIN,
                            "font_weight": "700",
                            "font_size": "1.2rem",
                            "line_height": "1.3",
                            "color": COLOR["ink"],
                        },
                    ),
                    rx.cond(
                        circle["kind"] == "announcement",
                        rx.text(
                            State.t["announcement_badge"],
                            style={
                                "font_family": FONT_LATIN,
                                "font_weight": "700",
                                "font_size": "0.7rem",
                                "color": "#8A5B12",
                                "background": "#FCEBC8",
                                "border_radius": "8px",
                                "padding": "2px 8px",
                            },
                        ),
                        rx.fragment(),
                    ),
                    align="center",
                    spacing="2",
                ),
                rx.text(
                    State.t["tap_to_chat"],
                    style={
                        "font_family": FONT_LATIN,
                        "font_weight": "400",
                        "font_size": "0.85rem",
                        "line_height": "1.35",
                        "color": COLOR["muted_ink"],
                    },
                ),
                spacing="1",
                align_items="flex-start",
                flex="1",
                min_width="0",
            ),
            rx.box(
                style={
                    "width": "14px",
                    "height": "14px",
                    "border_top": "3px solid #B08E5C",
                    "border_right": "3px solid #B08E5C",
                    "transform": "rotate(45deg)",
                    "opacity": ".8",
                    "flex_shrink": "0",
                }
            ),
            spacing="5",
            align="center",
            width="100%",
        ),
        on_click=lambda: State.open_circle_chat(circle["id"]),
        style={
            "width": "100%",
            "min_height": "108px",
            "padding": "16px 20px",
            "background": COLOR["card_cream"],
            "border": f"1px solid {COLOR['warm_border']}",
            "border_radius": "28px",
            "box_shadow": "0 2px 6px rgba(90,66,32,.07)",
            "cursor": "pointer",
            "text_align": "left",
        },
    )


def create_circle_form() -> rx.Component:
    return rx.vstack(
        rx.text(
            State.t["create_circle_title"],
            style={"font_weight": "700", "font_size": "1.1rem", "color": COLOR["ink"]},
        ),
        rx.input(
            placeholder=State.t["create_circle_name_placeholder"],
            value=State.create_circle_name_input,
            on_change=State.set_create_circle_name_input,
            style={
                "min_height": "56px",
                "font_size": "18px",
                "padding": "0 14px",
                "border_radius": "12px",
                "border": f"1px solid {COLOR['warm_border']}",
                "width": "100%",
            },
        ),
        rx.hstack(
            rx.checkbox(
                checked=State.create_circle_is_announcement,
                on_change=State.set_create_circle_is_announcement,
            ),
            rx.text(
                State.t["announcement_toggle_label"],
                style={"font_size": "0.95rem", "color": COLOR["muted_ink"]},
            ),
            align="center",
            spacing="2",
        ),
        rx.cond(
            State.circle_error != "",
            rx.text(State.circle_error, style={"color": "#8A3E0F", "font_weight": "600"}),
            rx.fragment(),
        ),
        rx.hstack(
            rx.button(
                State.t["cancel_add_button"],
                on_click=State.close_create_circle,
                style={
                    "flex": "1",
                    "min_height": "56px",
                    "border_radius": "14px",
                    "font_weight": "700",
                    "cursor": "pointer",
                    "background": "transparent",
                    "border": f"2px solid {COLOR['warm_border']}",
                    "color": COLOR["muted_ink"],
                },
            ),
            rx.button(
                State.t["create_button"],
                on_click=State.submit_create_circle,
                style={
                    "flex": "1",
                    "min_height": "56px",
                    "border_radius": "14px",
                    "font_weight": "700",
                    "cursor": "pointer",
                    **pill_button_style(True),
                },
            ),
            width="100%",
            spacing="3",
        ),
        spacing="3",
        width="100%",
        style={
            "margin": "0 20px 16px",
            "padding": "18px",
            "background": COLOR["card_cream"],
            "border": f"1px solid {COLOR['warm_border']}",
            "border_radius": "20px",
        },
    )


def join_circle_form() -> rx.Component:
    return rx.vstack(
        rx.text(
            State.t["join_circle_title"],
            style={"font_weight": "700", "font_size": "1.1rem", "color": COLOR["ink"]},
        ),
        rx.input(
            placeholder=State.t["join_circle_id_placeholder"],
            value=State.join_circle_id_input,
            on_change=State.set_join_circle_id_input,
            style={
                "min_height": "56px",
                "font_size": "16px",
                "font_family": FONT_MONO,
                "padding": "0 14px",
                "border_radius": "12px",
                "border": f"1px solid {COLOR['warm_border']}",
                "width": "100%",
            },
        ),
        rx.cond(
            State.circle_error != "",
            rx.text(State.circle_error, style={"color": "#8A3E0F", "font_weight": "600"}),
            rx.fragment(),
        ),
        rx.hstack(
            rx.button(
                State.t["cancel_add_button"],
                on_click=State.close_join_circle,
                style={
                    "flex": "1",
                    "min_height": "56px",
                    "border_radius": "14px",
                    "font_weight": "700",
                    "cursor": "pointer",
                    "background": "transparent",
                    "border": f"2px solid {COLOR['warm_border']}",
                    "color": COLOR["muted_ink"],
                },
            ),
            rx.button(
                State.t["join_button_circle"],
                on_click=State.submit_join_circle,
                style={
                    "flex": "1",
                    "min_height": "56px",
                    "border_radius": "14px",
                    "font_weight": "700",
                    "cursor": "pointer",
                    **pill_button_style(True),
                },
            ),
            width="100%",
            spacing="3",
        ),
        spacing="3",
        width="100%",
        style={
            "margin": "0 20px 16px",
            "padding": "18px",
            "background": COLOR["card_cream"],
            "border": f"1px solid {COLOR['warm_border']}",
            "border_radius": "20px",
        },
    )


def circle_actions_row() -> rx.Component:
    return rx.hstack(
        rx.button(
            State.t["create_circle"],
            on_click=State.open_create_circle,
            style={
                "flex": "1",
                "min_height": "72px",
                "border_radius": "20px",
                "font_family": FONT_LATIN,
                "font_weight": "700",
                "font_size": "1rem",
                "cursor": "pointer",
                "background": "transparent",
                "border": f"2px dashed {COLOR['gold_border']}",
                "color": "#8A5B12",
            },
        ),
        rx.button(
            State.t["join_circle"],
            on_click=State.open_join_circle,
            style={
                "flex": "1",
                "min_height": "72px",
                "border_radius": "20px",
                "font_family": FONT_LATIN,
                "font_weight": "700",
                "font_size": "1rem",
                "cursor": "pointer",
                "background": "transparent",
                "border": f"2px dashed {COLOR['gold_border']}",
                "color": "#8A5B12",
            },
        ),
        spacing="3",
        width="100%",
    )


def circle_list() -> rx.Component:
    return rx.vstack(
        rx.cond(
            State.create_circle_open,
            create_circle_form(),
            rx.cond(
                State.join_circle_open,
                join_circle_form(),
                circle_actions_row(),
            ),
        ),
        rx.cond(
            State.circles.length() == 0,
            rx.center(
                rx.text(
                    State.t["no_circles_yet"],
                    style={
                        "font_family": FONT_LATIN,
                        "font_size": "1rem",
                        "color": COLOR["muted_ink"],
                        "text_align": "center",
                        "padding": "20px 20px",
                    },
                ),
            ),
            rx.fragment(),
        ),
        rx.foreach(State.circles, circle_row),
        spacing="3",
        width="100%",
        style={"padding": "16px 20px 28px"},
    )


def recording_banner() -> rx.Component:
    return rx.cond(
        State.mic_permission_denied,
        rx.box(
            rx.text(
                State.t["mic_permission_denied"], style={"color": "#8A3E0F", "font_weight": "600"}
            ),
            style={
                "margin": "16px 20px 0",
                "padding": "14px 18px",
                "background": "#FBEFD5",
                "border": "1px solid #EFD9A8",
                "border_radius": "16px",
            },
        ),
        rx.cond(
            State.last_recording_data_url != "",
            rx.vstack(
                rx.hstack(
                    rx.text(
                        rx.cond(
                            State.voice_send_status == "failed",
                            State.t["voice_send_failed"],
                            State.t["recorded_label"],
                        ),
                        style={
                            "font_weight": "700",
                            "color": rx.cond(
                                State.voice_send_status == "failed", "#8A3E0F", COLOR["ink"]
                            ),
                            "flex": "1",
                        },
                    ),
                    rx.audio(src=State.last_recording_data_url, controls=True),
                    spacing="3",
                    align="center",
                    width="100%",
                ),
                rx.hstack(
                    rx.button(
                        State.t["discard"],
                        on_click=State.discard_recording,
                        disabled=State.voice_send_status == "uploading",
                        style={
                            "background": "transparent",
                            "border": f"2px solid {COLOR['warm_border']}",
                            "border_radius": "14px",
                            "padding": "8px 14px",
                            "font_weight": "700",
                            "color": COLOR["muted_ink"],
                            "cursor": "pointer",
                        },
                    ),
                    rx.button(
                        rx.cond(
                            State.voice_send_status == "uploading",
                            State.t["voice_uploading"],
                            rx.cond(
                                State.voice_send_status == "failed",
                                State.t["voice_send_failed"],
                                State.t["voice_send"],
                            ),
                        ),
                        on_click=State.send_voice_recording,
                        disabled=State.voice_send_status == "uploading",
                        style={
                            "background": COLOR["deep_green"],
                            "color": COLOR["card_cream"],
                            "border": "none",
                            "border_radius": "14px",
                            "padding": "8px 16px",
                            "font_weight": "700",
                            "cursor": "pointer",
                        },
                    ),
                    spacing="3",
                ),
                rx.cond(
                    State.voice_send_status == "uploading",
                    voice_upload_progress(),
                    rx.fragment(),
                ),
                spacing="3",
                align="start",
                width="100%",
                style={
                    "margin": "16px 20px 0",
                    "padding": "14px 18px",
                    "background": COLOR["green_tint"],
                    "border_radius": "16px",
                },
            ),
            rx.fragment(),
        ),
    )


def voice_upload_progress() -> rx.Component:
    """Upload progress bar. Its width and percentage are written straight to
    the DOM by UPLOAD_AND_SEND_VOICE_JS_TEMPLATE (ids sat-upload-*), not held
    in Reflex state -- see the comment on showProgress() there."""
    return rx.hstack(
        rx.box(
            rx.box(
                id="sat-upload-fill",
                style={
                    "width": "0%",
                    "height": "100%",
                    "background": COLOR["deep_green"],
                    "border_radius": "8px",
                    "transition": "width 0.2s",
                },
            ),
            id="sat-upload-bar",
            role="progressbar",
            aria_label=State.t["voice_uploading"],
            custom_attrs={"aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0"},
            style={
                "flex": "1",
                "height": "14px",
                "background": COLOR["warm_border"],
                "border_radius": "8px",
                "overflow": "hidden",
            },
        ),
        rx.text(
            "0%",
            id="sat-upload-pct",
            style={
                "min_width": "3.2em",
                "text_align": "right",
                "font_weight": "700",
                "color": COLOR["ink"],
            },
        ),
        spacing="3",
        align="center",
        width="100%",
    )


def mic_button() -> rx.Component:
    return rx.center(
        rx.button(
            rx.vstack(
                rx.box(
                    style={
                        "width": "26px",
                        "height": "40px",
                        "border_radius": "13px",
                        "background": COLOR["card_cream"],
                    }
                ),
                rx.box(
                    style={
                        "width": "34px",
                        "height": "9px",
                        "border_radius": "0 0 18px 18px",
                        "border": f"3px solid {COLOR['card_cream']}",
                        "border_top": "0",
                    }
                ),
                spacing="0",
                align="center",
            ),
            aria_label=State.t["mic_aria"],
            on_mouse_down=State.start_recording,
            on_mouse_up=State.stop_recording,
            on_mouse_leave=State.stop_recording,
            custom_attrs={"data-sat-hold": "press"},
            style={
                "width": "132px",
                "height": "132px",
                "border_radius": "50%",
                "border": f"5px solid {COLOR['card_cream']}",
                "background": rx.cond(
                    State.mic_recording, COLOR["saffron_pressed"], COLOR["saffron"]
                ),
                "color": COLOR["card_cream"],
                "cursor": "pointer",
            },
        ),
        style={"margin_bottom": "-46px", "position": "relative", "z_index": "2"},
    )


def bottom_tabs() -> rx.Component:
    return rx.hstack(
        rx.button(
            State.t["tab_people"],
            on_click=lambda: State.set_active_tab("people"),
            style={
                "flex": "1",
                "min_height": "88px",
                "border_radius": "24px",
                "font_family": FONT_LATIN,
                "font_weight": "700",
                "cursor": "pointer",
                **pill_button_style(True),
            },
            disabled=State.active_tab == "people",
        ),
        rx.button(
            State.t["tab_satsang"],
            on_click=lambda: State.set_active_tab("satsang"),
            **hold_to_hear("circle"),
            style={
                "flex": "1",
                "min_height": "88px",
                "border_radius": "24px",
                "font_family": FONT_LATIN,
                "font_weight": "700",
                "cursor": "pointer",
                "background": COLOR["card_cream"],
                "color": "#4A3A24",
                "border": f"2px solid {COLOR['warm_border']}",
            },
            disabled=State.active_tab == "satsang",
        ),
        spacing="3",
        style={
            "background": COLOR["card_cream"],
            "border_top": f"1px solid {COLOR['warm_border']}",
            "padding": "56px 20px 20px",
        },
    )


def home_screen() -> rx.Component:
    return rx.box(
        rx.box(
            app_header(),
            thought_card(),
            your_id_card(),
            settings_card(),
            section_heading(),
            rx.cond(State.active_tab == "people", contact_list(), circle_list()),
            style={"flex": "1", "min_height": "0", "overflow_y": "auto"},
        ),
        bottom_tabs(),
        style={
            "min_height": "100vh",
            "max_width": "560px",
            "margin": "0 auto",
            "background": COLOR["cream_canvas"],
            "font_family": FONT_LATIN,
            "color": COLOR["ink"],
            "display": "flex",
            "flex_direction": "column",
        },
    )


def join_screen() -> rx.Component:
    """Shown once, before Home. No network call: services/gateway has no
    login step at all (see module docstring's "Identity" section) --
    this just records a local display name and flips `joined`, matching
    the fact that the elder's real identity (the UUID) was already
    minted by ENSURE_IDENTITY_JS on load, before this screen even
    renders."""
    return rx.center(
        rx.vstack(
            rx.text(
                State.t["app_name"],
                style={
                    "font_family": FONT_SERIF,
                    "font_weight": "600",
                    "font_size": "2rem",
                    "color": COLOR["ink"],
                },
            ),
            rx.text(
                State.t["join_prompt"],
                style={"font_size": "1.05rem", "color": COLOR["muted_ink"], "text_align": "center"},
            ),
            rx.input(
                placeholder=State.t["join_placeholder"],
                value=State.display_name_input,
                on_change=State.set_display_name_input,
                style={
                    "min_height": "60px",
                    "font_size": "20px",
                    "padding": "0 16px",
                    "border_radius": "12px",
                    "border": f"1px solid {COLOR['warm_border']}",
                    "width": "100%",
                },
            ),
            rx.button(
                State.t["join_button"],
                on_click=State.join_circle,
                style={
                    "min_height": "60px",
                    "width": "100%",
                    "font_size": "20px",
                    "font_weight": "700",
                    "border_radius": "12px",
                    "background": COLOR["deep_green"],
                    "color": COLOR["card_cream"],
                    "border": "none",
                    "cursor": "pointer",
                },
            ),
            spacing="4",
            width="100%",
            style={"max_width": "420px", "padding": "24px"},
        ),
        style={"min_height": "100vh", "background": COLOR["cream_canvas"]},
    )


def chat_screen() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.button(
                State.t["back"],
                on_click=State.go_home,
                **hold_to_hear("back"),
                style={
                    "min_height": "56px",
                    "font_size": "18px",
                    "font_weight": "600",
                    "border_radius": "10px",
                    "background": COLOR["green_tint"],
                    "border": "none",
                    "padding": "0 16px",
                    "cursor": "pointer",
                },
            ),
            rx.text(
                State.chat_title,
                style={"font_size": "18px", "font_weight": "600", "color": COLOR["muted_ink"]},
            ),
            rx.spacer(),
            language_toggle(),
            width="100%",
            align="center",
            spacing="3",
        ),
        rx.cond(
            State.current_circle_id != "",
            rx.hstack(
                rx.vstack(
                    rx.text(
                        State.t["circle_id_label"],
                        style={
                            "font_family": FONT_LATIN,
                            "font_weight": "600",
                            "font_size": "0.8rem",
                            "color": COLOR["green_ink"],
                        },
                    ),
                    rx.text(
                        State.current_circle_id,
                        style={
                            "font_family": FONT_MONO,
                            "font_size": "0.78rem",
                            "color": COLOR["muted_ink"],
                            "word_break": "break-all",
                            "user_select": "all",
                        },
                    ),
                    spacing="1",
                    align_items="flex-start",
                    flex="1",
                    min_width="0",
                ),
                rx.button(
                    rx.cond(
                        State.copied_circle_id,
                        State.t["copied_id"],
                        rx.cond(
                            State.copy_circle_id_failed,
                            State.t["copy_failed"],
                            State.t["copy_id"],
                        ),
                    ),
                    on_click=State.copy_circle_id,
                    style={
                        "flex_shrink": "0",
                        "min_height": "44px",
                        "padding": "0 14px",
                        "border_radius": "12px",
                        "font_weight": "700",
                        "cursor": "pointer",
                        **pill_button_style(True),
                    },
                ),
                style={
                    "width": "100%",
                    "padding": "10px 4px",
                    "align_items": "center",
                },
            ),
        ),
        rx.text(
            "",
            id="live-chat-status",
            style={"font_size": "0.85rem", "color": COLOR["muted_ink"]},
        ),
        rx.box(
            id="live-chat-messages",
            style={
                "min_height": "45vh",
                "max_height": "50vh",
                "overflow_y": "auto",
                "padding": "12px 4px",
            },
        ),
        recording_banner(),
        mic_button(),
        rx.hstack(
            rx.input(
                id="live-chat-input",
                placeholder=State.t["type_placeholder"],
                on_key_down=State.handle_chat_key_down,
                style={
                    "flex": "1",
                    "min_height": "60px",
                    "font_size": "20px",
                    "padding": "0 16px",
                    "border_radius": "12px",
                    "border": f"1px solid {COLOR['warm_border']}",
                },
            ),
            rx.button(
                State.t["send"],
                on_click=State.send_live_message,
                **hold_to_hear("send_button"),
                style={
                    "min_height": "60px",
                    "min_width": "100px",
                    "font_size": "20px",
                    "font_weight": "700",
                    "border_radius": "12px",
                    "background": COLOR["deep_green"],
                    "color": COLOR["card_cream"],
                    "border": "none",
                    "cursor": "pointer",
                },
            ),
            width="100%",
            spacing="3",
            style={
                "position": "sticky",
                "bottom": "0",
                "background": COLOR["cream_canvas"],
                "padding_top": "12px",
            },
        ),
        spacing="3",
        width="100%",
        on_mount=State.enter_chat,
        style={
            "max_width": "560px",
            "margin": "0 auto",
            "padding": "24px 16px",
            "min_height": "100vh",
            "background": COLOR["cream_canvas"],
            "font_family": FONT_LATIN,
        },
    )


def index() -> rx.Component:
    return rx.box(
        rx.cond(
            State.joined,
            # Until the saved prefs are read, show the app as before: flashing the choice
            # screen at someone who already chose would invite a stray tap.
            rx.cond(
                State.lang_locked | ~State.prefs_loaded,
                rx.cond(
                    (State.current_contact_id == "") & (State.current_circle_id == ""),
                    home_screen(),
                    chat_screen(),
                ),
                language_screen(),
            ),
            join_screen(),
        ),
        on_mount=State.bootstrap,
        style={"background": COLOR["cream_canvas"], "font_family": FONT_LATIN},
    )


app = rx.App(stylesheets=STYLESHEETS)
app.add_page(index, title=f"{config.app_name}")
