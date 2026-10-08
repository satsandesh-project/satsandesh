"""The Circles page: see every circle, make one, rename one, and manage who is
in it. Route `/circles`.

A circle is either a "group" (anyone in it can post) or an "announcement" circle
(only moderators and admins post, everyone reads). The kind is chosen when the
circle is made and cannot be changed afterwards: switching a circle that already
has messages would change who may post in it. Removing someone from a circle
only removes them from that circle -- they stay in the community.
"""

from __future__ import annotations

import dataclasses

import reflex as rx

from admin_console.org import ui
from admin_console.org.context import get_source
from admin_console.org.ui import COLOR
from admin_console.source import SourceError
from contracts.chat.admin_org import AdminCircleCreate
from contracts.chat.circles import CircleKind, MembershipRole

CIRCLES_ROUTE = "/circles"
MEMBER_ROLES = [role.value for role in MembershipRole]
KIND_WORDS = {
    "group": "Group — everyone can post",
    "announcement": "Announcements — only moderators and admins post",
}


@dataclasses.dataclass
class CircleRow:
    id: str
    name: str
    kind: str
    kind_words: str
    member_count: int


@dataclasses.dataclass
class MemberRow:
    user_id: str
    name: str
    role: str


@dataclasses.dataclass
class Candidate:
    id: str
    name: str


def _circle_row(c) -> CircleRow:
    return CircleRow(
        id=c.id,
        name=c.name,
        kind=c.kind.value,
        kind_words=KIND_WORDS[c.kind.value],
        member_count=c.member_count,
    )


