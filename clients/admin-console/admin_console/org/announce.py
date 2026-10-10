"""The Compose an announcement page. Route `/announce`.

Write it, choose where it goes, read it back, then send. Sending goes to many
people at once and cannot be taken back, so there are always two steps: "Check
it" shows exactly what will be sent and to which circles, and only then "Send".
The request id is made when the message is checked and kept until it succeeds,
so pressing Send twice -- or retrying after a dropped connection -- posts it once.
"""

from __future__ import annotations

import dataclasses
import uuid

import reflex as rx

from admin_console.org import ui
from admin_console.org.context import get_source
from admin_console.org.ui import COLOR
from admin_console.source import SourceError
from contracts.chat.admin_org import AdminActionKind, AdminAnnouncementIn
from contracts.chat.circles import CircleKind

ANNOUNCE_ROUTE = "/announce"
EVERYONE = "all"


@dataclasses.dataclass
class Destination:
    id: str
    name: str
    member_count: int


@dataclasses.dataclass
class SentRow:
    when: str
    where: str
    length: int


class AnnounceState(rx.State):
    destinations: list[Destination] = []
    recent: list[SentRow] = []
    banner: str = ""
    banner_is_error: bool = False
    source_label: str = ""

    text: str = ""
    language: str = ""  # "" = not said; the gateway works it out
    target: str = EVERYONE
    # Set when the message is checked; kept until it has been sent.
    request_id: str = ""

    def _say(self, message: str, *, error: bool = False) -> None:
        self.banner = message
        self.banner_is_error = error

    def load(self) -> None:
        source = get_source()
        self.source_label = source.label
        try:
            circles = source.list_circles()
            actions = source.list_actions(limit=50)
        except SourceError as exc:
            self._say(f"Could not load the page — {exc}", error=True)
            return
        self.destinations = [
            Destination(id=c.id, name=c.name, member_count=c.member_count)
            for c in circles
            if c.kind is CircleKind.ANNOUNCEMENT
        ]
        names = {c.id: c.name for c in circles}
        self.recent = [
            SentRow(
                when=a.created_at.strftime("%d %b, %H:%M"),
                where=", ".join(
                    names.get(cid, "a circle") for cid in a.details.get("circle_ids", [])
                ),
                length=int(a.details.get("length", 0)),
            )
            for a in actions
            if a.action is AdminActionKind.ANNOUNCEMENT_PUBLISH
        ][:5]
        if self.target != EVERYONE and all(d.id != self.target for d in self.destinations):
            self.target = EVERYONE

    # -- composing --------------------------------------------------------------

    def set_text(self, value: str) -> None:
        self.text = value
        self.request_id = ""  # edited: this is a different message now

    def set_language(self, code: str) -> None:
        self.language = "" if self.language == code else code
        self.request_id = ""

    def set_target(self, target: str) -> None:
        self.target = target
        self.request_id = ""

    @rx.var
    def reviewing(self) -> bool:
        return self.request_id != ""

    @rx.var
    def target_words(self) -> str:
        if self.target == EVERYONE:
            n = len(self.destinations)
            return f"all {n} announcement circle{'s' if n != 1 else ''}"
        return next((f"“{d.name}”" for d in self.destinations if d.id == self.target), "")

    @rx.var
    def audience(self) -> int:
        if self.target == EVERYONE:
            return sum(d.member_count for d in self.destinations)
        return next((d.member_count for d in self.destinations if d.id == self.target), 0)

    def check(self) -> None:
        if not self.text.strip():
            self._say("Please write the announcement first.", error=True)
            return
        if not self.destinations:
            self._say(
                "There is no announcement circle yet. Make one on the Circles page first.",
                error=True,
            )
            return
        self.request_id = str(uuid.uuid4())
        self.banner = ""

    def edit_again(self) -> None:
        self.request_id = ""

    def send(self) -> None:
        if not self.request_id:
            return
        try:
            body = AdminAnnouncementIn(
                text=self.text.strip(),
                circle_id=None if self.target == EVERYONE else self.target,
                all_circles=self.target == EVERYONE,
                client_msg_id=uuid.UUID(self.request_id),
                source_lang=self.language or None,
            )
            sent = get_source().publish_announcement(body)
        except SourceError as exc:
            # Not sent. The request id is kept, so trying again cannot post twice.
            self._say(f"It was not sent — {exc}", error=True)
            return
        n = len(sent.circle_ids)
        self.text = ""
        self.request_id = ""
        self._say(f"Sent to {n} circle{'s' if n != 1 else ''}. People will see it shortly.")
        self.load()


