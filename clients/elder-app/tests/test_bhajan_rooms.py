"""The bhajan rooms page (elder_app/pages/bhajan_rooms.py and assets/bhajan_rooms.js).

The screen itself is JavaScript and is tested under Node (tests/js/); these are the checks that
belong on the Python side: both languages have every string the screen asks for, the page and the
home-screen link build, the page is registered at its own route, and the files it loads exist.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from elder_app.pages import bhajan_rooms as page

ASSETS = Path(__file__).resolve().parents[1] / "assets"
JS_TESTS = Path(__file__).resolve().parent / "js"


def _keys_the_screen_asks_for() -> set[str]:
    source = (ASSETS / "bhajan_rooms.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'\bT\("([a-z_]+)"', source))
    keys |= set(re.findall(r'"(err_[a-z]+)"', source))  # errorKey() names them without T(
    return keys


def test_both_languages_have_the_same_strings() -> None:
    assert set(page.TEXTS["en"]) == set(page.TEXTS["te"])
    assert all(page.TEXTS["en"].values()) and all(page.TEXTS["te"].values())


def test_every_string_the_screen_asks_for_exists_in_both_languages() -> None:
    wanted = _keys_the_screen_asks_for()
    assert wanted, "the screen asks for no strings?"
    for lang in ("en", "te"):
        assert wanted <= set(page.TEXTS[lang]), (lang, sorted(wanted - set(page.TEXTS[lang])))


def test_the_place_in_line_keeps_its_number_slot_in_both_languages() -> None:
    for lang in ("en", "te"):
        assert "{n}" in page.TEXTS[lang]["your_place"]


def test_the_boot_script_carries_the_gateway_the_colours_and_the_words() -> None:
    js = page.boot_js()
    config = json.loads(js[js.index("const config = ") + len("const config = ") : js.index(";\n")])
    from elder_app.elder_app import GATEWAY_PUBLIC_URL

    assert config["gateway"] == GATEWAY_PUBLIC_URL
    assert set(config["colors"]) == set(page._COLOR_KEYS)
    assert config["texts"]["te"]["title"] == page.TEXTS["te"]["title"]
    assert "/bhajan_rooms.js" in js  # loaded only on this page


def test_the_page_and_the_home_link_build() -> None:
    assert page.bhajan_rooms_page() is not None
    assert page.rooms_link() is not None
    assert page.ROUTE == "/bhajans"


def test_the_page_has_its_own_route_that_is_not_an_api_path() -> None:
    # Caddy sends /rooms* to the gateway; the page must not live there.
    assert not page.ROUTE.startswith("/rooms")


def test_the_page_is_registered_and_the_home_screen_links_to_it() -> None:
    import inspect

    from elder_app import elder_app

    assert "register_bhajan_rooms(app)" in inspect.getsource(elder_app)
    assert "rooms_link()" in inspect.getsource(elder_app.home_screen)


def test_the_files_the_page_loads_exist() -> None:
    assert (ASSETS / "bhajan_rooms.js").is_file()
    library = ASSETS / "livekit-client.umd.min.js"
    assert library.is_file() and library.stat().st_size > 100_000


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_screen_logic_passes_under_node() -> None:
    done = subprocess.run(
        ["node", "--test", str(JS_TESTS / "bhajan_rooms.test.js")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-500:]


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_the_colours_the_screen_uses_are_readable_against_each_other() -> None:
    """WCAG contrast of the pairs the screen draws: 4.5:1 for text, 3:1 for a field's edge and a
    focus ring. The pale `warm_border` is 1.2:1 on the cream and is for decoration only (#153)."""
    from elder_app.elder_app import COLOR

    text_pairs = [
        ("ink", "cream_canvas"),
        ("ink", "card_cream"),
        ("muted_ink", "cream_canvas"),
        ("green_ink", "green_tint"),
        ("card_cream", "deep_green"),
    ]
    for fg, bg in text_pairs:
        assert _contrast(COLOR[fg], COLOR[bg]) >= 4.5, (fg, bg)
    assert _contrast("#8A3E0F", "#FBEFE6") >= 4.5  # the danger button's text
    for edge, ground in [("muted_ink", "card_cream"), ("muted_ink", "cream_canvas"), ("deep_green", "cream_canvas")]:
        assert _contrast(COLOR[edge], COLOR[ground]) >= 3.0, (edge, ground)
    assert _contrast(COLOR["warm_border"], COLOR["cream_canvas"]) < 3.0  # why it must not be a field edge


def test_fields_do_not_use_the_pale_border_and_the_page_asks_for_light_controls() -> None:
    source = (ASSETS / "bhajan_rooms.js").read_text(encoding="utf-8")
    field = next(line for line in source.splitlines() if line.strip().startswith("const field ="))
    assert "c.muted_ink" in field and "warm_border" not in field
    assert '"color_scheme": "light"' in Path(page.__file__).read_text(encoding="utf-8")