class CirclesState(rx.State):
    circles: list[CircleRow] = []
    banner: str = ""
    banner_is_error: bool = False
    source_label: str = ""

    new_name: str = ""
    new_kind: str = "group"

    selected_id: str = ""
    selected_name: str = ""
    rename_text: str = ""
    members: list[MemberRow] = []
    candidates: list[Candidate] = []
    add_user_id: str = ""
    add_role: str = "member"
    # Two-step removal: the first click only asks.
    pending_remove_id: str = ""

    def _say(self, message: str, *, error: bool = False) -> None:
        self.banner = message
        self.banner_is_error = error

    def load(self) -> None:
        source = get_source()
        self.source_label = source.label
        try:
            circles = source.list_circles()
        except SourceError as exc:
            self._say(f"Could not load the circles — {exc}", error=True)
            return
        self.circles = [_circle_row(c) for c in circles]
        if self.selected_id:
            if any(c.id == self.selected_id for c in self.circles):
                self._load_members()
            else:  # it was removed elsewhere while open here
                self._close()

    def _close(self) -> None:
        self.selected_id = ""
        self.selected_name = ""
        self.rename_text = ""
        self.members = []
        self.candidates = []
        self.add_user_id = ""
        self.pending_remove_id = ""

    def _load_members(self) -> None:
        source = get_source()
        try:
            members = source.list_members(self.selected_id)
            users = source.list_users()
        except SourceError as exc:
            self._say(f"Could not load this circle's members — {exc}", error=True)
            return
        self.members = [
            MemberRow(user_id=m.user_id, name=m.name, role=m.role.value) for m in members
        ]
        in_circle = {m.user_id for m in members}
        self.candidates = [Candidate(id=u.id, name=u.name) for u in users if u.id not in in_circle]
        if self.add_user_id and all(c.id != self.add_user_id for c in self.candidates):
            self.add_user_id = ""

    # -- making a circle --------------------------------------------------------

    def set_new_name(self, value: str) -> None:
        self.new_name = value

    def set_new_kind(self, kind: str) -> None:
        self.new_kind = kind

    def create_circle(self) -> None:
        name = self.new_name.strip()
        if not name:
            self._say("Please type a name for the circle first.", error=True)
            return
        try:
            created = get_source().create_circle(
                AdminCircleCreate(name=name, kind=CircleKind(self.new_kind))
            )
        except SourceError as exc:
            self._say(f"The circle was not made — {exc}", error=True)
            return
        self.new_name = ""
        self._say(f"“{created.name}” has been made. Open it to add people.")
        self.load()

    # -- one circle ---------------------------------------------------------------

    def open_circle(self, circle_id: str) -> None:
        row = next((c for c in self.circles if c.id == circle_id), None)
        if row is None:
            return
        self.selected_id = circle_id
        self.selected_name = row.name
        self.rename_text = row.name
        self.pending_remove_id = ""
        self.add_user_id = ""
        self.banner = ""
        self._load_members()

    def close_circle(self) -> None:
        self._close()

    def set_rename_text(self, value: str) -> None:
        self.rename_text = value

    def rename(self) -> None:
        name = self.rename_text.strip()
        if not name:
            self._say("The circle needs a name.", error=True)
            return
        try:
            renamed = get_source().rename_circle(self.selected_id, name)
        except SourceError as exc:
            self._say(f"The circle was not renamed — {exc}", error=True)
            return
        self.selected_name = renamed.name
        self._say(f"Renamed to “{renamed.name}”.")
        self.load()

    # -- people in it ---------------------------------------------------------------

    def set_add_user(self, user_id: str) -> None:
        self.add_user_id = user_id

    def set_add_role(self, role: str) -> None:
        self.add_role = role

    def add_member(self) -> None:
        if not self.add_user_id:
            self._say("Choose who to add first.", error=True)
            return
        try:
            member = get_source().add_member(
                self.selected_id, self.add_user_id, MembershipRole(self.add_role)
            )
        except SourceError as exc:
            self._say(f"They were not added — {exc}", error=True)
            return
        self.add_user_id = ""
        self._say(f"{member.name} is now in “{self.selected_name}”.")
        self.load()

    def change_member_role(self, user_id: str, role: str) -> None:
        try:
            member = get_source().set_member_role(self.selected_id, user_id, MembershipRole(role))
        except SourceError as exc:
            self._say(f"The role was not changed — {exc}", error=True)
            self.load()
            return
        self._say(f"{member.name} is now {member.role.value} in this circle.")
        self.load()

    def ask_remove(self, user_id: str) -> None:
        self.pending_remove_id = user_id

    def cancel_remove(self) -> None:
        self.pending_remove_id = ""

    def confirm_remove(self) -> None:
        user_id, self.pending_remove_id = self.pending_remove_id, ""
        name = next((m.name for m in self.members if m.user_id == user_id), "They")
        try:
            get_source().remove_member(self.selected_id, user_id)
        except SourceError as exc:
            self._say(f"{name} was not removed — {exc}", error=True)
            self.load()
            return
        self._say(
            (
                f"{name} was taken out of “{self.selected_name}”. "
                "They are still a member of the community."
            )
        )
        self.load()


# --- the page --------------------------------------------------------------------------------
def _kind_buttons() -> rx.Component:
    def one(kind: str) -> rx.Component:
        return rx.button(
            KIND_WORDS[kind],
            on_click=CirclesState.set_new_kind(kind),
            style={
                "min_height": "64px",
                "flex": "1",
                "font_size": "17px",
                "font_weight": "700",
                "border_radius": "12px",
                "cursor": "pointer",
                "background": rx.cond(CirclesState.new_kind == kind, COLOR["green"], COLOR["card"]),
                "color": rx.cond(CirclesState.new_kind == kind, "white", COLOR["green"]),
                "border": f"2px solid {COLOR['green']}",
            },
        )

    return rx.flex(one("group"), one("announcement"), gap="12px", wrap="wrap", width="100%")


def _circle_line(row: CircleRow) -> rx.Component:
    return rx.flex(
        rx.box(
            rx.text(row.name, style={"font_weight": "700"}),
            rx.text(
                row.kind_words,
                style={"color": COLOR["muted"], "font_size": "16px"},
            ),
            style={"flex": "1", "min_width": "200px"},
        ),
        rx.text(row.member_count.to_string() + " people", style={"min_width": "100px"}),
        ui.big_button("Open", CirclesState.open_circle(row.id), primary=False),
        gap="12px",
        align="center",
        wrap="wrap",
        style={"padding": "12px 0", "border_bottom": f"1px solid {COLOR['line']}"},
    )


