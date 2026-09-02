"""SatSandesh elder chat UI shell (Week 4 M1).

Visual design lifted from a Claude Design handoff (see
docs/design/satsandesh-home-screen-design/ for the source spec): warm
devotional palette, Mulish/Noto Sans Telugu/Lora type system on a 20px
rem root (so a 200% OS text setting scales the whole screen), and named
interaction states with documented WCAG contrast ratios.

Placeholder/mock data only, deliberately — wiring this screen to the real
gateway backend is M2's separate Week 4 task ("Wire client -> gateway
end-to-end + staging deploy"), which depends on this screen existing, not
the other way around.

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

Backend-reality update (2026-09-01): the team confirmed the gateway/
backbone in `gateway/` + `backbone/` as final -- it implements exactly
one shared broadcast circle (see `gateway/ws.py`'s own docstring), not
per-contact routing, and has no sent/delivered-receipt concept. Rather
than keep pretending the old per-contact mock threads were real, this
screen now does genuine live messaging against that real shared circle:
join with a display name (`POST /session`), then connect
(`ws://.../ws`), following the exact connect/reconnect-with-backoff
pattern already verified end-to-end (locally and against the real
deployed server) in `elder_app/gateway_ws_proof.py` -- credit to that
module for the hard-won JS wiring details (on_mount + rx.call_script,
not rx.script; see its docstring for why). The five contacts on the
Home screen stay as a faces-before-names navigation surface (still the
real Week 4 design deliverable), but every one of them opens the same
real shared circle, with an honest bilingual note saying so -- since
that's genuinely all that exists on the backend right now, showing
separate private threads would be a UI lie, not a design choice.
"""

import json
import os

import reflex as rx
from rxconfig import config

GATEWAY_PUBLIC_URL = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost")

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
    "&family=Noto+Sans+Telugu:wght@400;600;700&family=Lora:wght@600&display=swap"
]

# ---------------------------------------------------------------------------
# Copy (bilingual)
# ---------------------------------------------------------------------------

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
        "satsang_placeholder": "Satsang sessions will appear here soon.",
        "recording_label": "Recording...",
        "recorded_label": "Voice message recorded",
        "discard": "Discard",
        "mic_permission_denied": "Microphone access was denied. "
        "Allow microphone access to record a voice message.",
        "back": "< Back",
        "type_placeholder": "Type a message...",
        "send": "Send",
        "no_messages": "No messages yet. Say hello!",
        "join_prompt": "What should we call you?",
        "join_placeholder": "Your name",
        "join_button": "Join",
        "shared_circle_note": "This is a shared circle — "
        "everyone connected can see these messages.",
        "connecting": "Connecting...",
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
        "satsang_placeholder": "సత్సంగ సమావేశాలు త్వరలో ఇక్కడ కనిపిస్తాయి.",
        "recording_label": "రికార్డ్ అవుతోంది...",
        "recorded_label": "వాయిస్ సందేశం రికార్డ్ చేయబడింది",
        "discard": "తొలగించు",
        "mic_permission_denied": "మైక్రోఫోన్ యాక్సెస్ నిరాకరించబడింది. దయచేసి అనుమతించండి.",
        "back": "< వెనుకకు",
        "type_placeholder": "సందేశం టైప్ చేయండి...",
        "send": "పంపండి",
        "no_messages": "ఇంకా సందేశాలు లేవు. హలో చెప్పండి!",
        "join_prompt": "మిమ్మల్ని ఏమని పిలవాలి?",
        "join_placeholder": "మీ పేరు",
        "join_button": "చేరండి",
        "shared_circle_note": "ఇది ఒక భాగస్వామ్య వర్గం — కనెక్ట్ అయిన అందరూ ఈ సందేశాలను చూడగలరు.",
        "connecting": "కనెక్ట్ అవుతోంది...",
    },
}

