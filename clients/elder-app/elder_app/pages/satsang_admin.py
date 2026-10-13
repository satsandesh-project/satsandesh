"""The satsang admin page (Week 9, M2): schedule a satsang, see what is coming and what was, get a
speaker's stream key, and end a satsang that is on the air.

It lives in its own file with its own route (`/satsang-admin`) and its own State, and imports
nothing from elder_app.py: elder_app.py gains only the import and the add_page line, so this
feature cannot collide with changes made there. It is an administrator's page, not an elder's:
there is deliberately no link to it from the elder screens, and it is in English.

How it talks to the gateway: like the rest of the app, from the browser (`fetch` through
rx.call_script), against GATEWAY_PUBLIC_URL, which in the compose stack is Caddy. The gateway
decides who may do what; this page only avoids offering what cannot work.

Two secrets pass through this page, and neither is ever in `State`:

  - the administrator's TOKEN. It is typed into an uncontrolled field, copied by the browser script
    into sessionStorage (gone when the tab closes, never kept across visits), and sent only as the
    Authorization header. Reflex state lives in the server's memory and is sent to the browser
    over a WebSocket, so anything in State is in the elder-app container too.
  - a STREAM KEY (inside the ingest URL). The gateway returns it once, on scheduling or on a new
    key.
    The browser script writes it straight into one element on the page, masked, and the Python side
    is told only "it worked" and the satsang's title. It wipes itself after two minutes, on "hide",
    on sign-out and on every page load. There is no way to show it again: the gateway stores only
    its hash, so a lost key is replaced, not recovered.

User ids are also treated with care: in AUTH_MODE=legacy a user id is a bearer credential, so the
list the browser script hands to Python is stripped of every id but the satsang's own, and
`shape_sessions` strips again.
"""

import json
import os
from datetime import datetime
from typing import Any

import reflex as rx

ROUTE = "/satsang-admin"
TITLE = "Satsang admin"

GATEWAY_PUBLIC_URL = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost")

# The palette of the elder app (same hex values), kept here so this file stands alone.
COLOR = {
    "canvas": "#F7F0E3",
    "card": "#FFFCF6",
    "ink": "#2A2118",
    "muted": "#4A3F2E",
    "primary": "#8A3E0F",  # white text: about 8:1
    "danger": "#8A1C1C",  # white text: about 10:1
    "green": "#1F4034",  # white text: about 11:1
    "border": "#C9B58E",
    "ok_bg": "#EAF1EC",
    "ok_ink": "#1F4034",
    "err_bg": "#FBE9E7",
    "err_ink": "#6B1111",
    "secret_bg": "#FFF3D6",
    "secret_border": "#8A1C1C",
    "white": "#FFFFFF",
}

# (what it is, text colour, background, minimum contrast ratio). The tests compute each ratio
# from the hex values: body text is held to 7:1 (WCAG AAA), large bold text to 4.5:1.
CONTRAST_PAIRS = [
    ("body text on a card", COLOR["ink"], COLOR["card"], 7.0),
    ("body text on the canvas", COLOR["ink"], COLOR["canvas"], 7.0),
    ("secondary text on a card", COLOR["muted"], COLOR["card"], 7.0),
    ("primary button", COLOR["white"], COLOR["primary"], 7.0),
    ("danger button and LIVE badge", COLOR["white"], COLOR["danger"], 7.0),
    ("scheduled badge", COLOR["white"], COLOR["green"], 7.0),
    ("ended and cancelled badge", COLOR["white"], COLOR["muted"], 7.0),
    ("success notice", COLOR["ok_ink"], COLOR["ok_bg"], 7.0),
    ("error notice", COLOR["err_ink"], COLOR["err_bg"], 7.0),
    ("secret panel text", COLOR["ink"], COLOR["secret_bg"], 7.0),
    ("secret panel warning", COLOR["danger"], COLOR["secret_bg"], 4.5),
    ("quiet button text on a card", COLOR["ink"], COLOR["card"], 7.0),
]

BUTTON_MIN_HEIGHT = "64px"
INPUT_MIN_HEIGHT = "60px"
BASE_FONT = "20px"

