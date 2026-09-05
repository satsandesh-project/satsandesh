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
        "no_circles_yet": 'No circles yet. Create one, or join with an ID.',
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
        "join_button": "Continue",
        "connecting": "Connecting...",
        "your_id_label": "Your ID -- share this with family so they can add you",
        "copy_id": "Copy",
        "copied_id": "Copied!",
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
        "join_button": "కొనసాగించు",
        "connecting": "కనెక్ట్ అవుతోంది...",
        "your_id_label": "మీ ఐడీ — కుటుంబంతో పంచుకోండి, వారు మిమ్మల్ని చేర్చుకోవచ్చు",
        "copy_id": "కాపీ చేయి",
        "copied_id": "కాపీ అయ్యింది!",
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

COPY_ID_JS_TEMPLATE = """
(async () => {
    try {
        await navigator.clipboard.writeText(%(my_id)s);
        return "ok";
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

  function statusLabel(msg) {
    if (msg.status === "cancelled") return %(status_cancelled)s;
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
    if (msg.status === "delivered") return %(status_delivered)s;
    if (msg.status === "sent") return %(status_sent)s;
    return %(status_pending)s;
  }

  function renderCurrentThread() {
    const messagesEl = document.getElementById("live-chat-messages");
    const contactId = window.__satCurrentContactId;
    if (!messagesEl || !contactId) return;
    const thread = threadFor(contactId);
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
      bubble.style.cursor = isOwn && msg.status === "pending" ? "pointer" : "default";
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
      const text = document.createElement("div");
      text.style.fontSize = "20px";
      text.style.color = "#2A2118";
      text.textContent = msg.text;
      bubble.appendChild(text);
      if (isOwn) {
        const status = document.createElement("div");
        status.style.fontSize = "12px";
        status.style.marginTop = "4px";
        status.style.color = msg.status === "cancelled" ? "#8A3E0F" : "#6E6047";
        status.style.fontStyle = msg.status === "cancelled" ? "italic" : "normal";
        status.textContent = statusLabel(msg);
        bubble.appendChild(status);
        if (msg.status === "pending" && msg.id) {
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

  function scheduleSentTransition(contactId, messageId) {
    if (window.__satPendingTimers[messageId]) clearTimeout(window.__satPendingTimers[messageId]);
    window.__satPendingTimers[messageId] = setTimeout(() => {
      delete window.__satPendingTimers[messageId];
      const thread = threadFor(contactId);
      const m = thread.find((x) => x.id === messageId);
      if (m && m.status === "pending") {
        upsertMessage(contactId, { id: messageId, status: "sent" });
      }
    }, UNDO_WINDOW_MS + 1000);
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

  function sendMessageFrame(contactId, text, clientMsgId, targetType) {
    window.__satPendingSendTarget = window.__satPendingSendTarget || {};
    window.__satPendingSendTarget[clientMsgId] = contactId;
    ws.send(JSON.stringify({
      type: "message.send",
      data: {
        client_msg_id: clientMsgId,
        target_type: targetType || "user",
        target_id: contactId,
        kind: "text",
        text: text,
      },
    }));
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
    for (const contactId of Object.keys(window.__satConversations)) {
      for (const msg of window.__satConversations[contactId]) {
        if (msg.author_id === window.__satUserId && !msg.id && msg.client_msg_id) {
          sendMessageFrame(contactId, msg.text, msg.client_msg_id, msg.target_type);
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
      upsertMessage(contactId, {
        id: data.id,
        author_id: data.author_id,
        target_id: data.target_id,
        target_type: data.target_type,
        text: data.text,
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
        text: m.text,
        status: m.status,
      }));
      for (const m of data.messages) {
        if (m.author_id !== window.__satUserId && m.status === "sent") sendDeliveredAck(m.id);
      }
      if (window.__satCurrentContactId === contactId) renderCurrentThread();
    } else if (frame.type === "error") {
      console.log("[satsandesh-ws] error frame:", frame.data);
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
    if (!window.__satConversations[contactId]) window.__satConversations[contactId] = [];
    window.__satConversations[contactId].push({
        client_msg_id: clientMsgId,
        author_id: window.__satUserId,
        target_id: contactId,
        target_type: targetType,
        text: text,
        status: "pending",
    });
    if (window.__satRenderCurrentThread) window.__satRenderCurrentThread();
    window.__satSendMessage(contactId, text, clientMsgId, targetType);
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
            body: JSON.stringify({ name: %(name)s }),
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
    contacts: list[dict[str, str]] = []

    # Real circles (services/gateway/'s app/circles.py) -- create, list,
    # self-join (POST /circles/{id}/join, PR #32). Loaded once identity is
    # ready, same timing as contacts, since GET /circles needs a real
    # Bearer token but not the "joined" (named) state.
    circles: list[dict[str, str]] = []
    current_circle_id: str = ""
    create_circle_open: bool = False
    create_circle_name_input: str = ""
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

    def set_active_tab(self, tab: str):
        self.active_tab = tab

    # -- Bootstrap: identity, saved name, saved contacts, then (if already
    # joined before) the persistent WS connection -- all on the app's own
    # on_mount, once per load, not per screen. --------------------------

    def bootstrap(self):
        return rx.call_script(ENSURE_IDENTITY_JS, callback=State.on_identity_ready)

    def on_identity_ready(self, user_id: str):
        self.my_user_id = user_id
        return [
            rx.call_script(LOAD_NAME_JS, callback=State.on_name_loaded),
            rx.call_script(LOAD_CONTACTS_JS, callback=State.on_contacts_loaded),
            self.load_circles(),
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
            "status_pending": json.dumps(TEXTS["en"]["status_pending"]),
            "status_sent": json.dumps(TEXTS["en"]["status_sent"]),
            "status_delivered": json.dumps(TEXTS["en"]["status_delivered"]),
            "status_cancelled": json.dumps(TEXTS["en"]["status_cancelled"]),
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
        self.circle_error = ""

    def close_create_circle(self):
        self.create_circle_open = False

    def set_create_circle_name_input(self, value: str):
        self.create_circle_name_input = value

    def submit_create_circle(self):
        name = self.create_circle_name_input.strip()
        if not name:
            return None
        self.circle_error = ""
        js = CREATE_CIRCLE_JS_TEMPLATE % {
            "gateway_url": json.dumps(GATEWAY_PUBLIC_URL),
            "name": json.dumps(name),
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
        js = COPY_ID_JS_TEMPLATE % {"my_id": json.dumps(self.my_user_id)}
        return rx.call_script(js, callback=State.on_id_copied)

    def on_id_copied(self, result: str):
        self.copied_id = result == "ok"

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
                },
            ),
            spacing="1",
            align_items="flex-start",
            flex="1",
            min_width="0",
        ),
        rx.button(
            rx.cond(State.copied_id, State.t["copied_id"], State.t["copy_id"]),
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
            your_id_card(),
            recording_banner(),
            section_heading(),
            rx.cond(State.active_tab == "people", contact_list(), circle_list()),
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
            rx.cond(
                (State.current_contact_id == "") & (State.current_circle_id == ""),
                home_screen(),
                chat_screen(),
            ),
            join_screen(),
        ),
        on_mount=State.bootstrap,
        style={"background": COLOR["cream_canvas"], "font_family": FONT_LATIN},
    )


app = rx.App(stylesheets=STYLESHEETS)
app.add_page(index, title=f"{config.app_name}")