CONTACTS = [
    {
        "id": "1",
        "name_en": "Lakshmi",
        "name_te": "లక్ష్మి",
        "meta_en": "Daughter · Hyderabad",
        "meta_te": "కూతురు · హైదరాబాద్",
        "initial_en": "L",
        "initial_te": "ల",
        "tint": TINTS[0],
    },
    {
        "id": "2",
        "name_en": "Ramana Rao",
        "name_te": "రమణ రావు",
        "meta_en": "Satsang · Kondapur",
        "meta_te": "సత్సంగం · కొండాపూర్",
        "initial_en": "R",
        "initial_te": "ర",
        "tint": TINTS[1],
    },
    {
        "id": "3",
        "name_en": "Padma Aunty",
        "name_te": "పద్మ ఆంటీ",
        "meta_en": "Neighbour · Ameerpet",
        "meta_te": "పొరుగు · అమీర్‌పేట్",
        "initial_en": "P",
        "initial_te": "ప",
        "tint": TINTS[2],
    },
    {
        "id": "4",
        "name_en": "Kondapur Circle",
        "name_te": "కొండాపూర్ బృందం",
        "meta_en": "14 members",
        "meta_te": "14 మంది",
        "initial_en": "K",
        "initial_te": "కొ",
        "tint": TINTS[3],
    },
    {
        "id": "5",
        "name_en": "Ashram Announcements",
        "name_te": "ఆశ్రమ ప్రకటనలు",
        "meta_en": "Listen only",
        "meta_te": "వినడానికి మాత్రమే",
        "initial_en": "A",
        "initial_te": "ఆ",
        "tint": TINTS[4],
    },
]

# ---------------------------------------------------------------------------
# Client-side JS: real session join + WebSocket chat against the shared
# circle. Pattern lifted from elder_app/gateway_ws_proof.py -- on_mount +
# rx.call_script (not rx.script, which doesn't fire in this Reflex/React
# combination; see that module's docstring for the three ruled-out
# attempts). The live message feed is rendered by this JS directly into a
# plain DOM node (id="live-chat-messages"), not through Reflex state --
# same reasoning as the proof module: a persistent socket pushing many
# async updates doesn't fit rx.call_script's one-shot call/callback shape,
# and wrapping it as a proper Reflex component wasn't verifiable against
# stable documented usage without risking a subtly wrong implementation.
# ---------------------------------------------------------------------------

JOIN_JS_TEMPLATE = """
(async () => {
    try {
        const resp = await fetch(%(gateway_url)s + "/session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ display_name: %(name)s }),
        });
        if (!resp.ok) return "error:HTTP " + resp.status;
        const data = await resp.json();
        window.__satToken = data.token;
        window.__satUserId = data.user_id;
        return JSON.stringify({ token: data.token, user_id: data.user_id });
    } catch (err) {
        return "error:" + (err && err.message ? err.message : "unreachable");
    }
})()
"""

