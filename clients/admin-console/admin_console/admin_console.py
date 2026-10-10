"""
The moderator console (M4, Week 7).

What the proposal asks of this screen (section 7.3): a review queue, the
original and the translation side by side, one-tap release/block, an
appeal thread, and a full audit trail. This scaffold covers the queue, the
side-by-side view, release/block, and the trail. Appeals are Week 9.

What it is NOT, yet: wired to the gateway. The routes it needs
(GET /moderation/queue, POST .../release|block, GET .../events) are M2's
side of issue #65, proposed as contracts/chat/moderation.py in PR #81.
Until they exist this runs on committed fixtures -- see source.py, where
swapping backends is a config change rather than a rewrite.

Three things in here are product requirements, not styling choices, and
should survive any redesign:

1. **Original and pivot are shown together, always.** A moderator reading
   only the English pivot is reviewing a translation of what the sender
   wrote, and devotional idiom is exactly where that goes wrong (the
   "kill my ego" case in the fixtures is the example).
2. **A degraded hold looks different from a judgement.** When the
   classifier failed closed there is no model opinion to read, and
   presenting `confidence 0.0` as if it were a verdict would mislead.
3. **The sender's notice is shown.** "Never silently" (proposal section
   15) is a promise about the sender's experience; a moderator should be
   able to see what the sender was actually told.
"""

from __future__ import annotations

import dataclasses
import uuid

import reflex as rx

from admin_console.org import register_org_pages
from admin_console.source import (
    QueueSource,
    ReviewConflict,
    SourceError,
    build_source_from_env,
)
from contracts.chat.moderation import ModerationReviewIn

COLOR = {
    "ink": "#1f2933",
    "muted": "#6b7280",
    "line": "#e5e7eb",
    "card": "#ffffff",
    "page": "#f7f7f5",
    "hold": "#b45309",
    "block": "#b91c1c",
    "release": "#15803d",
    "degraded": "#7c3aed",
}

# Chosen by the environment (CONSOLE_SOURCE, see source.py), fixture by default.
_source: QueueSource = build_source_from_env()


def set_source(source: QueueSource) -> None:
    """Swap the backend. Called by tests, and by whatever wires the real
    gateway in once PR #81's routes exist."""
    global _source
    _source = source


@dataclasses.dataclass
class Row:
    """Flattened queue item. Reflex state vars need plain serialisable
    fields, not the contract's nested pydantic models -- and `rx.Base` no
    longer exists in Reflex 0.9.9, so this is a dataclass."""

    message_id: str
    author: str
    target: str
    original_text: str
    original_language: str
    media_uri: str
    pivot_text: str
    has_pivot: bool
    label: str
    action: str
    confidence: str
    rationale: str
    notice_text: str
    degraded: bool
    event_count: int
    latest_event_id: str


@dataclasses.dataclass
class TrailRow:
    actor: str
    label: str
    action: str
    rationale: str
    note: str
    created_at: str
    degraded: bool


def _to_row(item) -> Row:
    e = item.latest_event
    return Row(
        message_id=str(item.message_id),
        author=item.author_display_name,
        target=f"{item.target_type}",
        original_text=item.original_text or "",
        original_language=item.original_language or "",
        media_uri=item.original_media_ref.uri if item.original_media_ref else "",
        pivot_text=item.pivot_text_en or "",
        has_pivot=item.pivot_text_en is not None,
        label=e.label.value,
        action=e.action.value,
        confidence="—" if e.confidence is None else f"{e.confidence:.2f}",
        rationale=e.rationale,
        notice_text=e.notice_text or "",
        degraded=e.degraded,
        event_count=item.event_count,
        latest_event_id=str(e.id),
    )