# --- the page --------------------------------------------------------------------------------
def _target_button(label, value) -> rx.Component:
    return rx.button(
        label,
        on_click=AnnounceState.set_target(value),
        custom_attrs=ui.toggle_attrs(AnnounceState.target == value),
        style={
            "min_height": "56px",
            "font_size": "18px",
            "font_weight": "700",
            "border_radius": "12px",
            "cursor": "pointer",
            "background": rx.cond(AnnounceState.target == value, COLOR["green"], COLOR["card"]),
            "color": rx.cond(AnnounceState.target == value, "white", COLOR["green"]),
            "border": f"2px solid {COLOR['green']}",
        },
    )


def _destination_button(d: Destination) -> rx.Component:
    return _target_button(d.name, d.id)


def _compose() -> rx.Component:
    return ui.card(
        rx.heading("Write an announcement", size="5"),
        ui.label("What do you want to say?"),
        rx.text_area(
            placeholder="For example: Satsang moves to 6 pm this Sunday.",
            value=AnnounceState.text,
            on_change=AnnounceState.set_text,
            aria_label="The announcement",
            style={**ui.FIELD_STYLE, "min_height": "160px", "font_size": "20px", "padding": "12px"},
        ),
        rx.text(
            "People read and hear it in their own language — you only write it once.",
            style={"color": COLOR["muted"], "margin_top": "6px"},
        ),
        ui.label("Who should get it?"),
        rx.flex(
            _target_button("Everyone", EVERYONE),
            rx.foreach(AnnounceState.destinations, _destination_button),
            gap="12px",
            wrap="wrap",
        ),
        ui.label("Language you wrote it in (if you are sure)"),
        ui.language_picker(AnnounceState.language, AnnounceState.set_language),
        rx.box(ui.big_button("Check it", AnnounceState.check), margin_top="20px"),
    )


def _review() -> rx.Component:
    return ui.card(
        rx.heading("Please check before sending", size="5"),
        rx.text(
            "It goes to ",
            rx.text.strong(AnnounceState.target_words),
            " — about ",
            rx.text.strong(AnnounceState.audience),
            " people. This cannot be taken back.",
            style={"margin": "8px 0 12px"},
        ),
        rx.box(
            AnnounceState.text,
            style={
                "padding": "16px",
                "background": COLOR["gold_tint"],
                "border_radius": "12px",
                "font_size": "20px",
                "white_space": "pre-wrap",
            },
        ),
        rx.flex(
            ui.big_button("Send it now", AnnounceState.send),
            ui.big_button("Go back and change it", AnnounceState.edit_again, primary=False),
            gap="12px",
            wrap="wrap",
            style={"margin_top": "20px"},
        ),
        style={"border": f"2px solid {COLOR['saffron']}"},
    )


def _sent_line(row: SentRow) -> rx.Component:
    return rx.flex(
        rx.text(row.when, style={"min_width": "120px", "color": COLOR["muted"]}),
        rx.text("to " + row.where),
        gap="12px",
        wrap="wrap",
        style={"padding": "8px 0", "border_bottom": f"1px solid {COLOR['line']}"},
    )


def announce_page() -> rx.Component:
    return ui.page(
        ANNOUNCE_ROUTE,
        ui.heading("Announcements", "Send a message to everyone in the announcement circles."),
        ui.banner(AnnounceState.banner, AnnounceState.banner_is_error),
        rx.cond(AnnounceState.reviewing, _review(), _compose()),
        ui.card(
            rx.heading("Sent recently", size="5"),
            rx.cond(
                AnnounceState.recent.length() > 0,
                rx.foreach(AnnounceState.recent, _sent_line),
                rx.text("Nothing sent yet.", color="gray"),
            ),
        ),
        rx.text(AnnounceState.source_label, style={"color": COLOR["muted"], "font_size": "14px"}),
        on_mount=AnnounceState.load,
    )
