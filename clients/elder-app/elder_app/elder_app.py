"""PLACEHOLDER elder client -- proves gateway<->client WS wiring works.

Not Member 1's real UI shell (see rxconfig.py's module docstring for the
full explanation and what to carry forward when the real shell lands).

Architecture decision, deliberate not accidental: the actual chat
WebSocket connection is plain browser JavaScript, not a
Reflex-State-managed connection. Reflex's own event-trigger/hooks
machinery for wrapping a live external WebSocket in a custom Component
exists, but its exact wiring (the `addEvents` JS bridge, hook
registration order) isn't something this session could verify against
real, stable, documented usage without risking a subtly wrong
implementation. Plain `fetch` + `WebSocket` are standard browser APIs
with a stable spec -- getting reconnect-with-backoff right in vanilla JS
carries a known, checkable risk, not a guessed one. This also means the
architecture Week 4's task implies (CORS genuinely matters, because the
browser holds the connection directly) is exactly what's built, not a
server-side relay that would make CORS moot.

Three real attempts to actually get the JS running, in order, each ruled
out with real evidence rather than assumed to be the problem:

  1. `rx.script(CODE)` (the higher-level helper) renders through
     react-helmet, which crashed in this Reflex/React combination
     (`Cannot read properties of null (reading 'addEventListener')`) --
     confirmed by inspecting the compiled output: the script WAS
     correctly generated, wrapped in `jsx(Helmet, ...)`, but Helmet's own
     commit crashed before ever attaching it to the document.
  2. `rx.el.script(CODE)` (inline, no Helmet) avoided that crash, and
     `rx.el.script(src="/client.js")` (external file) avoided it too --
     but NEITHER ever actually executed. Confirmed for the inline version
     by clicking Join both via the real UI and via a direct `.click()`
     call in the console (neither fired, no exception either -- the
     fingerprint of "never ran", not a logic bug). Confirmed for the
     external version by checking the network log directly: the tag was
     genuinely present in the live DOM with the correct `src`, and
     `curl`-ing that URL directly returned the file correctly, but the
     browser never issued a request for it at all. Both share the same
     symptom despite being different mechanisms, which points at
     something specific to how this app's SSR/hydration handles `<script>`
     insertion generally, not an inline-vs-external distinction.
  3. **This is the one that works**: `on_mount=rx.call_script(CODE)`.
     Reflex's own `on_mount` event trigger compiles to a real React
     `useEffect(() => {...}, [])` (confirmed by reading
     reflex_base/components/component.py's hook-generation code before
     relying on it), and `rx.call_script` executes arbitrary JS through
     Reflex's own event-dispatch path -- neither depends on a `<script>`
     tag being inserted and executed by the browser at all, which is
     exactly the mechanism that wasn't working. This is Reflex's
     documented, supported way to run client JS, not a workaround found
     by chance.

When Member 1's real UI shell replaces this page, CLIENT_JS below (fetch
/session, connect, reconnect-with-backoff, send/receive) is the part
worth carrying forward, adapted to call into whatever State/rendering the
real shell uses instead of raw DOM calls.
"""

import json
import os

import reflex as rx

# The gateway's origin as the BROWSER will reach it -- not a
# docker-internal service name. Same value rxconfig.py's REFLEX_API_URL
# addresses for Reflex's own unrelated internal protocol; this one is
# strictly for reaching the SatSandesh gateway's /session and /ws routes.
# In the compose deployment, Caddy serves both the elder-app page and the
# gateway's routes from this one origin (see infra/caddy/Caddyfile), so
# this is also correct for the dockerized case even though the default
# below looks like it's only for local dev.
GATEWAY_PUBLIC_URL = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost")