class State(rx.State):
    rows: list[Row] = []
    selected_id: str = ""
    trail: list[TrailRow] = []
    note: str = ""
    banner: str = ""
    banner_is_error: bool = False
    source_label: str = ""

    def _say(self, message: str, *, error: bool) -> None:
        """Add to the banner rather than replace it. A decision that went
        through and a queue refresh that then failed are both things the
        moderator needs to know; letting the second overwrite the first
        would make a successful release look like it had not happened."""
        self.banner = f"{self.banner} {message}".strip() if self.banner else message
        self.banner_is_error = self.banner_is_error or error

    def load_queue(self) -> None:
        self.source_label = _source.label
        try:
            items = _source.fetch_queue().items
        except SourceError as exc:
            # Keep what was on screen: blanking the queue would read as
            # "nothing is waiting", which is the one wrong thing to imply
            # when the truth is "I could not ask".
            self._say(
                f"Could not refresh the queue — {exc} Showing the last list loaded.", error=True
            )
            return
        self.rows = [_to_row(i) for i in items]
        if self.selected_id and all(r.message_id != self.selected_id for r in self.rows):
            self.selected_id = ""
            self.trail = []

    def select(self, message_id: str) -> None:
        self.selected_id = message_id
        self.note = ""
        self.banner = ""
        self.banner_is_error = False
        try:
            out = _source.fetch_events(uuid.UUID(message_id))
        except SourceError as exc:
            self.trail = []
            self._say(f"Could not load the audit trail — {exc}", error=True)
            return
        self.trail = [
            TrailRow(
                actor=e.actor_kind.value,
                label=e.label.value,
                action=e.action.value,
                rationale=e.rationale,
                note=e.note or "",
                created_at=e.created_at.strftime("%Y-%m-%d %H:%M"),
                degraded=e.degraded,
            )
            for e in out.events
        ]

    def set_note(self, value: str) -> None:
        self.note = value

    @property
    def _selected_row(self) -> Row | None:
        return next((r for r in self.rows if r.message_id == self.selected_id), None)

    def _review(self, release: bool) -> None:
        row = self._selected_row
        if row is None:
            return
        body = ModerationReviewIn(
            note=self.note or None,
            # Optimistic concurrency: if someone else decided this message
            # while it sat open here, the source raises rather than
            # overwriting a decision this moderator never saw.
            expected_event_id=uuid.UUID(row.latest_event_id),
        )
        try:
            out = _source.review(uuid.UUID(self.selected_id), release=release, body=body)
        except ReviewConflict as exc:
            self.banner = ""
            self.banner_is_error = False
            self._say(f"Not applied — {exc}", error=True)
            self.load_queue()
            return
        except SourceError as exc:
            # Not applied, and not a conflict: the decision did not happen.
            # Keep the selection so the moderator can simply try again.
            self.banner = ""
            self.banner_is_error = False
            self._say(f"Not applied — {exc}", error=True)
            return
        verb = "Released" if release else "Blocked"
        self.banner = ""
        self.banner_is_error = False
        self._say(
            f"{verb}. Sender notified."
            if out.notice_sent
            else f"{verb}, but the sender has NOT been notified yet.",
            error=not out.notice_sent,
        )
        self.selected_id = ""
        self.trail = []
        self.load_queue()

    def release(self) -> None:
        self._review(release=True)

    def block(self) -> None:
        self._review(release=False)


def _pill(text: rx.Var | str, color: str) -> rx.Component:
    return rx.text(
        text,
        style={
            "font_size": "0.75rem",
            "font_weight": "700",
            "color": color,
            "border": f"1px solid {color}",
            "border_radius": "999px",
            "padding": "1px 8px",
        },
    )


def queue_row(row: Row) -> rx.Component:
    return rx.box(
        rx.hstack(
            _pill(row.label, COLOR["hold"]),
            rx.cond(row.degraded, _pill("no model verdict", COLOR["degraded"]), rx.fragment()),
            rx.spacer(),
            rx.cond(
                row.event_count > 1,
                rx.text(
                    f"{row.event_count} events",
                    style={"font_size": "0.75rem", "color": COLOR["muted"]},
                ),
                rx.fragment(),
            ),
            width="100%",
        ),
        rx.text(row.author, style={"font_weight": "700", "color": COLOR["ink"]}),
        rx.text(
            rx.cond(row.has_pivot, row.pivot_text, row.original_text),
            no_of_lines=2,
            style={"font_size": "0.9rem", "color": COLOR["muted"]},
        ),
        on_click=lambda: State.select(row.message_id),
        style={
            "padding": "12px 14px",
            "border_bottom": f"1px solid {COLOR['line']}",
            "cursor": "pointer",
            "background": rx.cond(State.selected_id == row.message_id, "#eef2ff", "transparent"),
        },
    )


def side_by_side(row: Row) -> rx.Component:
    """Requirement, not layout preference -- see this module's docstring."""
    return rx.hstack(
        rx.vstack(
            rx.text(
                f"Original ({row.original_language})",
                style={"font_size": "0.75rem", "color": COLOR["muted"], "font_weight": "700"},
            ),
            rx.cond(
                row.original_text != "",
                rx.text(row.original_text, style={"color": COLOR["ink"]}),
                rx.text(
                    f"Voice note — {row.media_uri}",
                    style={"color": COLOR["muted"], "font_style": "italic"},
                ),
            ),
            align_items="flex-start",
            width="50%",
        ),
        rx.vstack(
            rx.text(
                "English pivot (what the classifier read)",
                style={"font_size": "0.75rem", "color": COLOR["muted"], "font_weight": "700"},
            ),
            rx.cond(
                row.has_pivot,
                rx.text(row.pivot_text, style={"color": COLOR["ink"]}),
                rx.text(
                    "No pivot — the pipeline failed before producing one. "
                    "You are reviewing the original alone.",
                    style={"color": COLOR["degraded"], "font_style": "italic"},
                ),
            ),
            align_items="flex-start",
            width="50%",
        ),
        align_items="flex-start",
        spacing="4",
        width="100%",
    )