# ---------------------------------------------------------------------------------------------
# What the browser does. Each function returns the JavaScript for one rx.call_script; every script
# answers with ONE JSON string: {"ok", "status", "detail", "data"}. `status` 0 means "no request
# was made or none got through"; `detail` is a short code or the gateway's own plain-English
# detail string, never a token or a key. Run for real under Node by tests/test_satsang_admin.py.
# ---------------------------------------------------------------------------------------------

_JS_COMMON = """
const GATEWAY = __GATEWAY__;
const TOKEN_KEY = "satsang_admin_token";
const MASK = "\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const reply = (ok, status, detail, data) =>
    JSON.stringify({
        ok: ok, status: status, detail: detail || "", data: data === undefined ? null : data,
    });
const field = (id) => document.getElementById(id);
const fieldValue = (id) => {
    const el = field(id);
    return el ? String(el.value || "").trim() : "";
};
const clearField = (id) => { const el = field(id); if (el) el.value = ""; };
const wipeSecret = (message) => {
    const el = field("satsang-secret-url");
    if (el) { delete el.dataset.value; el.dataset.shown = "0"; el.textContent = message || ""; }
    const label = field("satsang-secret-toggle");
    if (label) label.textContent = "Show";
};
const showSecret = (url) => {
    const el = field("satsang-secret-url");
    if (!el) return false;
    el.dataset.value = url;
    el.dataset.shown = "0";
    el.textContent = MASK;
    const label = field("satsang-secret-toggle");
    if (label) label.textContent = "Show";
    setTimeout(() => wipeSecret("Hidden for safety. Get a new key if you still need one."), 120000);
    return true;
};
const request = async (method, path, body) => {
    const token = sessionStorage.getItem(TOKEN_KEY) || "";
    if (!token) return { ok: false, status: 0, detail: "no_token", payload: null };
    const headers = { Authorization: "Bearer " + token };
    if (body) headers["Content-Type"] = "application/json";
    try {
        const resp = await fetch(GATEWAY + path, {
            method: method,
            headers: headers,
            body: body ? JSON.stringify(body) : undefined,
        });
        let payload = null;
        try { payload = await resp.json(); } catch (e) { payload = null; }
        const detail = payload && typeof payload.detail === "string" ? payload.detail : "";
        return { ok: resp.ok, status: resp.status, detail: detail, payload: payload };
    } catch (e) {
        return { ok: false, status: 0, detail: "network", payload: null };
    }
};
"""

_JS_BOOT = """
wipeSecret("");
return reply(true, 200, "", { signed_in: !!sessionStorage.getItem(TOKEN_KEY) });
"""

_JS_SIGN_IN = """
const token = fieldValue("satsang-token");
if (!token) return reply(false, 0, "empty");
sessionStorage.setItem(TOKEN_KEY, token);
clearField("satsang-token");
return reply(true, 200);
"""

_JS_SIGN_OUT = """
sessionStorage.removeItem(TOKEN_KEY);
wipeSecret("");
return reply(true, 200);
"""

_JS_LOAD = """
const r = await request("GET", "/sessions/manage");
if (!r.ok) return reply(false, r.status, r.detail);
const items = r.payload && Array.isArray(r.payload.items) ? r.payload.items : [];
const fmt = new Intl.DateTimeFormat(undefined, {
    weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
});
const rows = items.map((i) => {
    const d = new Date(i.scheduled_start_at);
    return {
        id: i.id, title: i.title, speaker_name: i.speaker_name, status: i.status,
        scheduled_start_at: i.scheduled_start_at, when: isNaN(d.getTime()) ? "" : fmt.format(d),
    };
});
return reply(true, 200, "", rows);
"""

_JS_SCHEDULE = """
const title = fieldValue("satsang-title");
const speaker = fieldValue("satsang-speaker");
const whenRaw = fieldValue("satsang-when");
if (!title || title.length > 120) return reply(false, 0, "invalid:title");
if (!UUID.test(speaker)) return reply(false, 0, "invalid:speaker");
if (!/^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}/.test(whenRaw)) return reply(false, 0, "invalid:when");
const when = new Date(whenRaw);
if (isNaN(when.getTime())) return reply(false, 0, "invalid:when");
const r = await request("POST", "/sessions", {
    title: title, speaker_user_id: speaker, scheduled_start_at: when.toISOString(),
});
if (!r.ok) return reply(false, r.status, r.detail);
const p = r.payload;
if (!p || !p.session || typeof p.ingest_url !== "string") {
    return reply(false, r.status, "bad_response");
}
const shown = showSecret(p.ingest_url);
["satsang-title", "satsang-speaker", "satsang-when"].forEach(clearField);
return reply(true, 201, shown ? "" : "secret_missing", {
    id: p.session.id, title: p.session.title,
});
"""