CLIENT_JS_TEMPLATE = r"""
if (window.__satsandeshWsInit) {
  // on_mount can fire more than once under React strict-mode double
  // invocation in dev -- guard against wiring up two parallel connections.
  console.log("[satsandesh-ws] already initialized, skipping");
} else {
window.__satsandeshWsInit = true;
(function () {
  const GATEWAY_URL = %(gateway_url)s;
  const WS_URL = GATEWAY_URL.replace(/^http/, "ws");

  const els = {
    joinPanel: document.getElementById("join-panel"),
    nameInput: document.getElementById("display-name-input"),
    joinButton: document.getElementById("join-button"),
    statusLine: document.getElementById("status-line"),
    chatPanel: document.getElementById("chat-panel"),
    messages: document.getElementById("chat-messages"),
    chatInput: document.getElementById("chat-input"),
    sendButton: document.getElementById("send-button"),
  };

  let ws = null;
  let token = null;
  let myUserId = null;
  let backoffMs = 1000;
  const MAX_BACKOFF_MS = 30000;
  let reconnectAttempt = 0;
  let intentionalClose = false;

  function setStatus(text) {
    els.statusLine.textContent = text;
    console.log("[satsandesh-ws] status:", text);
  }

  function appendMessage(senderId, body, isOwn) {
    const row = document.createElement("div");
    row.style.margin = "6px 0";
    row.style.textAlign = isOwn ? "right" : "left";
    const bubble = document.createElement("span");
    bubble.style.display = "inline-block";
    bubble.style.padding = "8px 12px";
    bubble.style.borderRadius = "10px";
    bubble.style.background = isOwn ? "#dbeafe" : "#f1f5f9";
    bubble.style.maxWidth = "80%%";
    const who = document.createElement("div");
    who.style.fontSize = "12px";
    who.style.fontWeight = "600";
    who.style.color = "#475569";
    who.textContent = senderId;
    const text = document.createElement("div");
    text.style.fontSize = "16px";
    text.textContent = body;
    bubble.appendChild(who);
    bubble.appendChild(text);
    row.appendChild(bubble);
    els.messages.appendChild(row);
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function appendSystemLine(text) {
    const row = document.createElement("div");
    row.style.margin = "6px 0";
    row.style.fontSize = "13px";
    row.style.color = "#b91c1c";
    row.style.fontStyle = "italic";
    row.textContent = text;
    els.messages.appendChild(row);
    els.messages.scrollTop = els.messages.scrollHeight;
  }

  function scheduleReconnect() {
    reconnectAttempt += 1;
    const jitter = Math.random() * 300;
    const delay = backoffMs + jitter;
    console.log(
      "[satsandesh-ws] disconnected -- reconnect attempt " +
        reconnectAttempt +
        " in " +
        Math.round(delay) +
        "ms"
    );
    setStatus("Reconnecting... (attempt " + reconnectAttempt + ")");
    setTimeout(connect, delay);
    backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
  }

  function connect() {
    if (!token) return;
    console.log("[satsandesh-ws] connecting to", WS_URL + "/ws");
    setStatus("Connecting...");
    ws = new WebSocket(WS_URL + "/ws?token=" + encodeURIComponent(token));

    ws.onopen = function () {
      console.log("[satsandesh-ws] connected");
      setStatus("Connected as " + myUserId);
      backoffMs = 1000;
      reconnectAttempt = 0;
    };

    ws.onmessage = function (event) {
      const data = JSON.parse(event.data);
      console.log("[satsandesh-ws] received:", data);
      if (data.type === "history") {
        els.messages.innerHTML = "";
        for (const m of data.messages) {
          appendMessage(m.sender_id, m.body, m.sender_id === myUserId);
        }
        if (data.warning) {
          appendSystemLine(data.warning);
        }
      } else if (data.type === "message") {
        appendMessage(data.sender_id, data.body, data.sender_id === myUserId);
      } else if (data.type === "error") {
        appendSystemLine(data.detail);
      }
    };

    ws.onclose = function (event) {
      console.log("[satsandesh-ws] closed, code=" + event.code + " reason=" + event.reason);
      if (intentionalClose) return;
      if (event.code === 4401) {
        setStatus("Auth rejected -- please rejoin");
        appendSystemLine("Session rejected by server: " + event.reason);
        return; // don't retry an auth failure forever
      }
      scheduleReconnect();
    };

    ws.onerror = function (err) {
      console.log("[satsandesh-ws] error", err);
    };
  }

  els.joinButton.addEventListener("click", function () {
    const name = els.nameInput.value.trim();
    if (!name) return;
    setStatus("Joining...");
    fetch(GATEWAY_URL + "/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: name }),
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error("session request failed: " + resp.status);
        return resp.json();
      })
      .then(function (data) {
        token = data.token;
        myUserId = data.user_id;
        console.log("[satsandesh-ws] session issued for", myUserId);
        els.joinPanel.style.display = "none";
        els.chatPanel.style.display = "block";
        connect();
      })
      .catch(function (err) {
        console.log("[satsandesh-ws] session request failed:", err);
        setStatus("Could not reach gateway: " + err.message);
      });
  });

  function send() {
    const body = els.chatInput.value.trim();
    if (!body || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ body: body }));
    els.chatInput.value = "";
  }

  els.sendButton.addEventListener("click", send);
  els.chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") send();
  });

  window.addEventListener("beforeunload", function () {
    intentionalClose = true;
    if (ws) ws.close();
  });

  console.log("[satsandesh-ws] client initialized, gateway =", GATEWAY_URL);
})();
}
"""

