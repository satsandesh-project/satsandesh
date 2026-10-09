"""
The organisation pages, for someone using a keyboard and a screen reader.

Kshitiz asked for this pass on #132 (for volunteers who are not technical), and a
layout test cannot see it, so it is checked on the rendered component tree:

- every field, drop-down and tick-box has a NAME (a placeholder is not one);
- a button that is repeated down a list says whose it is ("Remove Asha", not
  just "Remove" six times);
- a button that is one of a set of choices says whether it is the selected one;
- messages land in a live region that is on the page before the message is, so a
  screen reader announces them;
- the QR image has a text alternative.

What this cannot replace is using the pages with a screen reader. It stops the
easy regressions: a field added with no name, a drop-down whose label never
reaches the page.
"""

from __future__ import annotations

import pytest
from admin_console.org import announce, circles, members

PAGES = {
    "members": members.members_page,
    "circles": circles.circles_page,
    "announce": announce.announce_page,
}


def _nodes(tree) -> list[dict]:
    found: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if "name" in node and "props" in node:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tree)
    return found


def _named(node: dict) -> bool:
    return any(prop.startswith('"aria-label"') for prop in node.get("props", []))


def _render(page) -> tuple[list[dict], str]:
    """The nodes, and every prop of every node as one searchable string. (Not the JSON
    dump of the tree: that escapes the quotes inside each prop.)"""
    nodes = _nodes(page().render())
    return nodes, "\n".join(prop for node in nodes for prop in node["props"])


@pytest.mark.parametrize("page", PAGES.values(), ids=PAGES.keys())
def test_every_field_dropdown_and_tickbox_has_a_name(page) -> None:
    nodes, _ = _render(page)
    # Text inputs and text areas are wrapped in DebounceInput, which carries the props.
    controls = [
        n
        for n in nodes
        if n["name"] in ("DebounceInput", "RadixThemesTextArea", "RadixThemesCheckbox")
        or n["name"] == "RadixThemesSelect.Trigger"
    ]
    nameless = [n["name"] for n in controls if not _named(n)]
    assert not nameless, f"unnamed controls: {nameless}"


@pytest.mark.parametrize("page", PAGES.values(), ids=PAGES.keys())
def test_no_dropdown_carries_its_name_only_on_the_root(page) -> None:
    """The Radix Select root draws no element, so a label there never reaches the
    page. The name belongs on the trigger (ui.select puts it there)."""
    nodes, _ = _render(page)
    on_root = [n for n in nodes if n["name"] == "RadixThemesSelect.Root" and _named(n)]
    assert on_root == []


def test_the_buttons_repeated_down_a_list_say_whose_they_are() -> None:
    _, members_page = _render(members.members_page)
    _, circles_page = _render(circles.circles_page)
    assert '"Sign-in code for "' in members_page
    assert '"Role of "' in members_page
    assert '"Open "' in circles_page
    assert '"Remove "' in circles_page
    assert '"Yes, take "' in circles_page and '"No, keep "' in circles_page
    assert '"Role of "' in circles_page


def test_a_choice_between_buttons_says_which_is_selected() -> None:
    for page in (members.members_page, circles.circles_page, announce.announce_page):
        _, text = _render(page)
        assert "aria-pressed" in text, page.__name__


def test_the_three_languages_form_a_named_group_of_pressable_buttons() -> None:
    _, text = _render(members.members_page)
    assert 'role:"group"' in text and '"aria-label":"Language"' in text
    assert text.count("Language: ") >= 3, "each of the three buttons names its language"


@pytest.mark.parametrize("page", PAGES.values(), ids=PAGES.keys())
def test_messages_land_in_a_live_region_that_is_always_on_the_page(page) -> None:
    nodes, text = _render(page)
    region = [n for n in nodes if any('"aria-live":"polite"' in p for p in n["props"])]
    assert len(region) == 1
    assert any('role:"status"' in p or '"role":"status"' in p for p in region[0]["props"])
    # The region itself is not conditional: the message inside it is.
    assert region[0]["name"] == "RadixThemesBox"


def test_the_qr_image_has_a_text_alternative() -> None:
    _, text = _render(members.members_page)
    assert "QR code of the sign-in code below, for " in text
