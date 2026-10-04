"""Tests for elder_app.elder_app -- the Week 5 accessibility pass (PR #45):
quiet-hours settings and the tap-and-hold-to-hear-it audio labels; and
Week 6's voice capture (record, upload, send).

Reflex blocks direct State instantiation outside its own runtime unless
`PYTEST_CURRENT_TEST` is set (`reflex.state.is_testing_env`) -- true
under plain `pytest`, so no harness/fixture is needed to construct one
here.

The double-fire / keyboard-access regression
---------------------------------------------
Two rounds of review on PR #45, on the same five buttons:

1. A plain `on_click` alongside `on_mouse_down`/`up` double-fired the
   real action even on a long hold -- a mouseup on the same element
   always fires a click in the browser regardless of press duration, so
   holding "Send" to preview it also actually sent the message.
2. The fix for #1 removed `on_click` and routed the real action through
   `on_mouse_up` instead -- which broke keyboard activation, since
   pressing Enter/Space on a focused button fires a native click with no
   mousedown/mouseup at all.

The actual fix keeps `on_click` as the one real action (mouse and
keyboard both), and suppresses only the specific click that follows a
completed mouse hold, at the DOM level, via a capture-phase listener
installed by `AUDIO_LABEL_HOLD_START_JS_TEMPLATE`. That JS-level
click-guard isn't practical to unit test headless (no DOM here) --
`test_all_five_hold_buttons_keep_their_on_click` below is the
Python-side regression coverage available: a plain structural check that
every button carrying the hold affordance still has its own `on_click`
wired, so a future edit can't silently drop it again the way review
round 1 did.
"""

from elder_app.elder_app import (
    CHAT_CONNECT_JS_TEMPLATE,
    CHAT_SEND_JS,
    RECEIVER_TEXT_KEYS,
    SAVE_PREFS_JS_TEMPLATE,
    SAVE_SETTINGS_JS_TEMPLATE,
    STATUS_TEXT_KEYS,
    TEXTS,
    TOUCH_HOLD_SHIM_JS,
    UPLOAD_AND_SEND_VOICE_JS_TEMPLATE,
    State,
    add_person_button,
    bottom_tabs,
    chat_screen,
    mic_button,
    settings_card,
)


def _fresh_state() -> State:
    return State()


def _has_event_trigger(component, name: str) -> bool:
    return name in component.event_triggers


def _on_mouse_down_handler_name(component) -> str | None:
    chain = component.event_triggers.get("on_mouse_down")
    if chain is None or not chain.events:
        return None
    return chain.events[0].handler.fn.__name__


def test_all_five_hold_buttons_keep_their_on_click():
    # add_person_button() and settings_card() are each a single button
    # carrying the hold affordance (settings_card grew a language picker
    # and a TTS toggle in Week 7, but only its original Save button wires
    # on_mouse_down=start_audio_label_hold -- the new buttons don't touch
    # this affordance at all). bottom_tabs() and chat_screen() contain
    # more than one -- walk their children to find the ones that carry
    # the audio-label hold affordance specifically (on_mouse_down wired
    # to start_audio_label_hold, not mic_button's own
    # on_mouse_down=start_recording, which legitimately has no on_click
    # -- it's a press-and-hold record control, not a tap action), and
    # assert each still has on_click too.
    def hold_buttons(component):
        found = []
        if _on_mouse_down_handler_name(component) == "start_audio_label_hold":
            found.append(component)
        for child in getattr(component, "children", []):
            found.extend(hold_buttons(child))
        return found

    candidates = (
        hold_buttons(add_person_button())
        + hold_buttons(settings_card())
        + hold_buttons(bottom_tabs())
        + hold_buttons(chat_screen())
    )

    assert len(candidates) == 5, f"expected 5 hold-affordance buttons, found {len(candidates)}"
    for component in candidates:
        assert _has_event_trigger(component, "on_click"), (
            f"{component} carries the tap-and-hold affordance (on_mouse_down) "
            "but lost its on_click -- this is exactly the keyboard-access "
            "regression from PR #45's review round 2."
        )


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


# -- Week 7: preferred content language + TTS on/off ------------------------


def test_on_settings_loaded_parses_preferred_language_and_tts_on():
    state = _fresh_state()

    state.on_settings_loaded('{"preferred_language": "hi", "tts_on": false}')

    assert state.preferred_language_input == "hi"
    assert state.tts_on_input is False


def test_on_settings_loaded_without_these_fields_leaves_the_join_time_default():
    # An older/not-yet-updated /me/settings response (or the endpoint not
    # existing at all yet) must not silently reset what was picked at
    # onboarding -- only overwrite when the server actually sent a value.
    state = _fresh_state()
    state.preferred_language_input = "te"
    state.tts_on_input = False

    state.on_settings_loaded('{"quiet_hours_start": null, "quiet_hours_end": null}')

    assert state.preferred_language_input == "te"
    assert state.tts_on_input is False