def detail(row: Row) -> rx.Component:
    return rx.vstack(
        rx.heading(row.author, size="5"),
        side_by_side(row),
        rx.divider(),
        rx.cond(
            row.degraded,
            rx.callout(
                "The classifier failed closed on this message — there is no model "
                "verdict to weigh. Decide on the text alone.",
                color_scheme="purple",
            ),
            rx.text(
                f"Classifier: {row.label} · {row.action} · confidence {row.confidence}",
                style={"font_size": "0.85rem", "color": COLOR["muted"]},
            ),
        ),
        rx.text(row.rationale, style={"font_size": "0.9rem", "color": COLOR["ink"]}),
        rx.cond(
            row.notice_text != "",
            rx.box(
                rx.text(
                    "The sender was told:",
                    style={"font_size": "0.75rem", "color": COLOR["muted"], "font_weight": "700"},
                ),
                rx.text(row.notice_text, style={"font_size": "0.9rem"}),
                style={
                    "background": COLOR["page"],
                    "padding": "8px 10px",
                    "border_radius": "8px",
                    "width": "100%",
                },
            ),
            rx.fragment(),
        ),
        rx.divider(),
        rx.text_area(
            value=State.note,
            on_change=State.set_note,
            placeholder="Note for the record (what the appeal will be answered against)",
            style={"width": "100%", "min_height": "70px"},
        ),
        rx.hstack(
            rx.button("Release", on_click=State.release, color_scheme="green"),
            rx.button("Block", on_click=State.block, color_scheme="red"),
            spacing="3",
        ),
        rx.divider(),
        rx.text("Audit trail", style={"font_weight": "700", "color": COLOR["ink"]}),
        rx.foreach(
            State.trail,
            lambda t: rx.box(
                rx.hstack(
                    _pill(t.actor, COLOR["muted"]),
                    rx.text(f"{t.label} · {t.action}", style={"font_size": "0.85rem"}),
                    rx.spacer(),
                    rx.text(t.created_at, style={"font_size": "0.75rem", "color": COLOR["muted"]}),
                    width="100%",
                ),
                rx.text(t.rationale, style={"font_size": "0.85rem", "color": COLOR["muted"]}),
                rx.cond(
                    t.note != "",
                    rx.text(f"Note: {t.note}", style={"font_size": "0.85rem"}),
                    rx.fragment(),
                ),
                style={"padding": "8px 0", "border_bottom": f"1px solid {COLOR['line']}"},
            ),
        ),
        align_items="flex-start",
        spacing="3",
        style={"padding": "18px 20px", "width": "100%"},
    )


def index() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.heading("Moderator console", size="6"),
            rx.link("Members, circles, announcements", href="/members"),
            rx.spacer(),
            rx.text(
                State.source_label,
                style={"font_size": "0.8rem", "color": COLOR["muted"]},
            ),
            width="100%",
            style={"padding": "14px 20px", "border_bottom": f"1px solid {COLOR['line']}"},
        ),
        rx.cond(
            State.banner != "",
            rx.callout(
                State.banner,
                color_scheme=rx.cond(State.banner_is_error, "red", "green"),
                style={"margin": "10px 20px", "width": "calc(100% - 40px)"},
            ),
            rx.fragment(),
        ),
        rx.hstack(
            rx.box(
                rx.cond(
                    State.rows.length() > 0,
                    rx.foreach(State.rows, queue_row),
                    rx.text(
                        "Nothing waiting for review.",
                        style={"padding": "20px", "color": COLOR["muted"]},
                    ),
                ),
                style={
                    "width": "360px",
                    "border_right": f"1px solid {COLOR['line']}",
                    "height": "100%",
                    "overflow_y": "auto",
                },
            ),
            rx.box(
                rx.cond(
                    State.selected_id != "",
                    rx.foreach(
                        State.rows.to(list[Row]),
                        lambda r: rx.cond(
                            r.message_id == State.selected_id, detail(r), rx.fragment()
                        ),
                    ),
                    rx.text(
                        "Select a message to review.",
                        style={"padding": "20px", "color": COLOR["muted"]},
                    ),
                ),
                style={"flex": "1", "height": "100%", "overflow_y": "auto"},
            ),
            align_items="stretch",
            spacing="0",
            width="100%",
            style={"flex": "1", "min_height": "0"},
        ),
        spacing="0",
        style={"height": "100vh", "background": COLOR["card"], "color": COLOR["ink"]},
        on_mount=State.load_queue,
    )


app = rx.App()
app.add_page(index, title="SatSandesh — Moderator console")
register_org_pages(app)
