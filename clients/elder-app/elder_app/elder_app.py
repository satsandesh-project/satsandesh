"""SatSandesh elder chat UI shell (Week 4 M1).

Placeholder/mock data only, deliberately — wiring this screen to the real
gateway backend is M2's separate Week 4 task ("Wire client -> gateway
end-to-end + staging deploy"), which depends on this screen existing, not
the other way around. Structured so that step is a data-source swap, not a
UI rewrite: every place that reads CONTACTS/INITIAL_MESSAGES is the seam
where a real API call replaces the mock.

Design principles, straight from the Week 4 schedule line for M1:
  - two-taps-to-anything: Home -> tap a contact -> chat screen. One tap,
    not a menu tree. The language toggle is reachable in one tap from
    either screen.
  - large targets: every tappable element has a large min-height/width and
    big text, sized for elder users, not phone-app defaults.
  - faces-before-names: each contact row shows a large avatar first; the
    name is secondary, smaller text below/beside it. Recognition before
    reading.
  - bilingual (Telugu / English): a single toggle switches all UI chrome
    and contact names. Free-typed message text is left as typed -- this
    app does not machine-translate a user's own words.
"""

import reflex as rx

from rxconfig import config

TEXTS = {
    "en": {
        "app_title": "SatSandesh",
        "contacts_heading": "Your People",
        "back": "< Back",
        "type_placeholder": "Type a message...",
        "send": "Send",
        "no_messages": "No messages yet. Say hello!",
        "lang_button": "తెలుగు",
    },
    "te": {
        "app_title": "సత్సందేశ్",
        "contacts_heading": "మీ వాళ్ళు",
        "back": "< వెనుకకు",
        "type_placeholder": "సందేశం టైప్ చేయండి...",
        "send": "పంపండి",
        "no_messages": "ఇంకా సందేశాలు లేవు. హలో చెప్పండి!",
        "lang_button": "English",
    },
}

CONTACTS = [
    {
        "id": "1",
        "name_en": "Priya (Daughter)",
        "name_te": "ప్రియ (కూతురు)",
        "emoji": "👩",
        "color": "#e57373",
    },
    {
        "id": "2",
        "name_en": "Ravi (Son)",
        "name_te": "రవి (కొడుకు)",
        "emoji": "👨",
        "color": "#64b5f6",
    },
    {
        "id": "3",
        "name_en": "Dr. Kumar",
        "name_te": "డా. కుమార్",
        "emoji": "🧑‍⚕️",
        "color": "#81c784",
    },
    {
        "id": "4",
        "name_en": "Lakshmi (Neighbor)",
        "name_te": "లక్ష్మి (పొరుగింటి)",
        "emoji": "👵",
        "color": "#ffb74d",
    },
]

INITIAL_MESSAGES = {
    "1": [
        {"from": "them", "text": "Amma, did you take your morning tablets?", "time": "9:02 AM"},
        {"from": "me", "text": "Yes, just now.", "time": "9:05 AM"},
    ],
    "2": [
        {"from": "them", "text": "Calling you this evening, be free.", "time": "8:40 AM"},
    ],
    "3": [
        {"from": "them", "text": "Your BP reading looks good this week.", "time": "Yesterday"},
    ],
    "4": [],
}


class State(rx.State):
    language: str = "en"
    current_contact_id: str = ""
    draft_text: str = ""
    contacts: list[dict[str, str]] = CONTACTS
    messages: dict[str, list[dict[str, str]]] = INITIAL_MESSAGES

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
    def current_messages(self) -> list[dict[str, str]]:
        return self.messages.get(self.current_contact_id, [])

    def open_chat(self, contact_id: str):
        self.current_contact_id = contact_id

    def go_home(self):
        self.current_contact_id = ""
        self.draft_text = ""

    def toggle_language(self):
        self.language = "te" if self.language == "en" else "en"

    def set_draft(self, value: str):
        self.draft_text = value

    def send_message(self):
        text = self.draft_text.strip()
        if not text:
            return
        existing = self.messages.get(self.current_contact_id, [])
        updated = existing + [{"from": "me", "text": text, "time": "now"}]
        self.messages = {**self.messages, self.current_contact_id: updated}
        self.draft_text = ""


def language_toggle() -> rx.Component:
    return rx.button(
        State.t["lang_button"],
        on_click=State.toggle_language,
        style={
            "min_height": "56px",
            "min_width": "96px",
            "font_size": "18px",
            "font_weight": "600",
            "border_radius": "10px",
            "background": "#37474f",
            "color": "white",
            "border": "none",
        },
    )