_JS_GET_KEY = """
const sid = __SID__ || fieldValue("satsang-key-session");
if (!UUID.test(sid)) return reply(false, 0, "invalid:session");
const r = await request("POST", "/sessions/" + sid + "/key");
if (!r.ok) return reply(false, r.status, r.detail);
const p = r.payload;
if (!p || !p.session || typeof p.ingest_url !== "string") {
    return reply(false, r.status, "bad_response");
}
const shown = showSecret(p.ingest_url);
clearField("satsang-key-session");
return reply(true, 200, shown ? "" : "secret_missing", {
    id: p.session.id, title: p.session.title,
});
"""

_JS_ACT = """
const sid = __SID__;
if (!UUID.test(sid)) return reply(false, 0, "invalid:session");
const r = await request("POST", "/sessions/" + sid + "/" + __ACTION__);
return r.ok ? reply(true, r.status, "", { id: sid }) : reply(false, r.status, r.detail);
"""

_JS_TOGGLE_SECRET = """
const el = field("satsang-secret-url");
const label = field("satsang-secret-toggle");
if (!el || !el.dataset.value) return reply(false, 0, "nothing");
if (el.dataset.shown === "1") {
    el.dataset.shown = "0"; el.textContent = MASK; if (label) label.textContent = "Show";
} else {
    el.dataset.shown = "1";
    el.textContent = el.dataset.value;
    if (label) label.textContent = "Hide";
}
return reply(true, 200);
"""

_JS_HIDE_SECRET = """
wipeSecret("");
return reply(true, 200);
"""

_JS_COPY_SECRET = """
const el = field("satsang-secret-url");
if (!el || !el.dataset.value) return reply(false, 0, "copy_failed");
try {
    await navigator.clipboard.writeText(el.dataset.value);
    return reply(true, 200);
} catch (e) {
    return reply(false, 0, "copy_failed");
}
"""


def _script(body: str, **literals: str) -> str:
    source = (_JS_COMMON + body).replace("__GATEWAY__", json.dumps(GATEWAY_PUBLIC_URL))
    for name, value in literals.items():
        source = source.replace(f"__{name.upper()}__", value)
    return "(async () => {\n" + source + "\n})()"


def js_boot() -> str:
    return _script(_JS_BOOT)


def js_sign_in() -> str:
    return _script(_JS_SIGN_IN)


def js_sign_out() -> str:
    return _script(_JS_SIGN_OUT)


def js_load() -> str:
    return _script(_JS_LOAD)


def js_schedule() -> str:
    return _script(_JS_SCHEDULE)


def js_get_key(session_id: str | None) -> str:
    """A new key for this satsang, or for the one named in the page's own field when `session_id` is
    None. The id is checked to be a UUID in the browser before it goes into a URL."""
    return _script(_JS_GET_KEY, sid=json.dumps(session_id) if session_id else "null")


def js_act(session_id: str, action: str) -> str:
    if action not in ("end", "cancel"):
        raise ValueError(f"unknown action {action!r}")
    return _script(_JS_ACT, sid=json.dumps(session_id), action=json.dumps(action))


def js_toggle_secret() -> str:
    return _script(_JS_TOGGLE_SECRET)


def js_hide_secret() -> str:
    return _script(_JS_HIDE_SECRET)


def js_copy_secret() -> str:
    return _script(_JS_COPY_SECRET)


# ---------------------------------------------------------------------------------------------
# What Python does with the answers
# ---------------------------------------------------------------------------------------------

_STATUS_LABEL = {
    "live": "LIVE NOW",
    "scheduled": "Scheduled",
    "ended": "Ended",
    "cancelled": "Cancelled",
}
_BADGE_BG = {
    "live": COLOR["danger"],
    "scheduled": COLOR["green"],
    "ended": COLOR["muted"],
    "cancelled": COLOR["muted"],
}
_SHOWN_KEYS = ("id", "title", "speaker_name", "status", "scheduled_start_at", "when")