CHAT_CONNECT_JS_TEMPLATE = """
if (window.__satsandeshWsInit) {
  console.log("[satsandesh-ws] already initialized, skipping");
} else {
window.__satsandeshWsInit = true;
(function () {
  const GATEWAY_URL = %(gateway_url)s;
  const WS_URL = GATEWAY_URL.replace(/^http/, "ws");
  const MY_ID = window.__satUserId;
  const token = window.__satToken;

  const messagesEl = document.getElementById("live-chat-messages");
  const statusEl = document.getElementById("live-chat-status");
  if (!messagesEl || !token) return;

  let ws = null;
  let backoffMs = 1000;
  const MAX_BACKOFF_MS = 30000;
  let reconnectAttempt = 0;
  let intentionalClose = false;

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
    console.log("[satsandesh-ws] status:", text);
  }

  function appendMessage(senderId, body, isOwn) {
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
    const who = document.createElement("div");
    who.style.fontSize = "13px";
    who.style.fontWeight = "700";
    who.style.color = "#6E6047";
    who.style.marginBottom = "2px";
    who.textContent = isOwn ? "You" : senderId;
    const text = document.createElement("div");
    text.style.fontSize = "20px";
    text.style.color = "#2A2118";
    text.textContent = body;
    bubble.appendChild(who);
    bubble.appendChild(text);
    row.appendChild(bubble);
    messagesEl.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function appendSystemLine(text) {
    const row = document.createElement("div");
    row.style.margin = "8px 0";
    row.style.fontSize = "14px";
    row.style.color = "#B4531A";
    row.style.fontStyle = "italic";
    row.textContent = text;
    messagesEl.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function scheduleReconnect() {
    reconnectAttempt += 1;
    const jitter = Math.random() * 300;
    const delay = backoffMs + jitter;
    setStatus("Reconnecting... (attempt " + reconnectAttempt + ")");
    setTimeout(connect, delay);
    backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
  }

  function connect() {
    setStatus("Connecting...");
    ws = new WebSocket(WS_URL + "/ws?token=" + encodeURIComponent(token));
    window.__satWs = ws;

    ws.onopen = function () {
      setStatus("Connected");
      backoffMs = 1000;
      reconnectAttempt = 0;
    };

    ws.onmessage = function (event) {
      const data = JSON.parse(event.data);
      if (data.type === "history") {
        messagesEl.innerHTML = "";
        for (const m of data.messages) {
          appendMessage(m.sender_id, m.body, m.sender_id === MY_ID);
        }
        if (data.warning) appendSystemLine(data.warning);
      } else if (data.type === "message") {
        appendMessage(data.sender_id, data.body, data.sender_id === MY_ID);
      } else if (data.type === "error") {
        appendSystemLine(data.detail);
      }
    };

    ws.onclose = function (event) {
      if (intentionalClose) return;
      if (event.code === 4401) {
        setStatus("Session expired -- please rejoin");
        return;
      }
      scheduleReconnect();
    };

    ws.onerror = function () {};
  }

  connect();

  window.addEventListener("beforeunload", function () {
    intentionalClose = true;
    if (ws) ws.close();
  });
})();
}
"""

