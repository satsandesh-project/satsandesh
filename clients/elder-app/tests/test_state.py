"""Tests for elder_app.elder_app.State -- the Week 5 accessibility pass
(PR #45): quiet-hours settings and the tap-and-hold-to-hear-it audio
labels.

Reflex blocks direct State instantiation outside its own runtime unless
`PYTEST_CURRENT_TEST` is set (`reflex.state.is_testing_env`) -- true
under plain `pytest`, so no harness/fixture is needed to construct one
here.

The double-fire regression (release handlers)
----------------------------------------------
Reviewed on PR #45: a plain `on_click` alongside `on_mouse_down`/`up`
double-fired the real action even on a long hold, because a mouseup on
the same element always fires a click in the browser regardless of press
duration -- holding "Send" to preview it also actually sent the message.
Fixed by routing every real action through the button's `on_mouse_up`
only, gated on `AUDIO_LABEL_HOLD_RELEASE_JS`'s "short"/"held" verdict
(see that constant's own comment in elder_app.py for the JS-side half of
this). These tests are the Python-side regression coverage for that fix:
"held" must never perform the real action, "short" always must. The JS
timer itself isn't practical to unit test headless, so it's exercised
only through this boundary.
"""

from elder_app.elder_app import State


def _fresh_state() -> State:
    return State()


def test_release_back_short_goes_home():
    state = _fresh_state()
    state.current_contact_id = "abc"
    state.current_circle_id = "def"

    state.on_release_back("short")

    assert state.current_contact_id == ""
    assert state.current_circle_id == ""


def test_release_back_held_does_not_go_home():
    state = _fresh_state()
    state.current_contact_id = "abc"
    state.current_circle_id = "def"

    state.on_release_back("held")

    assert state.current_contact_id == "abc"
    assert state.current_circle_id == "def"


def test_release_send_short_triggers_send_event():
    state = _fresh_state()

    event = state.on_release_send("short")

    assert event is not None


def test_release_send_held_triggers_nothing():
    state = _fresh_state()

    event = state.on_release_send("held")

    assert event is None


def test_release_satsang_tab_short_switches_tab():
    state = _fresh_state()
    state.active_tab = "people"

    state.on_release_satsang_tab("short")

    assert state.active_tab == "satsang"


def test_release_satsang_tab_held_leaves_tab_alone():
    state = _fresh_state()
    state.active_tab = "people"

    state.on_release_satsang_tab("held")

    assert state.active_tab == "people"


def test_release_add_person_short_opens_the_form():
    state = _fresh_state()
    state.add_contact_open = False

    state.on_release_add_person("short")

    assert state.add_contact_open is True


def test_release_add_person_held_leaves_form_closed():
    state = _fresh_state()
    state.add_contact_open = False

    state.on_release_add_person("held")

    assert state.add_contact_open is False


def test_release_quiet_hours_save_short_triggers_save():
    state = _fresh_state()
    state.quiet_hours_saved = True  # a stale "Saved" flash from a previous save

    event = state.on_release_quiet_hours_save("short")

    assert event is not None
    # save_quiet_hours() itself clears this before issuing the save call.
    assert state.quiet_hours_saved is False


def test_release_quiet_hours_save_held_triggers_nothing():
    state = _fresh_state()
    state.quiet_hours_saved = True

    event = state.on_release_quiet_hours_save("held")

    assert event is None
    assert state.quiet_hours_saved is True  # untouched


def test_on_settings_loaded_parses_hh_mm_ss_into_hh_mm():
    state = _fresh_state()

    state.on_settings_loaded('{"quiet_hours_start": "21:30:00", "quiet_hours_end": "06:00:00"}')

    assert state.quiet_hours_start_input == "21:30"
    assert state.quiet_hours_end_input == "06:00"


def test_on_settings_loaded_null_values_clear_the_inputs():
    state = _fresh_state()
    state.quiet_hours_start_input = "21:30"
    state.quiet_hours_end_input = "06:00"

    state.on_settings_loaded('{"quiet_hours_start": null, "quiet_hours_end": null}')

    assert state.quiet_hours_start_input == ""
    assert state.quiet_hours_end_input == ""


def test_on_settings_loaded_empty_result_leaves_inputs_untouched():
    # LOAD_SETTINGS_JS_TEMPLATE returns "" on a fetch failure or a
    # not-yet-built endpoint -- must fail soft, not clear or crash.
    state = _fresh_state()
    state.quiet_hours_start_input = "21:30"

    state.on_settings_loaded("")

    assert state.quiet_hours_start_input == "21:30"


def test_on_settings_loaded_malformed_json_leaves_inputs_untouched():
    state = _fresh_state()
    state.quiet_hours_start_input = "21:30"

    state.on_settings_loaded("not json")

    assert state.quiet_hours_start_input == "21:30"
