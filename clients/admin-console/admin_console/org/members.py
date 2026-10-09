"""The Members page: add a person, see everyone, change a role, get someone's
sign-in code again. Route `/members`.

"Add a person" is the whole point of this feature -- an admin onboards a new
member from here, never from the database. It creates the person, puts them in
the circles ticked, and shows their sign-in code (a QR and the text) to hand
over. The code is shown on screen only, never stored by the console.
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
ROLES = [role.value for role in SiteRole]


@dataclasses.dataclass
class UserRow:
    id: str
    name: str
    language: str
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

    # The sign-in code just issued, shown until dismissed. Never persisted.
    issued_name: str = ""
    issued_token: str = ""
    issued_qr: str = ""

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
                language=ui.LANGUAGE_NAMES.get(u.preferred_language, u.preferred_language),
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
        self._show_code(created.user.name, created.access_token)
        self.form_name = ""
        self.circles = [dataclasses.replace(c, ticked=False) for c in self.circles]
        self._say(f"{created.user.name} has been added. Their sign-in code is below.")
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

    def show_signin(self, user_id: str) -> None:
        try:
            issued = get_source().reissue_token(user_id)
        except SourceError as exc:
            self._say(f"A new sign-in code could not be made — {exc}", error=True)
            return
        name = next((u.name for u in self.users if u.id == user_id), "this person")
        self._show_code(name, issued.access_token)
        self._say(f"A fresh sign-in code for {name} is below.")

    def _show_code(self, name: str, token: str) -> None:
        self.issued_name = name
        self.issued_token = token
        self.issued_qr = ui.qr_data_uri(token)

    def dismiss_code(self) -> None:
        self.issued_name = ""
        self.issued_token = ""
        self.issued_qr = ""


# --- the page --------------------------------------------------------------------------------
def _circle_tick(choice: CircleChoice) -> rx.Component:
    return rx.hstack(
        rx.checkbox(
            checked=choice.ticked,
            on_change=lambda ticked: MembersState.toggle_circle(choice.id, ticked),
            aria_label="Put them in " + choice.name,
            size="3",
        ),
        rx.text(choice.name, style={"font_size": "18px"}),
        ui.pill(choice.kind),
        align="center",
        style={"min_height": "48px"},
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
            rx.text("There are no circles yet — make one on the Circles page.", color="gray"),
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
    )


def _code_card() -> rx.Component:
    return rx.cond(
        MembersState.issued_token != "",
        ui.card(
            rx.heading("Sign-in code for", size="5"),
            rx.heading(MembersState.issued_name, size="6", style={"color": COLOR["green"]}),
            rx.text(
                "Show or send this to them. Anyone who has it can sign in as them, "
                "so keep it private. It is not saved here — close this and it is gone; "
                "you can always make a fresh one.",
                style={"color": COLOR["muted"], "margin": "8px 0 12px"},
            ),
            rx.cond(
                MembersState.issued_qr != "",
                rx.image(
                    src=MembersState.issued_qr,
                    alt="QR code of the sign-in code below, for " + MembersState.issued_name,
                    width="240px",
                    height="240px",
                ),
                rx.fragment(),
            ),
            rx.text_area(
                value=MembersState.issued_token,
                read_only=True,
                aria_label="Sign-in code, as text",
                style={**ui.FIELD_STYLE, "min_height": "110px", "font_size": "14px"},
            ),
            rx.box(
                ui.big_button("Done — hide this", MembersState.dismiss_code, primary=False),
                margin_top="12px",
            ),
            style={"border": f"2px solid {COLOR['green']}"},
        ),
        rx.fragment(),
    )


def _person_row(row: UserRow) -> rx.Component:
    return rx.flex(
        rx.box(
            rx.text(row.name, style={"font_weight": "700"}),
            rx.text(row.language, style={"color": COLOR["muted"], "font_size": "16px"}),
            style={"flex": "1", "min_width": "180px"},
        ),
        ui.select(
            ROLES,
            row.role,
            lambda role: MembersState.change_role(row.id, role),
            "Role of " + row.name,
        ),
        ui.big_button(
            "Sign-in code",
            MembersState.show_signin(row.id),
            primary=False,
            aria_label="Sign-in code for " + row.name,
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
        _code_card(),
        _add_form(),
        ui.card(
            rx.heading("Everyone", size="5"),
            rx.cond(
                MembersState.users.length() > 0,
                rx.foreach(MembersState.users, _person_row),
                rx.text("No one yet.", color="gray"),
            ),
        ),
        rx.text(MembersState.source_label, style={"color": COLOR["muted"], "font_size": "14px"}),
        on_mount=MembersState.load,
    )