def _decode(result: str) -> dict[str, Any]:
    try:
        parsed = json.loads(result)
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict):
        return {"ok": False, "status": 0, "detail": "bad_response", "data": None}
    return {
        "ok": bool(parsed.get("ok")),
        "status": parsed.get("status") if isinstance(parsed.get("status"), int) else 0,
        "detail": parsed.get("detail") if isinstance(parsed.get("detail"), str) else "",
        "data": parsed.get("data"),
    }


def _start(item: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(item["scheduled_start_at"]))
    except (KeyError, ValueError):
        return datetime.min.astimezone()


def shape_sessions(raw: str | list) -> tuple[list[dict], list[dict]]:
    """(upcoming and live, past) rows for the screen, from what the browser script returned.

    Keeps only the fields the screen shows, so even a script that leaked a user id or anything else
    could not put it into State. Anything unreadable is skipped, not an error."""
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return [], []
    if not isinstance(items, list):
        return [], []
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict) or item.get("status") not in _STATUS_LABEL:
            continue
        if not isinstance(item.get("id"), str) or not isinstance(item.get("title"), str):
            continue
        status = item["status"]
        shown = {k: str(item.get(k) or "") for k in _SHOWN_KEYS}
        shown["when"] = shown["when"] or shown["scheduled_start_at"]
        shown["status_label"] = _STATUS_LABEL[status]
        shown["badge_bg"] = _BADGE_BG[status]
        shown["can_end"] = status == "live"
        shown["can_cancel"] = status == "scheduled"
        shown["can_key"] = status == "scheduled"
        shown["_start"] = _start(item)
        rows.append(shown)
    upcoming = [r for r in rows if r["status"] in ("live", "scheduled")]
    past = [r for r in rows if r["status"] in ("ended", "cancelled")]
    upcoming.sort(key=lambda r: (r["status"] != "live", r["_start"]))
    past.sort(key=lambda r: r["_start"], reverse=True)
    for r in rows:
        del r["_start"]
    return upcoming, past


def describe_failure(op: str, result: dict[str, Any]) -> str:
    """One plain sentence for a failed call. Never repeats what the server said beyond a fixed
    reading of it, so a token or anything odd in a response cannot reach the screen."""
    status, detail = result.get("status", 0), result.get("detail", "")
    if detail == "no_token":
        return "Please sign in first: paste your administrator token."
    if detail == "empty":
        return "Paste your administrator token first."
    if detail == "network":
        return "Could not reach the server. Check the connection and try again."
    if detail == "invalid:title":
        return "Give the satsang a title of 1 to 120 characters."
    if detail == "invalid:speaker":
        return "The speaker must be given as a user id: a long code of letters, digits and dashes."
    if detail == "invalid:when":
        return "Choose a date and time for the satsang."
    if detail == "invalid:session":
        return "That is not a session id. Paste the whole code."
    if detail == "copy_failed":
        return "Could not copy. Press Show and copy the address by hand."
    if status == 401:
        return "Your token was not accepted. Sign in again with a fresh token."
    if status == 403:
        return "This token is not for an administrator or moderator."
    if op == "schedule" and status == 404:
        return "No such speaker. Check the user id."
    if op == "schedule" and status == 422:
        if "past" in detail:
            return "That start time is in the past. Choose a later time."
        return "Please check the title, speaker and time."
    if op == "key" and status == 404:
        return "No such satsang, or it is not yours."
    if op == "key" and status == 409:
        return "This satsang is already live, finished or cancelled, so it cannot have a new key."
    if op in ("end", "cancel") and status == 404:
        return "No such satsang."
    if op == "end" and status == 409:
        return "This satsang cannot be ended: it is not live."
    if op == "cancel" and status == 409:
        return "This satsang cannot be cancelled: it is no longer waiting to start."
    return f"Something went wrong on the server (code {status}). Try again in a moment."


