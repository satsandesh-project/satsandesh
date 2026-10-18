"""The Members page: add a person, see everyone, change a role. Route `/members`.

"Add a person" is the whole point of this feature -- an admin onboards a new
member from here, never from the database. It creates the person and puts them
in the circles ticked.

It does NOT show a sign-in code, yet. The only one the gateway can issue today is
the 30-day, unrevocable session token (`access_token` on `POST /admin/users`,
`POST /admin/users/{id}/token`): a QR of it is a paper password, and the elder
app cannot accept it anyway. So the console drops the token it is handed and
offers no way to issue another. The single-use claim (`POST
/admin/users/{id}/claim`, contracts/chat/claims.py) is what this page will show,
as a QR of `<app>/#claim=<code>`, once the gateway has it.
"""

from __future__ import annotations

import dataclasses

import reflex as rx

from admin_console.org import ui
from admin_console.org.context import get_source
from admin_console.org.ui import COLOR
from admin_console.source import SourceError
from contracts.chat.admin_org import AdminUserCreate, SiteRole

MEMBERS_ROUTE = "/members"
SIGN_IN_LATER = (
    "Signing in on their phone is not ready in this console yet: it comes with "
    "one-time sign-in codes."
)
ROLES = [role.value for role in SiteRole]


@dataclasses.dataclass
class UserRow:
    id: str
    name: str
    language: str
    language_code: str
    role: str


@dataclasses.dataclass
class CircleChoice:
    id: str
    name: str
    kind: str
    ticked: bool


class MembersState(rx.State):
    users: list[UserRow] = []
    circles: list[CircleChoice] = []
    banner: str = ""
    banner_is_error: bool = False
    source_label: str = ""

    form_name: str = ""
    form_language: str = "te"
    form_role: str = "elder"

    def _say(self, message: str, *, error: bool = False) -> None:
        self.banner = message
        self.banner_is_error = error

    def load(self) -> None:
        source = get_source()
        self.source_label = source.label
        try:
            users = source.list_users()
            circles = source.list_circles()
        except SourceError as exc:
            # Keep what was on screen: blanking the list would read as "there is
            # nobody", the one wrong thing to imply when the truth is "I could
            # not ask".
            self._say(f"Could not load the list — {exc}", error=True)
            return
        self.users = [
            UserRow(
                id=u.id,
                name=u.name,
                language=ui.language_name(u.preferred_language),
                language_code=u.preferred_language,
                role=u.role.value,
            )
            for u in users
        ]
        ticked = {c.id for c in self.circles if c.ticked}
        self.circles = [
            CircleChoice(id=c.id, name=c.name, kind=c.kind.value, ticked=c.id in ticked)
            for c in circles
        ]

    # -- the form -------------------------------------------------------------

    def set_form_name(self, value: str) -> None:
        self.form_name = value

    def set_form_language(self, code: str) -> None:
        self.form_language = code

    def set_form_role(self, role: str) -> None:
        self.form_role = role

    def toggle_circle(self, circle_id: str, ticked: bool) -> None:
        self.circles = [
            dataclasses.replace(c, ticked=ticked) if c.id == circle_id else c for c in self.circles
        ]

    def create_member(self) -> None:
        name = self.form_name.strip()
        if not name:
            self._say("Please type the person's name first.", error=True)
            return
        try:
            body = AdminUserCreate(
                name=name,
                preferred_language=self.form_language,
                role=self.form_role,
                circle_ids=[c.id for c in self.circles if c.ticked],
            )
            created = get_source().create_user(body)
        except SourceError as exc:
            # Nothing was created (the gateway refuses the whole request), and
            # the form is kept so the admin can simply try again.
            self._say(f"{name} was not added — {exc}", error=True)
            return
        # `created.access_token` is deliberately not kept: see this module's docstring.
        self.form_name = ""
        self.circles = [dataclasses.replace(c, ticked=False) for c in self.circles]
        self._say(f"{created.user.name} has been added. {SIGN_IN_LATER}")
        self.load()

    # -- people already here ----------------------------------------------------

    def change_role(self, user_id: str, role: str) -> None:
        try:
            updated = get_source().set_user_role(user_id, SiteRole(role))
        except SourceError as exc:
            self._say(f"The role was not changed — {exc}", error=True)
            self.load()  # put the drop-down back to what is really stored
            return
        self._say(f"{updated.name} is now {updated.role.value}.")
        self.load()


