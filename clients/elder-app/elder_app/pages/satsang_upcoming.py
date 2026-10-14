"""The elder's satsang list (`/satsang`): what is on now and what is coming, each one a big card
that opens its own listen screen (`satsang_listen.py`). Its words, its one request
and its palette come from `satsang_common.py`; read that module's docstring first. It imports
nothing from elder_app.py.

`entry_card()` is the bilingual card on the home screen that leads here: Telugu first, English under
it, so it can be read before the app knows the elder's language.
"""

from __future__ import annotations

from typing import Any

import reflex as rx

from elder_app.pages.satsang_common import (
    COLOR,
    TEXTS,
    TICK_MS,
    UPCOMING_EVERY_TICKS,
    js_load_sessions,
    parse_load,
    text_for,
)

ROUTE = "/satsang"
TITLE = "Satsang"
FONT = "Mulish, 'Noto Sans Telugu', system-ui, sans-serif"


class SatsangUpcomingState(rx.State):
    """What the list shows. No id of a person and no key: only titles, names and times."""

    status: str = "loading"  # loading | ready | error
    lang: str = "en"
    t: dict[str, str] = TEXTS["en"]
    items: list[dict[str, Any]] = []
    empty_text: str = TEXTS["en"]["empty"]
    error_text: str = ""
    error_body: str = ""
    _ticks: int = 0

    def boot(self):
        return rx.call_script(js_load_sessions(), callback=SatsangUpcomingState.after_load)

    def after_load(self, raw: str):
        loaded = parse_load(raw)
        self.lang = loaded["lang"]
        self.t = text_for(self.lang)
        self.empty_text = self.t["empty"]
        if not loaded["ok"]:
            key = "need_identity" if loaded["error"] == "no_identity" else "load_error"
            self.status = "error"
            self.error_text = self.t[f"{key}_title"]
            self.error_body = self.t[f"{key}_body"]
            return
        self.status = "ready"
        self.items = [_card(entry, self.t) for entry in loaded["items"]]

    def tick(self, _now: str = ""):
        self._ticks += 1
        if self._ticks % UPCOMING_EVERY_TICKS:
            return None
        return self.boot()


def _card(entry: dict[str, Any], t: dict[str, str]) -> dict[str, Any]:
    live = entry["status"] == "live"
    cancelled = entry["status"] == "cancelled"
    badge = t["on_now"] if live else t["cancelled_badge"] if cancelled else t["coming_up"]
    return {
        "id": entry["id"],
        "href": f"/satsang/{entry['id']}",
        "title": entry["title"],
        "speaker": entry["speaker_name"],
        "when": entry["when"],
        "live": live,
        "cancelled": cancelled,
        "badge": badge,
        "cta": t["listen"],
    }


S = SatsangUpcomingState


def _badge(item) -> rx.Component:
    return rx.box(
        item["badge"],
        style={
            "display": "inline-block",
            "padding": "6px 16px",
            "border_radius": "999px",
            "font_size": "22px",
            "font_weight": "800",
            "color": COLOR["white"],
            "background": rx.cond(
                item["live"],
                COLOR["live"],
                rx.cond(item["cancelled"], COLOR["muted"], COLOR["green"]),
            ),
        },
    )


def _session_card(item) -> rx.Component:
    return rx.link(
        rx.vstack(
            _badge(item),
            rx.text(item["title"], style={"font_size": "30px", "font_weight": "800"}),
            rx.text(item["speaker"], style={"font_size": "24px", "color": COLOR["muted"]}),
            rx.text(item["when"], style={"font_size": "24px", "color": COLOR["muted"]}),
            rx.cond(
                item["live"],
                rx.text(
                    item["cta"],
                    style={"font_size": "28px", "font_weight": "800", "color": COLOR["primary"]},
                ),
                rx.fragment(),
            ),
            spacing="2",
            align="start",
        ),
        href=item["href"],
        underline="none",
        style={
            "display": "block",
            "min_height": "88px",
            "width": "100%",
            "box_sizing": "border-box",
            "padding": "20px",
            "border_radius": "20px",
            "background": COLOR["card"],
            "color": COLOR["ink"],
            "border": rx.cond(
                item["live"], f"3px solid {COLOR['live']}", f"2px solid {COLOR['border']}"
            ),
        },
    )


def _message(title, body) -> rx.Component:
    return rx.box(
        rx.text(title, style={"font_size": "30px", "font_weight": "800"}),
        rx.text(body, style={"font_size": "24px"}),
        style={
            "padding": "20px",
            "border_radius": "16px",
            "background": COLOR["warn_bg"],
            "color": COLOR["warn_ink"],
        },
    )


def _list() -> rx.Component:
    return rx.cond(
        S.items.length() > 0,
        rx.vstack(rx.foreach(S.items, _session_card), spacing="4", width="100%"),
        rx.text(S.empty_text, style={"font_size": "26px"}),
    )


def page() -> rx.Component:
    return rx.box(
        rx.moment(interval=TICK_MS, on_change=S.tick, style={"display": "none"}),
        rx.vstack(
            rx.link(
                S.t["back_home"],
                href="/",
                style={"font_size": "24px", "color": COLOR["primary"], "font_weight": "700"},
            ),
            rx.heading(
                S.t["upcoming_title"],
                as_="h1",
                style={"font_size": "40px", "color": COLOR["ink"], "margin": "0"},
            ),
            rx.cond(
                S.status == "loading",
                rx.text(S.t["loading_title"], style={"font_size": "26px"}),
                rx.cond(S.status == "error", _message(S.error_text, S.error_body), _list()),
            ),
            spacing="5",
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


def entry_card() -> rx.Component:
    """The card on the home screen. Both languages are always shown."""
    te, en = TEXTS["te"], TEXTS["en"]
    return rx.link(
        rx.vstack(
            rx.text(te["entry_title"], style={"font_size": "32px", "font_weight": "800"}),
            rx.text(en["entry_title"], style={"font_size": "26px", "font_weight": "700"}),
            rx.text(te["entry_hint"], style={"font_size": "22px", "color": COLOR["muted"]}),
            rx.text(en["entry_hint"], style={"font_size": "22px", "color": COLOR["muted"]}),
            spacing="1",
            align="start",
        ),
        href=ROUTE,
        underline="none",
        style={
            "display": "block",
            "min_height": "96px",
            "margin": "12px 16px",
            "padding": "16px 20px",
            "border_radius": "20px",
            "background": COLOR["gold"],
            "color": COLOR["ink"],
            "border": f"2px solid {COLOR['border']}",
            "font_family": FONT,
        },
    )