class SatsangAdminState(rx.State):
    """What the screen shows. Holds no token and no key: see the module docstring."""

    signed_in: bool = False
    notice: str = ""
    notice_is_error: bool = False
    upcoming: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
    # The panel that shows a stream key is on screen. The key itself is only in the browser.
    secret_open: bool = False
    secret_for: str = ""
    # An end or cancel waiting for its "yes".
    confirm_kind: str = ""
    confirm_id: str = ""
    confirm_title: str = ""
    acting: str = ""

    def _say(self, text: str, *, error: bool = False) -> None:
        self.notice, self.notice_is_error = text, error

    # -- opening the page, signing in and out -----------------------------------------------

    def boot(self):
        return rx.call_script(js_boot(), callback=SatsangAdminState.after_boot)

    def after_boot(self, result: str):
        self.secret_open = False
        self.secret_for = ""
        self.confirm_kind = self.confirm_id = self.confirm_title = ""
        data = _decode(result)["data"]
        self.signed_in = bool(isinstance(data, dict) and data.get("signed_in"))
        return self.load() if self.signed_in else None

    def sign_in(self):
        return rx.call_script(js_sign_in(), callback=SatsangAdminState.after_sign_in)

    def after_sign_in(self, result: str):
        r = _decode(result)
        if not r["ok"]:
            self._say(describe_failure("signin", r), error=True)
            return None
        self.signed_in = True
        self._say("")
        return self.load()

    def sign_out(self):
        self.signed_in = False
        self.secret_open = False
        self.secret_for = ""
        self.confirm_kind = self.confirm_id = self.confirm_title = ""
        self.upcoming, self.past = [], []
        self._say("")
        return rx.call_script(js_sign_out())

    # -- the list ---------------------------------------------------------------------------

    def load(self):
        return rx.call_script(js_load(), callback=SatsangAdminState.after_load)

    def after_load(self, result: str):
        r = _decode(result)
        if r["ok"]:
            self.upcoming, self.past = shape_sessions(r["data"])
            self._say("")
            return None
        self._say(describe_failure("load", r), error=True)
        if r["status"] == 401:
            # the token is wrong: forget it, so a reload does not look signed in
            self.signed_in = False
            self.upcoming, self.past = [], []
            return rx.call_script(js_sign_out())
        return None

    # -- scheduling and keys ----------------------------------------------------------------

    def schedule(self):
        return rx.call_script(js_schedule(), callback=SatsangAdminState.after_schedule)

    def after_schedule(self, result: str):
        r = _decode(result)
        if not r["ok"]:
            self._say(describe_failure("schedule", r), error=True)
            return None
        data = r["data"] if isinstance(r["data"], dict) else {}
        title = str(data.get("title") or "")
        if r["detail"] == "secret_missing":
            self._say(
                f"Scheduled “{title}”, but its stream key could not be shown. "
                "Use “Get a new key” for it.",
                error=True,
            )
        else:
            self.secret_open, self.secret_for = True, title
            self._say(f"Scheduled “{title}”. Copy its stream address below now: it is shown once.")
        return self.load()

    def get_key_for(self, session_id: str):
        return rx.call_script(js_get_key(session_id), callback=SatsangAdminState.after_key)

    def get_my_key(self):
        return rx.call_script(js_get_key(None), callback=SatsangAdminState.after_key)

    def after_key(self, result: str):
        r = _decode(result)
        if not r["ok"]:
            self._say(describe_failure("key", r), error=True)
            return None
        data = r["data"] if isinstance(r["data"], dict) else {}
        if r["detail"] == "secret_missing":
            self._say("A new key was made but could not be shown. Ask for another.", error=True)
            return None
        self.secret_open, self.secret_for = True, str(data.get("title") or "")
        self._say("A new key is ready below. The old one no longer works.")
        return None

    def hide_secret(self):
        self.secret_open = False
        self.secret_for = ""
        return rx.call_script(js_hide_secret())

    def copy_secret(self):
        return rx.call_script(js_copy_secret(), callback=SatsangAdminState.after_copy)

    def after_copy(self, result: str):
        r = _decode(result)
        if r["ok"]:
            self._say("Address copied. Paste it into the broadcasting app.")
        else:
            self._say(describe_failure("copy", r), error=True)

    # -- ending and cancelling, each after a "yes" -------------------------------------------

    def ask_end(self, session_id: str, title: str):
        self.confirm_kind, self.confirm_id, self.confirm_title = "end", session_id, title

    def ask_cancel(self, session_id: str, title: str):
        self.confirm_kind, self.confirm_id, self.confirm_title = "cancel", session_id, title

    def dismiss_confirm(self):
        self.confirm_kind = self.confirm_id = self.confirm_title = ""

    def confirm(self):
        kind, session_id = self.confirm_kind, self.confirm_id
        self.confirm_kind = self.confirm_id = self.confirm_title = ""
        if kind not in ("end", "cancel") or not session_id:
            return None
        self.acting = kind
        return rx.call_script(js_act(session_id, kind), callback=SatsangAdminState.after_action)

    def after_action(self, result: str):
        r = _decode(result)
        if not r["ok"]:
            self._say(describe_failure(self.acting, r), error=True)
            return None
        self._say(
            "The satsang has been ended."
            if self.acting == "end"
            else "The satsang has been cancelled."
        )
        return self.load()