# --- the page --------------------------------------------------------------------------------
def _circle_tick(choice: CircleChoice) -> rx.Component:
    # The whole row is one <label>, so tapping the name ticks the box too: the box alone is
    # about 20 px, too small a target for the people this is for.
    return rx.text(
        rx.hstack(
            rx.checkbox(
                checked=choice.ticked,
                on_change=lambda ticked: MembersState.toggle_circle(choice.id, ticked),
                aria_label="Put them in " + choice.name,
                size="3",
                style={"width": "28px", "height": "28px"},
            ),
            rx.text(choice.name, as_="span", style={"font_size": "18px"}),
            ui.pill(choice.kind),
            align="center",
            spacing="3",
        ),
        as_="label",
        style={"display": "block", "min_height": "56px", "padding": "8px 0", "cursor": "pointer"},
    )


def _add_form() -> rx.Component:
    return ui.card(
        rx.heading("Add a person", size="5"),
        ui.label("Name"),
        ui.text_field(
            "Their name",
            MembersState.form_name,
            MembersState.set_form_name,
            aria_label="Name of the new person",
        ),
        ui.label("Language they read and listen in"),
        ui.language_picker(MembersState.form_language, MembersState.set_form_language),
        ui.label("Circles to put them in"),
        rx.cond(
            MembersState.circles.length() > 0,
            rx.vstack(rx.foreach(MembersState.circles, _circle_tick), align="start", spacing="1"),
            rx.text(
                "There are no circles yet — make one on the Circles page.",
                style={"color": COLOR["muted"]},
            ),
        ),
        ui.label("Role"),
        ui.select(
            ROLES, MembersState.form_role, MembersState.set_form_role, "Role for the new person"
        ),
        rx.text(
            "Elder takes part in circles. Moderator also reviews held messages. "
            "Admin also manages people.",
            style={"color": COLOR["muted"], "margin_top": "6px"},
        ),
        rx.box(ui.big_button("Add this person", MembersState.create_member), margin_top="20px"),
        rx.text(SIGN_IN_LATER, style={"color": COLOR["muted"], "margin_top": "12px"}),
    )


def _person_row(row: UserRow) -> rx.Component:
    return rx.flex(
        rx.box(
            rx.text(row.name, style={"font_weight": "700"}),
            rx.text(
                row.language,
                # So a screen reader reads తెలుగు in Telugu, not as English.
                custom_attrs={"lang": row.language_code},
                style={"color": COLOR["muted"], "font_size": "16px"},
            ),
            style={"flex": "1", "min_width": "180px"},
        ),
        ui.select(
            ROLES,
            row.role,
            lambda role: MembersState.change_role(row.id, role),
            "Role of " + row.name,
        ),
        gap="12px",
        align="center",
        wrap="wrap",
        style={"padding": "12px 0", "border_bottom": f"1px solid {COLOR['line']}"},
    )


def members_page() -> rx.Component:
    return ui.page(
        MEMBERS_ROUTE,
        ui.heading("Members", "Everyone in the community. Add a new person here."),
        ui.banner(MembersState.banner, MembersState.banner_is_error),
        _add_form(),
        ui.card(
            rx.heading("Everyone", size="5"),
            rx.cond(
                MembersState.users.length() > 0,
                rx.foreach(MembersState.users, _person_row),
                rx.text("No one yet.", style={"color": COLOR["muted"]}),
            ),
        ),
        rx.text(MembersState.source_label, style={"color": COLOR["muted"], "font_size": "14px"}),
        on_mount=MembersState.load,
    )