def avatar(contact: rx.Var[dict], size: str = "72px") -> rx.Component:
    return rx.box(
        contact["emoji"],
        style={
            "width": size,
            "height": size,
            "min_width": size,
            "border_radius": "50%",
            "background": contact["color"],
            "display": "flex",
            "align_items": "center",
            "justify_content": "center",
            "font_size": "36px",
            "flex_shrink": "0",
        },
    )


def contact_row(contact: rx.Var[dict]) -> rx.Component:
    name = rx.cond(State.language == "en", contact["name_en"], contact["name_te"])
    return rx.box(
        rx.hstack(
            avatar(contact),
            rx.text(name, style={"font_size": "24px", "font_weight": "600", "color": "#1a1a1a"}),
            spacing="4",
            align="center",
        ),
        on_click=lambda: State.open_chat(contact["id"]),
        style={
            "width": "100%",
            "min_height": "96px",
            "padding": "16px 20px",
            "background": "#ffffff",
            "border_radius": "14px",
            "border": "1px solid #e0e0e0",
            "cursor": "pointer",
            "box_shadow": "0 1px 3px rgba(0,0,0,0.08)",
        },
    )


def home_screen() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.heading(State.t["app_title"], style={"font_size": "32px", "font_weight": "700"}),
            rx.spacer(),
            language_toggle(),
            width="100%",
            align="center",
        ),
        rx.text(
            State.t["contacts_heading"],
            style={"font_size": "20px", "color": "#555", "margin_top": "8px"},
        ),
        rx.vstack(
            rx.foreach(State.contacts, contact_row),
            spacing="3",
            width="100%",
            style={"margin_top": "8px"},
        ),
        spacing="4",
        width="100%",
        style={"max_width": "560px", "margin": "0 auto", "padding": "24px 16px"},
    )


def message_bubble(msg: rx.Var[dict]) -> rx.Component:
    is_me = msg["from"] == "me"
    return rx.box(
        rx.text(msg["text"], style={"font_size": "20px", "line_height": "1.4"}),
        rx.text(msg["time"], style={"font_size": "13px", "color": "#777", "margin_top": "4px"}),
        style=rx.cond(
            is_me,
            {
                "align_self": "flex-end",
                "background": "#dcf8c6",
                "border_radius": "16px 16px 4px 16px",
                "padding": "12px 16px",
                "max_width": "80%",
            },
            {
                "align_self": "flex-start",
                "background": "#ffffff",
                "border": "1px solid #e0e0e0",
                "border_radius": "16px 16px 16px 4px",
                "padding": "12px 16px",
                "max_width": "80%",
            },
        ),
    )


def chat_screen() -> rx.Component:
    name = rx.cond(
        State.language == "en",
        State.current_contact["name_en"],
        State.current_contact["name_te"],
    )
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
                    "background": "#eceff1",
                    "border": "none",
                    "padding": "0 16px",
                },
            ),
            avatar(State.current_contact, size="56px"),
            rx.text(name, style={"font_size": "24px", "font_weight": "700"}),
            rx.spacer(),
            language_toggle(),
            width="100%",
            align="center",
            spacing="3",
        ),
        rx.cond(
            State.current_messages.length() > 0,
            rx.vstack(
                rx.foreach(State.current_messages, message_bubble),
                spacing="3",
                width="100%",
                align_items="stretch",
                style={"padding": "16px 4px", "min_height": "50vh"},
            ),
            rx.center(
                rx.text(State.t["no_messages"], style={"font_size": "20px", "color": "#777"}),
                style={"min_height": "50vh"},
            ),
        ),
        rx.hstack(
            rx.input(
                placeholder=State.t["type_placeholder"],
                value=State.draft_text,
                on_change=State.set_draft,
                style={
                    "flex": "1",
                    "min_height": "60px",
                    "font_size": "20px",
                    "padding": "0 16px",
                    "border_radius": "12px",
                    "border": "1px solid #ccc",
                },
            ),
            rx.button(
                State.t["send"],
                on_click=State.send_message,
                style={
                    "min_height": "60px",
                    "min_width": "100px",
                    "font_size": "20px",
                    "font_weight": "700",
                    "border_radius": "12px",
                    "background": "#2e7d32",
                    "color": "white",
                    "border": "none",
                },
            ),
            width="100%",
            spacing="3",
            style={"position": "sticky", "bottom": "0", "background": "#f5f5f5", "padding_top": "12px"},
        ),
        spacing="3",
        width="100%",
        style={"max_width": "560px", "margin": "0 auto", "padding": "24px 16px"},
    )


def index() -> rx.Component:
    return rx.box(
        rx.cond(State.current_contact_id == "", home_screen(), chat_screen()),
        style={"min_height": "100vh", "background": "#f5f5f5", "font_family": "system-ui, sans-serif"},
    )


app = rx.App()
app.add_page(index, title=f"{config.app_name}")
