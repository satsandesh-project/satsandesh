"""The elder's listen screen (`/satsang/<id>`): one big button and the few words that matter.

What it can know and what it does not is in `satsang_common.py`. This file is the State that follows
the list and the player, and the page. Two things are deliberate:

- **The tap is not a Reflex event.** The big button's `onClick` is the browser's own
  (`window.satsang.play()`, from assets/satsang_player.js), because iOS only starts audio that is
  requested during the touch itself, and a round trip to this server would lose it. Python learns
  what happened by reading `window.satsang.status()` once a second.
- **The audio element is always in the page** (outside every conditional), carrying the stream
  address in `data-url`; if it were rendered inside a branch, a state change could unmount it while
  the sound is playing.

The session id comes from the address (`/satsang/<id>`) and is checked before use; the elder's own
id is read by the browser script and only ever sent as the Authorization header.
"""

from __future__ import annotations

import json
import re
from typing import Any

import reflex as rx

from elder_app.pages.satsang_common import (
    COLOR,
    JS_STATUS,
    JS_STOP,
    LIST_EVERY_TICKS,
    LISTEN_TICK_MS,
    TEXTS,
    js_load_sessions,
    parse_load,
    phase_for,
    text_for,
)

ROUTE = "/satsang/[session_id]"
TITLE = "Satsang"
FONT = "Mulish, 'Noto Sans Telugu', system-ui, sans-serif"

_VALID_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_PLAYERS = {"idle", "connecting", "playing", "stalled", "failed"}

_LISTENING = {
    "connecting",
    "connecting_long",
    "playing",
    "stalled_soft",
    "stalled_drop",
    "stalled_long",
}
_OFFER_PLAY = {"live_ready", "failed_unreachable", "failed_blocked", "failed_other"}
_LIVE_PHASES = _LISTENING | _OFFER_PLAY | {"failed_unsupported"}
_OVER = {"ended", "cancelled"}


class SatsangListenState(rx.State):
    """What the screen shows: the satsang's public fields and the player's three, nothing else."""

    lang: str = "en"
    t: dict[str, str] = TEXTS["en"]
    load_state: str = "loading"  # loading | ready | error
    load_error: str = ""
    item: dict[str, Any] = {}
    listed: bool = False  # the latest list holds this satsang
    seen_live: bool = False
    player: str = "idle"
    waiting_s: int = 0
    error_kind: str = ""
    phase: str = "loading"
    title: str = TEXTS["en"]["loading_title"]
    body: str = TEXTS["en"]["loading_body"]
    stream_url: str = ""
    late: bool = False
    show_play: bool = False
    show_stop: bool = False
    show_notes: bool = False
    show_session: bool = False
    show_retry: bool = False
    show_check: bool = False
    valid_id: bool = False
    _sid: str = ""
    _ticks: int = 0

    def open_session(self, session_id: str) -> None:
        """Say which satsang the address names. Anything that is not a plain id is refused here,
        once, so no other code has to check it again."""
        self.valid_id = bool(_VALID_ID.fullmatch(session_id))
        self._sid = session_id if self.valid_id else ""

    # -- loading and polling ----------------------------------------------------------------

    def boot(self):
        self.open_session(str(self.router._page.params.get("session_id", "")))
        if not self.valid_id:
            self.load_state = "ready"
            self.listed = False
            self._show()
            return None
        return self.check_again()

    def check_again(self):
        return rx.call_script(js_load_sessions(), callback=SatsangListenState.after_load)

    def tick(self, _now: str = ""):
        if not self.valid_id:
            return None
        self._ticks += 1
        events = [rx.call_script(JS_STATUS, callback=SatsangListenState.after_status)]
        if self._ticks % LIST_EVERY_TICKS == 0:
            events.append(self.check_again())
        return events

    def stop(self):
        self.player, self.waiting_s, self.error_kind = "idle", 0, ""
        self._show()
        return rx.call_script(JS_STOP)

    def after_load(self, raw: str):
        loaded = parse_load(raw)
        self.lang = loaded["lang"]
        self.t = text_for(self.lang)
        if loaded["ok"]:
            self.load_state, self.load_error = "ready", ""
            found = next((i for i in loaded["items"] if i["id"] == self._sid), None)
            self.listed = found is not None
            if found is not None:
                self.item = found
                self.stream_url = found["stream_url"] or self.stream_url
                self.late = found["late"]
                if found["status"] == "live":
                    self.seen_live = True
        else:
            self.load_state, self.load_error = "error", loaded["error"]
        self._show()
        if self.phase in _OVER and self.player != "idle":
            return self.stop()
        return None

    def after_status(self, raw: str):
        try:
            data = json.loads(raw)
            status = data["status"]
            waiting = int(data["waiting_s"])
            kind = str(data.get("error_kind", ""))
        except (TypeError, ValueError, KeyError):
            return
        self.player = status if status in _PLAYERS else "idle"
        self.waiting_s = max(0, waiting)
        self.error_kind = kind
        self._show()

    # -- from the facts to what is on the screen --------------------------------------------

    def _show(self) -> None:
        status = self.item.get("status") if self.listed else None
        self.phase = phase_for(
            load_state=self.load_state,
            load_error=self.load_error,
            item_status=status,
            seen_live=self.seen_live,
            player=self.player,
            waiting_s=self.waiting_s,
            error_kind=self.error_kind,
        )
        words = text_for(self.lang)
        self.title = words[f"{self.phase}_title"]
        body = words[f"{self.phase}_body"]
        if self.phase == "scheduled":
            body = body.format(
                speaker=self.item.get("speaker_name") or words["speaker"],
                when=self.item.get("when", ""),
            )
        self.body = body
        self.show_play = self.phase in _OFFER_PLAY
        self.show_stop = self.phase in _LISTENING
        self.show_notes = self.phase in _LIVE_PHASES
        self.show_session = bool(self.item) and self.phase not in {
            "loading",
            "need_identity",
            "load_error",
            "gone",
        }
        self.show_retry = self.phase == "load_error"
        self.show_check = self.phase == "stalled_long"