# ---------------------------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------------------------

S = SatsangAdminState

_BUTTON_KINDS = {
    "primary": {
        "background": COLOR["primary"],
        "color": COLOR["white"],
        "border": "2px solid transparent",
    },
    "danger": {
        "background": COLOR["danger"],
        "color": COLOR["white"],
        "border": "2px solid transparent",
    },
    "quiet": {
        "background": "transparent",
        "color": COLOR["ink"],
        "border": f"2px solid {COLOR['ink']}",
    },
}


def big_button(label, on_click, kind: str = "primary", **props) -> rx.Component:
    """A tap target at least 64px tall in 20px bold type. Always words, never an icon alone."""
    return rx.button(
        label,
        on_click=on_click,
        type="button",
        style={
            "min_height": BUTTON_MIN_HEIGHT,
            "font_size": BASE_FONT,
            "font_weight": "700",
            "padding": "0 24px",
            "border_radius": "14px",
            "cursor": "pointer",
            **_BUTTON_KINDS[kind],
        },
        **props,
    )


_INPUT_STYLE = {
    "min_height": INPUT_MIN_HEIGHT,
    "font_size": BASE_FONT,
    "padding": "0 16px",
    "border_radius": "12px",
    "border": f"2px solid {COLOR['ink']}",
    "background": COLOR["white"],
    "color": COLOR["ink"],
    "width": "100%",
    "box_sizing": "border-box",
}


def labelled_input(ident: str, label: str, help_text: str = "", **props) -> rx.Component:
    """An uncontrolled field, read by id in the browser: nothing typed here reaches State."""
    return rx.vstack(
        rx.el.label(
            label,
            html_for=ident,
            style={"font_size": BASE_FONT, "font_weight": "700", "color": COLOR["ink"]},
        ),
        rx.cond(
            help_text != "",
            rx.text(help_text, style={"font_size": "18px", "color": COLOR["muted"]}),
            rx.fragment(),
        ),
        rx.el.input(id=ident, name=ident, style=_INPUT_STYLE, **props),
        spacing="2",
        width="100%",
        align="start",
    )


def card(*children, **props) -> rx.Component:
    return rx.vstack(
        *children,
        spacing="4",
        width="100%",
        align="start",
        style={
            "padding": "24px",
            "background": COLOR["card"],
            "border": f"2px solid {COLOR['border']}",
            "border_radius": "20px",
            "box_sizing": "border-box",
        },
        **props,
    )


def heading(text: str) -> rx.Component:
    return rx.heading(
        text, as_="h2", style={"font_size": "28px", "color": COLOR["ink"], "margin": "0"}
    )


def body(*children, **style) -> rx.Component:
    return rx.text(
        *children,
        style={"font_size": BASE_FONT, "color": COLOR["ink"], "line_height": "1.5", **style},
    )


def banner() -> rx.Component:
    def shown(bg: str, ink: str) -> rx.Component:
        return rx.box(
            S.notice,
            role="status",
            aria_live="polite",
            style={
                "width": "100%",
                "padding": "16px 20px",
                "border_radius": "14px",
                "font_size": BASE_FONT,
                "font_weight": "700",
                "background": bg,
                "color": ink,
                "border": f"2px solid {ink}",
                "box_sizing": "border-box",
            },
        )

    return rx.cond(
        S.notice != "",
        rx.cond(
            S.notice_is_error,
            shown(COLOR["err_bg"], COLOR["err_ink"]),
            shown(COLOR["ok_bg"], COLOR["ok_ink"]),
        ),
        rx.fragment(),
    )