CHAT_SEND_JS = """
(() => {
  const input = document.getElementById("live-chat-input");
  const body = (input.value || "").trim();
  if (!body || !window.__satWs || window.__satWs.readyState !== WebSocket.OPEN) return "";
  window.__satWs.send(JSON.stringify({ body: body }));
  input.value = "";
  return "sent";
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


class State(rx.State):
    language: str = "en"
    current_contact_id: str = ""
    active_tab: str = "people"
    contacts: list[dict[str, str]] = CONTACTS

    # Real gateway session -- POST /session -> a token, then the WS
    # connect in CHAT_CONNECT_JS_TEMPLATE. No password, no account:
    # the gateway's own auth.py issues a token for whatever display name
    # is given (see that file's docstring -- not final auth, a real step
    # up from a bare client-asserted string on every message).
    display_name_input: str = ""
    joined: bool = False
    join_error: str = ""
    my_display_name: str = ""

    mic_recording: bool = False
    mic_permission_denied: bool = False
    last_recording_data_url: str = ""

    @rx.var
    def t(self) -> dict[str, str]:
        return TEXTS[self.language]

    @rx.var
    def current_contact(self) -> dict[str, str]:
        for c in self.contacts:
            if c["id"] == self.current_contact_id:
                return c
        return {}

    def open_chat(self, contact_id: str):
        self.current_contact_id = contact_id

    def go_home(self):
        self.current_contact_id = ""

    def toggle_language(self):
        self.language = "te" if self.language == "en" else "en"

    def set_active_tab(self, tab: str):
        self.active_tab = tab

    def set_display_name_input(self, value: str):
        self.display_name_input = value

    def join_circle(self):
        self.join_error = ""
        name = self.display_name_input.strip()
        if not name:
            return None
        js = JOIN_JS_TEMPLATE % {
            "gateway_url": json.dumps(GATEWAY_PUBLIC_URL),
            "name": json.dumps(name),
        }
        return rx.call_script(js, callback=State.on_joined)

    def on_joined(self, result: str):
        if result.startswith("error:"):
            self.join_error = result[len("error:") :]
            return
        # window.__satToken/__satUserId (set by JOIN_JS_TEMPLATE) are all
        # the JS side needs -- CHAT_CONNECT_JS_TEMPLATE reads them
        # directly, no need to round-trip the user_id through Python state.
        self.my_display_name = self.display_name_input.strip()
        self.joined = True

    def connect_chat(self):
        if not self.joined:
            return None
        js = CHAT_CONNECT_JS_TEMPLATE % {"gateway_url": json.dumps(GATEWAY_PUBLIC_URL)}
        return rx.call_script(js)

    def send_live_message(self):
        return rx.call_script(CHAT_SEND_JS)

    def handle_chat_key_down(self, key: str):
        if key == "Enter":
            return rx.call_script(CHAT_SEND_JS)
        return None

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


# ---------------------------------------------------------------------------
# Shared style helpers
# ---------------------------------------------------------------------------


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
    name = rx.cond(State.language == "en", contact["name_en"], contact["name_te"])
    meta = rx.cond(State.language == "en", contact["meta_en"], contact["meta_te"])
    initial = rx.cond(State.language == "en", contact["initial_en"], contact["initial_te"])
    return rx.button(
        rx.hstack(
            rx.box(
                rx.text(
                    initial,
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
                    name,
                    style={
                        "font_family": FONT_LATIN,
                        "font_weight": "700",
                        "font_size": "1.2rem",
                        "line_height": "1.3",
                        "color": COLOR["ink"],
                    },
                ),
                rx.text(
                    meta,
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


def contact_list() -> rx.Component:
    return rx.vstack(
        rx.foreach(State.contacts, contact_row),
        spacing="3",
        width="100%",
        style={"padding": "0 20px 28px"},
    )


def satsang_placeholder() -> rx.Component:
    return rx.center(
        rx.text(
            State.t["satsang_placeholder"],
            style={
                "font_family": FONT_LATIN,
                "font_size": "1.05rem",
                "color": COLOR["muted_ink"],
                "text_align": "center",
                "padding": "0 40px",
            },
        ),
        style={"min_height": "35vh"},
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
            rx.hstack(
                rx.text(
                    State.t["recorded_label"],
                    style={"font_weight": "700", "color": COLOR["ink"], "flex": "1"},
                ),
                rx.audio(src=State.last_recording_data_url, controls=True),
                rx.button(
                    State.t["discard"],
                    on_click=State.discard_recording,
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
                spacing="3",
                align="center",
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
            recording_banner(),
            section_heading(),
            rx.cond(State.active_tab == "people", contact_list(), satsang_placeholder()),
            style={"flex": "1", "min_height": "0", "overflow_y": "auto"},
        ),
        mic_button(),
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
    """Shown once, before Home. The gateway needs a display name to
    issue a session token (POST /session) -- there's no other identity
    source yet (see auth.py's own docstring: not final auth)."""
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
            rx.cond(
                State.join_error != "",
                rx.text(State.join_error, style={"color": "#8A3E0F", "font_weight": "600"}),
                rx.fragment(),
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
                State.my_display_name,
                style={"font_size": "18px", "font_weight": "600", "color": COLOR["muted_ink"]},
            ),
            rx.spacer(),
            language_toggle(),
            width="100%",
            align="center",
            spacing="3",
        ),
        rx.box(
            rx.text(
                State.t["shared_circle_note"],
                style={"font_size": "0.9rem", "color": "#8A5B12"},
            ),
            style={
                "background": COLOR["gold_sand"],
                "border": "1px solid #EFD9A8",
                "border_radius": "14px",
                "padding": "10px 14px",
                "margin_bottom": "8px",
            },
        ),
        rx.text(
            State.t["connecting"],
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
        on_mount=State.connect_chat,
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
            rx.cond(State.current_contact_id == "", home_screen(), chat_screen()),
            join_screen(),
        ),
        style={"background": COLOR["cream_canvas"], "font_family": FONT_LATIN},
    )


app = rx.App(stylesheets=STYLESHEETS)
app.add_page(index, title=f"{config.app_name}")