S = SatsangListenState


def play_button() -> rx.Component:
    """The big button. Its click is the browser's own (see the module docstring)."""
    return rx.button(
        rx.vstack(
            rx.text("▶", style={"font_size": "64px", "line_height": "1"}),
            rx.text(S.t["play_label"], style={"font_size": "36px", "font_weight": "800"}),
            spacing="2",
            align="center",
        ),
        type="button",
        custom_attrs={"onClick": rx.Var("() => window.satsang.play()")},
        style={
            "min_height": "200px",
            "min_width": "200px",
            "width": "100%",
            "max_width": "420px",
            "font_size": "36px",
            "font_weight": "800",
            "border_radius": "32px",
            "cursor": "pointer",
            "background": COLOR["primary"],
            "color": COLOR["white"],
        },
    )


def stop_button() -> rx.Component:
    return rx.button(
        S.t["stop_label"],
        type="button",
        custom_attrs={"onClick": rx.Var("() => window.satsang.stop()")},
        style=_quiet_button(),
    )


def _quiet_button() -> dict[str, str]:
    return {
        "min_height": "88px",
        "min_width": "200px",
        "font_size": "28px",
        "font_weight": "800",
        "border_radius": "20px",
        "cursor": "pointer",
        "background": COLOR["card"],
        "color": COLOR["ink"],
        "border": f"3px solid {COLOR['ink']}",
    }


def _text_button(label, on_click) -> rx.Component:
    return rx.button(label, on_click=on_click, type="button", style=_quiet_button())


def _session_card() -> rx.Component:
    return rx.box(
        rx.text(S.item["title"], style={"font_size": "30px", "font_weight": "800"}),
        rx.text(S.item["speaker_name"], style={"font_size": "24px", "color": COLOR["muted"]}),
        style={
            "width": "100%",
            "box_sizing": "border-box",
            "padding": "16px 20px",
            "border_radius": "16px",
            "background": COLOR["card"],
            "border": f"2px solid {COLOR['border']}",
        },
    )


def _notes() -> rx.Component:
    return rx.vstack(
        rx.cond(S.late, rx.text(S.t["late_note"], style={"font_size": "22px"}), rx.fragment()),
        rx.text(S.t["delay_note"], style={"font_size": "22px"}),
        spacing="2",
        width="100%",
        style={
            "padding": "16px 20px",
            "border_radius": "16px",
            "background": COLOR["gold"],
            "color": COLOR["ink"],
            "box_sizing": "border-box",
        },
    )


def page() -> rx.Component:
    return rx.box(
        rx.script(src="/vendor/hls.min.js"),
        rx.script(src="/satsang_player.js"),
        rx.el.audio(
            id="satsang-audio",
            custom_attrs={"data-url": S.stream_url},
            style={"display": "none"},
        ),
        rx.moment(interval=LISTEN_TICK_MS, on_change=S.tick, style={"display": "none"}),
        rx.vstack(
            rx.link(
                S.t["back_to_list"],
                href="/satsang",
                style={"font_size": "24px", "color": COLOR["primary"], "font_weight": "700"},
            ),
            rx.cond(S.show_session, _session_card(), rx.fragment()),
            rx.heading(
                S.title,
                as_="h1",
                style={"font_size": "36px", "color": COLOR["ink"], "margin": "0"},
            ),
            rx.text(S.body, style={"font_size": "26px", "line_height": "1.4"}),
            rx.cond(S.show_play, play_button(), rx.fragment()),
            rx.cond(S.show_stop, stop_button(), rx.fragment()),
            rx.cond(S.show_check, _text_button(S.t["check_again"], S.check_again), rx.fragment()),
            rx.cond(S.show_retry, _text_button(S.t["try_again"], S.check_again), rx.fragment()),
            rx.cond(S.show_notes, _notes(), rx.fragment()),
            spacing="5",
            align="center",
            width="100%",
            style={"max_width": "560px", "margin": "0 auto", "padding": "24px 16px 64px"},
        ),
        on_mount=S.boot,
        style={
            "min_height": "100vh",
            "background": COLOR["canvas"],
            "font_family": FONT,
            "color": COLOR["ink"],
        },
    )