def sign_in_card() -> rx.Component:
    return card(
        heading("Sign in"),
        body(
            "Paste your administrator token. It stays in this browser tab only and is forgotten "
            "when you close the tab."
        ),
        labelled_input(
            "satsang-token",
            "Administrator token",
            type="password",
            auto_complete="off",
            spell_check=False,
        ),
        big_button("Use this token", S.sign_in),
    )


def secret_panel() -> rx.Component:
    """Always in the page, shown or hidden by `display`, so the script that fills it never runs
    before it exists."""
    return rx.box(
        rx.vstack(
            rx.text(
                "This is a secret",
                style={"font_size": "28px", "font_weight": "800", "color": COLOR["danger"]},
            ),
            body(
                "Anyone who has it can broadcast as the speaker. Show it only to the speaker, "
                "do not send it in a chat or take a picture of it, and hide it as soon as it "
                "is copied.",
                font_weight="700",
            ),
            body(rx.text.span("For: "), rx.text.span(S.secret_for, style={"font_weight": "800"})),
            body(
                "The address below contains the key. It is shown once and cannot be looked up "
                "again: if it is lost, get a new key (the old one then stops working).",
                color=COLOR["muted"],
            ),
            rx.el.div(
                id="satsang-secret-url",
                aria_live="polite",
                style={
                    "font_family": "ui-monospace, Menlo, monospace",
                    "font_size": BASE_FONT,
                    "font_weight": "700",
                    "word_break": "break-all",
                    "padding": "16px",
                    "min_height": INPUT_MIN_HEIGHT,
                    "background": COLOR["white"],
                    "color": COLOR["ink"],
                    "border": f"2px dashed {COLOR['secret_border']}",
                    "border_radius": "12px",
                    "width": "100%",
                    "box_sizing": "border-box",
                },
            ),
            rx.hstack(
                rx.button(
                    rx.el.span("Show", id="satsang-secret-toggle"),
                    on_click=rx.call_script(js_toggle_secret()),
                    type="button",
                    style={
                        "min_height": BUTTON_MIN_HEIGHT,
                        "font_size": BASE_FONT,
                        "font_weight": "700",
                        "padding": "0 24px",
                        "border_radius": "14px",
                        "cursor": "pointer",
                        **_BUTTON_KINDS["quiet"],
                    },
                ),
                big_button("Copy address", S.copy_secret),
                big_button("I have saved it: hide", S.hide_secret, kind="danger"),
                spacing="3",
                wrap="wrap",
            ),
            spacing="4",
            align="start",
            width="100%",
        ),
        style={
            "display": rx.cond(S.secret_open, "block", "none"),
            "width": "100%",
            "padding": "24px",
            "background": COLOR["secret_bg"],
            "border": f"6px solid {COLOR['secret_border']}",
            "border_radius": "20px",
            "box_sizing": "border-box",
        },
    )


def schedule_card() -> rx.Component:
    return card(
        heading("Schedule a satsang"),
        labelled_input("satsang-title", "Title", max_length=120),
        labelled_input(
            "satsang-speaker",
            "Speaker's user id",
            "The long code that identifies the speaker. There is no list of people to pick yet.",
            spell_check=False,
            auto_complete="off",
        ),
        labelled_input(
            "satsang-when",
            "Date and time",
            "In this device's own time zone.",
            type="datetime-local",
        ),
        big_button("Schedule satsang", S.schedule),
    )


def my_key_card() -> rx.Component:
    return card(
        heading("I am the speaker"),
        body(
            "Paste the code of your satsang to get a new stream key. The old key stops working at "
            "once. Only the satsang's own speaker, or an administrator, can do this."
        ),
        labelled_input(
            "satsang-key-session", "Satsang code", spell_check=False, auto_complete="off"
        ),
        big_button("Get a new stream key", S.get_my_key),
    )