CLIENT_JS = CLIENT_JS_TEMPLATE % {"gateway_url": json.dumps(GATEWAY_PUBLIC_URL)}


def index() -> rx.Component:
    return rx.el.div(
        rx.el.h1(
            "SatSandesh — Elder Chat",
            style={"font-size": "24px", "margin-bottom": "4px"},
        ),
        rx.el.p(
            "Placeholder test client (Week 4 WebSocket wiring proof) — "
            "not the real UI shell.",
            style={"font-size": "13px", "color": "#64748b", "margin-bottom": "16px"},
        ),
        rx.el.div(
            rx.el.input(
                id="display-name-input",
                placeholder="Your name",
                style={
                    "font-size": "18px",
                    "padding": "10px",
                    "margin-right": "8px",
                    "border": "1px solid #cbd5e1",
                    "border-radius": "8px",
                },
            ),
            rx.el.button(
                "Join",
                id="join-button",
                style={
                    "font-size": "18px",
                    "padding": "10px 20px",
                    "border-radius": "8px",
                    "background": "#2563eb",
                    "color": "white",
                    "border": "none",
                    "cursor": "pointer",
                },
            ),
            id="join-panel",
        ),
        rx.el.div(
            "Not connected",
            id="status-line",
            style={"font-size": "14px", "color": "#475569", "margin": "12px 0"},
        ),
        rx.el.div(
            rx.el.div(
                id="chat-messages",
                style={
                    "border": "1px solid #e2e8f0",
                    "border-radius": "8px",
                    "padding": "12px",
                    "height": "320px",
                    "overflow-y": "auto",
                    "margin-bottom": "10px",
                },
            ),
            rx.el.div(
                rx.el.input(
                    id="chat-input",
                    placeholder="Type a message...",
                    style={
                        "font-size": "18px",
                        "padding": "10px",
                        "margin-right": "8px",
                        "border": "1px solid #cbd5e1",
                        "border-radius": "8px",
                        "width": "60%",
                    },
                ),
                rx.el.button(
                    "Send",
                    id="send-button",
                    style={
                        "font-size": "18px",
                        "padding": "10px 20px",
                        "border-radius": "8px",
                        "background": "#16a34a",
                        "color": "white",
                        "border": "none",
                        "cursor": "pointer",
                    },
                ),
            ),
            id="chat-panel",
            style={"display": "none"},
        ),
        id="satsandesh-root",
        on_mount=rx.call_script(CLIENT_JS),
        style={
            "max-width": "640px",
            "margin": "40px auto",
            "font-family": "system-ui, sans-serif",
            "padding": "0 16px",
        },
    )


app = rx.App()
app.add_page(index, route="/")
