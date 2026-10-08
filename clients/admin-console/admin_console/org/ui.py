"""
Shared pieces for the organisation pages (Members, Circles, Announce).

Designed for the volunteers who run a satsang community, who are often not
technical and not young: large type, large buttons, plain words, a calm warm
palette (the same one as the elder app), and every destructive or irreversible
step asks once more. Nothing here is clever.
"""

from __future__ import annotations

import reflex as rx

COLOR = {
    "page": "#F7F0E3",
    "card": "#FFFCF6",
    "ink": "#2A2118",
    "muted": "#6E6047",
    "line": "#EADCC4",
    "green": "#2F5D50",
    "green_tint": "#EAF1EC",
    "saffron": "#B4531A",
    "gold_tint": "#FBEFD5",
    "error_tint": "#F7E2D6",
}

LANGUAGES = [("te", "తెలుగు"), ("hi", "हिन्दी"), ("en", "English")]
LANGUAGE_NAMES = dict(LANGUAGES)

NAV = [
    ("Review queue", "/"),
    ("Members", "/members"),
    ("Circles", "/circles"),
    ("Announcements", "/announce"),
]

FIELD_STYLE = {
    "min_height": "56px",
    "font_size": "18px",
    "border_radius": "12px",
    "border": f"1px solid {COLOR['line']}",
    "background": "white",
    "width": "100%",
}


def nav_bar(current: str) -> rx.Component:
    """The way between the console's pages. `current` is the route of the page
    being shown, which is drawn as the selected one."""
    return rx.hstack(
        rx.heading("SatSandesh", size="6", style={"color": COLOR["green"]}),
        rx.spacer(),
        *[
            rx.link(
                label,
                href=route,
                style={
                    "font_size": "18px",
                    "font_weight": "700" if route == current else "500",
                    "padding": "12px 18px",
                    "border_radius": "12px",
                    "min_height": "48px",
                    "display": "flex",
                    "align_items": "center",
                    "background": COLOR["green_tint"] if route == current else "transparent",
                    "color": COLOR["green"],
                    "text_decoration": "none",
                },
            )
            for label, route in NAV
        ],
        width="100%",
        wrap="wrap",
        style={
            "padding": "10px 20px",
            "background": COLOR["card"],
            "border_bottom": f"1px solid {COLOR['line']}",
        },
    )


def page(current: str, *children: rx.Component, on_mount=None) -> rx.Component:
    """The frame every organisation page shares. `on_mount` is the page's own
    loader."""
    extra = {"on_mount": on_mount} if on_mount is not None else {}
    return rx.box(
        nav_bar(current),
        rx.box(
            *children,
            style={"max_width": "960px", "margin": "0 auto", "padding": "24px 16px 64px"},
        ),
        style={
            "min_height": "100vh",
            "background": COLOR["page"],
            "color": COLOR["ink"],
            "font_size": "18px",
        },
        **extra,
    )


def banner(message: rx.Var, is_error: rx.Var) -> rx.Component:
    return rx.cond(
        message != "",
        rx.box(
            rx.text(message, style={"font_size": "18px", "line_height": "1.5"}),
            style={
                "padding": "16px 20px",
                "border_radius": "12px",
                "margin_bottom": "16px",
                "background": rx.cond(is_error, COLOR["error_tint"], COLOR["green_tint"]),
                "border": f"1px solid {COLOR['line']}",
            },
        ),
        rx.fragment(),
    )


def card(*children: rx.Component, **style) -> rx.Component:
    return rx.box(
        *children,
        style={
            "background": COLOR["card"],
            "border": f"1px solid {COLOR['line']}",
            "border_radius": "16px",
            "padding": "20px",
            "margin_bottom": "20px",
            **style,
        },
    )


def heading(text: str, hint: str = "") -> rx.Component:
    return rx.box(
        rx.heading(text, size="7", style={"color": COLOR["green"], "margin_bottom": "4px"}),
        rx.text(hint, style={"color": COLOR["muted"], "margin_bottom": "16px"})
        if hint
        else rx.fragment(),
    )


def label(text: str) -> rx.Component:
    return rx.text(text, style={"font_weight": "700", "margin_top": "14px", "margin_bottom": "6px"})


def big_button(text, on_click, *, primary: bool = True, **extra) -> rx.Component:
    return rx.button(
        text,
        on_click=on_click,
        style={
            "min_height": "56px",
            "padding": "0 24px",
            "font_size": "18px",
            "font_weight": "700",
            "border_radius": "12px",
            "cursor": "pointer",
            "background": COLOR["green"] if primary else COLOR["card"],
            "color": "white" if primary else COLOR["green"],
            "border": f"2px solid {COLOR['green']}",
        },
        **extra,
    )


def pill(text, color: str = COLOR["gold_tint"]) -> rx.Component:
    return rx.box(
        text,
        style={
            "display": "inline-block",
            "padding": "4px 12px",
            "border_radius": "999px",
            "font_size": "15px",
            "background": color,
            "border": f"1px solid {COLOR['line']}",
        },
    )


def text_field(placeholder: str, value: rx.Var, on_change, **extra) -> rx.Component:
    return rx.input(
        placeholder=placeholder, value=value, on_change=on_change, style=FIELD_STYLE, **extra
    )


def language_picker(value: rx.Var, on_pick) -> rx.Component:
    """Three big buttons, the selected one filled -- not a drop-down. `on_pick`
    is an event handler taking the language code."""
    return rx.hstack(
        *[
            rx.button(
                name,
                on_click=on_pick(code),
                style={
                    "min_height": "56px",
                    "flex": "1",
                    "font_size": "18px",
                    "font_weight": "700",
                    "border_radius": "12px",
                    "cursor": "pointer",
                    "background": rx.cond(value == code, COLOR["green"], COLOR["card"]),
                    "color": rx.cond(value == code, "white", COLOR["green"]),
                    "border": f"2px solid {COLOR['green']}",
                },
            )
            for code, name in LANGUAGES
        ],
        spacing="2",
        width="100%",
    )


def qr_data_uri(text: str) -> str:
    """An SVG data URI of `text` as a QR code, or "" if the optional `segno`
    package is not installed (the page then shows the code as text only)."""
    try:
        import segno
    except ImportError:
        return ""
    return segno.make(text, error="m").svg_data_uri(scale=6, border=2)