def test_set_preferred_language_input_updates_and_clears_saved_flag():
    state = _fresh_state()
    state.settings_saved = True

    state.set_preferred_language_input("hi")

    assert state.preferred_language_input == "hi"
    assert state.settings_saved is False


def test_set_tts_on_input_updates_and_clears_saved_flag():
    state = _fresh_state()
    state.tts_on_input = True
    state.settings_saved = True

    state.set_tts_on_input(False)

    assert state.tts_on_input is False
    assert state.settings_saved is False


def test_save_settings_resets_saved_and_error_flags_and_returns_an_event():
    state = _fresh_state()
    state.settings_saved = True
    state.settings_error = True

    event = state.save_settings()

    assert state.settings_saved is False
    assert state.settings_error is False
    assert event is not None


def test_join_circle_includes_a_settings_save_so_the_picked_language_persists():
    state = _fresh_state()
    state.display_name_input = "Test Elder"
    state.preferred_language_input = "hi"

    events = state.join_circle()

    assert events is not None
    assert len(events) == 3  # save name, connect_chat, save_settings
    assert state.joined is True
    assert state.my_display_name == "Test Elder"


# -- Week 6: voice capture send/upload state -------------------------------
#
# The actual upload (POST /media) and the retry-once-then-surface logic
# live entirely in UPLOAD_AND_SEND_VOICE_JS_TEMPLATE -- not practical to
# unit test headless, same reasoning as the audio-label click-guard above.
# What's tested here is the Python-side state machine around it: does
# starting a send show the right "uploading" state, and does the result
# callback land on the right outcome for "ok" vs any failure.


def test_send_voice_recording_marks_uploading_and_returns_an_event():
    state = _fresh_state()
    state.voice_send_status = ""

    event = state.send_voice_recording()

    assert state.voice_send_status == "uploading"
    assert event is not None


def test_on_voice_send_result_ok_clears_the_recording():
    state = _fresh_state()
    state.voice_send_status = "uploading"
    state.last_recording_data_url = "data:audio/webm;base64,abc123"

    state.on_voice_send_result("ok")

    assert state.voice_send_status == ""
    assert state.last_recording_data_url == ""


def test_on_voice_send_result_failure_keeps_the_recording_for_retry():
    state = _fresh_state()
    state.voice_send_status = "uploading"
    state.last_recording_data_url = "data:audio/webm;base64,abc123"

    state.on_voice_send_result("error:upload_failed")

    assert state.voice_send_status == "failed"
    # The recording itself must survive a failure -- discarding it here
    # would mean a dropped connection costs the elder their whole
    # recording, not just the upload attempt.
    assert state.last_recording_data_url == "data:audio/webm;base64,abc123"


def test_discard_recording_resets_both_the_url_and_the_send_status():
    state = _fresh_state()
    state.last_recording_data_url = "data:audio/webm;base64,abc123"
    state.voice_send_status = "failed"

    state.discard_recording()

    assert state.last_recording_data_url == ""
    assert state.voice_send_status == ""


def test_typed_messages_declare_their_language_and_resends_keep_it():
    # The pipeline translates from source_lang and otherwise falls back to the author's
    # stored language (Telugu for everyone today), so a Hindi message would be treated
    # as Telugu. These pieces must all stay wired: the detector, the send, the resend.
    assert "window.__satDetectLang = function" in CHAT_CONNECT_JS_TEMPLATE
    assert "0x0C00" in CHAT_CONNECT_JS_TEMPLATE and "0x0900" in CHAT_CONNECT_JS_TEMPLATE
    assert "source_lang: sourceLang" in CHAT_SEND_JS
    assert "source_lang: msg.source_lang" in CHAT_CONNECT_JS_TEMPLATE


def test_receiver_text_exists_in_both_ui_languages():
    # The chat JS is built once outside Reflex and is handed both languages; a key
    # missing from one of them would be a KeyError when the connection is set up.
    for lang in ("en", "te"):
        for key in RECEIVER_TEXT_KEYS:
            assert TEXTS[lang].get(key), f"{key} missing or empty in {lang}"


def test_on_prefs_loaded_restores_saved_choices():
    state = _fresh_state()

    state.on_prefs_loaded('{"lang": "hi", "tts": false, "autoplay": true}')

    assert state.preferred_language_input == "hi"
    assert state.tts_on_input is False
    assert state.autoplay_input is True


def test_on_prefs_loaded_ignores_missing_or_corrupt_data():
    state = _fresh_state()

    state.on_prefs_loaded("")
    state.on_prefs_loaded("not json")
    state.on_prefs_loaded('{"lang": "xx", "tts": "yes", "autoplay": 1}')

    # An unknown language and non-boolean flags must not overwrite the defaults.
    assert state.preferred_language_input == "en"
    assert state.tts_on_input is True
    assert state.autoplay_input is False