def _member_line(row: MemberRow) -> rx.Component:
    return rx.flex(
        rx.text(row.name, style={"font_weight": "700", "flex": "1", "min_width": "160px"}),
        rx.select(
            MEMBER_ROLES,
            value=row.role,
            on_change=lambda role: CirclesState.change_member_role(row.user_id, role),
            size="3",
        ),
        rx.cond(
            CirclesState.pending_remove_id == row.user_id,
            rx.hstack(
                ui.big_button("Yes, take them out", CirclesState.confirm_remove),
                ui.big_button("No, keep", CirclesState.cancel_remove, primary=False),
                spacing="2",
            ),
            ui.big_button("Remove", CirclesState.ask_remove(row.user_id), primary=False),
        ),
        gap="12px",
        align="center",
        wrap="wrap",
        style={"padding": "12px 0", "border_bottom": f"1px solid {COLOR['line']}"},
    )


def _add_person() -> rx.Component:
    return rx.box(
        ui.label("Add someone to this circle"),
        rx.cond(
            CirclesState.candidates.length() > 0,
            rx.flex(
                rx.select.root(
                    rx.select.trigger(placeholder="Choose a person", style={"min_height": "56px"}),
                    rx.select.content(
                        rx.foreach(
                            CirclesState.candidates,
                            lambda c: rx.select.item(c.name, value=c.id),
                        )
                    ),
                    value=CirclesState.add_user_id,
                    on_change=CirclesState.set_add_user,
                    size="3",
                ),
                rx.select(
                    MEMBER_ROLES,
                    value=CirclesState.add_role,
                    on_change=CirclesState.set_add_role,
                    size="3",
                ),
                ui.big_button("Add", CirclesState.add_member),
                gap="12px",
                align="center",
                wrap="wrap",
            ),
            rx.text("Everyone is already in this circle.", color="gray"),
        ),
    )


def _open_circle() -> rx.Component:
    return rx.cond(
        CirclesState.selected_id != "",
        ui.card(
            rx.flex(
                rx.heading(CirclesState.selected_name, size="6", style={"color": COLOR["green"]}),
                rx.spacer(),
                ui.big_button("Close", CirclesState.close_circle, primary=False),
                align="center",
            ),
            ui.label("Rename"),
            rx.flex(
                rx.box(
                    ui.text_field(
                        "Circle name", CirclesState.rename_text, CirclesState.set_rename_text
                    ),
                    style={"flex": "1", "min_width": "220px"},
                ),
                ui.big_button("Save name", CirclesState.rename),
                gap="12px",
                wrap="wrap",
            ),
            ui.label("People in this circle"),
            rx.cond(
                CirclesState.members.length() > 0,
                rx.foreach(CirclesState.members, _member_line),
                rx.text("No one is in this circle yet.", color="gray"),
            ),
            _add_person(),
            style={"border": f"2px solid {COLOR['green']}"},
        ),
        rx.fragment(),
    )


def circles_page() -> rx.Component:
    return ui.page(
        CIRCLES_ROUTE,
        ui.heading("Circles", "Groups of people who talk together, and announcement circles."),
        ui.banner(CirclesState.banner, CirclesState.banner_is_error),
        _open_circle(),
        ui.card(
            rx.heading("All circles", size="5"),
            rx.cond(
                CirclesState.circles.length() > 0,
                rx.foreach(CirclesState.circles, _circle_line),
                rx.text("No circles yet.", color="gray"),
            ),
        ),
        ui.card(
            rx.heading("Make a new circle", size="5"),
            ui.label("Name"),
            ui.text_field(
                "For example: Evening Satsang", CirclesState.new_name, CirclesState.set_new_name
            ),
            ui.label("What kind"),
            _kind_buttons(),
            rx.text(
                "You cannot change the kind later.",
                style={"color": COLOR["muted"], "margin_top": "6px"},
            ),
            rx.box(
                ui.big_button("Make this circle", CirclesState.create_circle), margin_top="20px"
            ),
        ),
        rx.text(CirclesState.source_label, style={"color": COLOR["muted"], "font_size": "14px"}),
        on_mount=CirclesState.load,
    )