def confirm_box(item: rx.Var[dict]) -> rx.Component:
    return rx.vstack(
        rx.cond(
            S.confirm_kind == "end",
            body(
                rx.text.span("End “"),
                rx.text.span(S.confirm_title),
                rx.text.span("” for everyone who is listening?"),
                font_weight="800",
            ),
            body(
                rx.text.span("Cancel “"),
                rx.text.span(S.confirm_title),
                rx.text.span("”? It will not take place."),
                font_weight="800",
            ),
        ),
        rx.hstack(
            big_button(
                rx.cond(S.confirm_kind == "end", "Yes, end it", "Yes, cancel it"),
                S.confirm,
                kind="danger",
            ),
            big_button("No, keep it", S.dismiss_confirm, kind="quiet"),
            spacing="3",
            wrap="wrap",
        ),
        spacing="3",
        align="start",
        width="100%",
    )


def row_buttons(item: rx.Var[dict]) -> rx.Component:
    return rx.hstack(
        rx.cond(
            item["can_end"],
            big_button("End this satsang", S.ask_end(item["id"], item["title"]), kind="danger"),
            rx.fragment(),
        ),
        rx.cond(
            item["can_key"],
            big_button("Get a new key", S.get_key_for(item["id"]), kind="primary"),
            rx.fragment(),
        ),
        rx.cond(
            item["can_cancel"],
            big_button("Cancel", S.ask_cancel(item["id"], item["title"]), kind="quiet"),
            rx.fragment(),
        ),
        spacing="3",
        wrap="wrap",
    )


def session_row(item: rx.Var[dict]) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text(
                item["status_label"],
                style={
                    "font_size": BASE_FONT,
                    "font_weight": "800",
                    "color": COLOR["white"],
                    "background": item["badge_bg"],
                    "padding": "6px 14px",
                    "border_radius": "10px",
                },
            ),
            rx.text(
                item["when"],
                style={"font_size": BASE_FONT, "font_weight": "700", "color": COLOR["ink"]},
            ),
            spacing="3",
            align="center",
            wrap="wrap",
        ),
        rx.text(
            item["title"], style={"font_size": "24px", "font_weight": "800", "color": COLOR["ink"]}
        ),
        body(
            rx.text.span("Speaker: "),
            rx.text.span(item["speaker_name"], style={"font_weight": "700"}),
        ),
        rx.text(
            rx.text.span("Code: "),
            rx.text.span(item["id"]),
            style={
                "font_size": "18px",
                "color": COLOR["muted"],
                "font_family": "ui-monospace, Menlo, monospace",
                "word_break": "break-all",
            },
        ),
        rx.cond(S.confirm_id == item["id"], confirm_box(item), row_buttons(item)),
        spacing="3",
        align="start",
        width="100%",
        style={
            "padding": "20px",
            "background": COLOR["white"],
            "border": f"2px solid {COLOR['border']}",
            "border_radius": "16px",
            "box_sizing": "border-box",
        },
    )


def session_list(title: str, rows, empty_text: str) -> rx.Component:
    return card(
        heading(title),
        rx.cond(
            rows.length() > 0,
            rx.vstack(rx.foreach(rows, session_row), spacing="4", width="100%"),
            body(empty_text, color=COLOR["muted"]),
        ),
    )


def signed_in_view() -> rx.Component:
    return rx.vstack(
        secret_panel(),
        schedule_card(),
        session_list("Upcoming and live", S.upcoming, "Nothing is scheduled."),
        session_list("Past", S.past, "Nothing yet."),
        my_key_card(),
        spacing="5",
        width="100%",
    )


def page() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.heading(
                    TITLE,
                    as_="h1",
                    style={"font_size": "36px", "color": COLOR["ink"], "margin": "0"},
                ),
                rx.spacer(),
                rx.cond(
                    S.signed_in, big_button("Sign out", S.sign_out, kind="quiet"), rx.fragment()
                ),
                align="center",
                width="100%",
                wrap="wrap",
            ),
            banner(),
            rx.cond(S.signed_in, signed_in_view(), sign_in_card()),
            spacing="5",
            width="100%",
            style={"max_width": "880px", "margin": "0 auto", "padding": "24px 16px 64px"},
        ),
        on_mount=S.boot,
        style={
            "min_height": "100vh",
            "background": COLOR["canvas"],
            "font_size": BASE_FONT,
            "color": COLOR["ink"],
        },
    )