def test_autoplay_is_off_by_default_and_the_setter_changes_it():
    state = _fresh_state()
    assert state.autoplay_input is False

    state.set_autoplay_input(True)

    assert state.autoplay_input is True


def test_chat_js_draws_translated_and_original_views():
    # The chat JS is a browser-side string, not unit-testable headless; this guards the
    # parts a refactor could silently drop.
    from elder_app.elder_app import CHAT_CONNECT_JS_TEMPLATE

    assert "buildMessageBody" in CHAT_CONNECT_JS_TEMPLATE
    assert "Authorization" in CHAT_CONNECT_JS_TEMPLATE.split("function loadAudioUrl")[1][:400]
    assert "renderings: data.renderings" in CHAT_CONNECT_JS_TEMPLATE
    assert "renderings: m.renderings" in CHAT_CONNECT_JS_TEMPLATE


def _hold_marker(component) -> str | None:
    return dict(component.custom_attrs or {}).get("data-sat-hold")


def _components_with_hold_marker(component) -> list:
    found = [component] if _hold_marker(component) else []
    for child in getattr(component, "children", []):
        found.extend(_components_with_hold_marker(child))
    return found


def test_touch_hold_markers_cover_every_hold_button_and_the_mic():
    # Reflex has no touch triggers, so TOUCH_HOLD_SHIM_JS finds its targets by the
    # data-sat-hold attribute. A hold button without it silently stays mouse-only
    # (it works on a laptop, so nobody notices) -- the exact gap this guards.
    marked = (
        _components_with_hold_marker(add_person_button())
        + _components_with_hold_marker(settings_card())
        + _components_with_hold_marker(bottom_tabs())
        + _components_with_hold_marker(chat_screen())
    )
    assert [_hold_marker(c) for c in marked].count("label") == 5
    assert _hold_marker(mic_button().children[0]) == "press"


def test_touch_shim_is_installed_on_bootstrap_and_covers_both_modes():
    assert "touchstart" in TOUCH_HOLD_SHIM_JS and "touchend" in TOUCH_HOLD_SHIM_JS
    assert "touchcancel" in TOUCH_HOLD_SHIM_JS
    assert 'satHold === "press"' in TOUCH_HOLD_SHIM_JS
    events = State().bootstrap()
    assert len(events) == 2


def test_upload_js_reports_progress_and_retries_three_times():
    # The template goes through Python %-formatting, so a stray literal % in the JS
    # would raise here (and in the browser it would never load).
    js = UPLOAD_AND_SEND_VOICE_JS_TEMPLATE % {"gateway_url": '"http://example.test"'}
    assert "XMLHttpRequest" in js and "xhr.upload.onprogress" in js
    assert "sat-upload-fill" in js and "attempt < 3" in js


def test_status_words_exist_in_both_languages_and_cover_held_and_blocked():
    # The chat JS draws a message's status from these (both languages, picked by the UI
    # language). A held or blocked message used to fall through to "Sending... (tap to
    # cancel)", with a tap that the gateway refuses.
    assert "status_held" in STATUS_TEXT_KEYS and "status_blocked" in STATUS_TEXT_KEYS
    for lang in ("en", "te"):
        for key in STATUS_TEXT_KEYS:
            assert TEXTS[lang].get(key), f"{key} missing or empty in {lang}"


def test_chat_js_labels_held_and_blocked_in_the_ui_language():
    assert 'statusWord("status_held")' in CHAT_CONNECT_JS_TEMPLATE
    assert 'statusWord("status_blocked")' in CHAT_CONNECT_JS_TEMPLATE
    assert "STATUS_TEXT[ui]" in CHAT_CONNECT_JS_TEMPLATE


def test_changing_language_or_speech_saves_straight_away():
    # The gateway's value wins on load, so a pick that only ever reached localStorage
    # would be undone on the next visit. Each setter must sync the JS AND save.
    state = _fresh_state()

    language_events = state.set_preferred_language_input("hi")
    speech_events = state.set_tts_on_input(False)

    assert state.preferred_language_input == "hi" and state.tts_on_input is False
    assert len(language_events) == 2
    assert len(speech_events) == 2


def test_settings_writes_carry_the_timezone_and_format_cleanly():
    # Quiet hours are only enforced for a user with a timezone. Both templates go through
    # Python %-formatting, so a stray literal % would raise here.
    save = SAVE_SETTINGS_JS_TEMPLATE % {
        "gateway_url": '"http://example.test"',
        "start": '"21:30"',
        "end": '"06:00"',
        "preferred_language": '"hi"',
        "tts_on": "true",
    }
    prefs = SAVE_PREFS_JS_TEMPLATE % {
        "gateway_url": '"http://example.test"',
        "preferred_language": '"hi"',
        "tts_on": "true",
    }
    for js in (save, prefs):
        assert "resolvedOptions().timeZone" in js and "body.timezone = zone" in js
    # the quick save must not carry a half-typed quiet-hours time
    assert "quiet_hours" not in prefs
