"""The elder's satsang screens: what is on (`/satsang`) and listening (`/satsang/<id>`).

Two routes, not one screen with two states, because each has ONE job and the listen screen must have
nothing competing with the play button. What can be tested without a browser is tested here; what
can only be seen is listed at the end of this docstring.

- The decision of which screen the elder is looking at is a pure function (`phase_for`) over what
  the list says and what the player reports. It is tested as a full table, boundaries included: the
  elder is told nothing for the first seconds of a hiccup, "waiting" at 8 s, "the speaker's
  connection may have dropped" at 25 s, "may have ended" at 120 s (the media server drops a silent
  publisher after 30 s, so the list usually says "ended" well before that).
- Everything the screen says is in two languages (English, Telugu), with identical keys.
- The page makes ONE request, `GET /sessions`, with the elder's own id in the Authorization header
  (as the rest of the app does), never in a URL, and stores nothing.
- The stream address and the tap: the audio element is always in the page (never inside a branch
  that can unmount), carries the address in `data-url`, and the big button is a DOM handler, not a
  Reflex event (iOS needs the tap itself to start audio).

NOT checked here, only by hand, and said so in the pull request: a real browser playing a real
stream, an iPhone, and the Telugu wording, which no native reader has read.
"""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from elder_app.pages import satsang_common as common
from elder_app.pages import satsang_listen, satsang_upcoming
from elder_app.pages.satsang_common import (
    CONNECT_LONG_S,
    CONTRAST_PAIRS,
    DROP_WAIT_S,
    LATE_AFTER_S,
    LONG_WAIT_S,
    SOFT_WAIT_S,
    TEXTS,
    js_load_sessions,
    parse_load,
    phase_for,
    text_for,
)
from elder_app.pages.satsang_listen import SatsangListenState
from elder_app.pages.satsang_upcoming import SatsangUpcomingState

HERE = Path(__file__).parent
ELDER_APP = HERE.parent / "elder_app" / "elder_app.py"
ASSETS = HERE.parent / "assets"
RUNNER = HERE / "satsang_admin_js_runner.js"

SID = "01a133af-1fe1-7429-a95d-dcbb6c9fe7c3"
OTHER = "01a133b2-362c-7a72-a22e-eef6f77f3410"
ELDER_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
NOW = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)


def iso(delta_minutes: int) -> str:
    return (NOW + timedelta(minutes=delta_minutes)).isoformat().replace("+00:00", "Z")


def item(sid=SID, status="scheduled", start=60, title="Gita discourse", speaker="Swami Ananda"):
    return {
        "id": sid,
        "title": title,
        "speaker_name": speaker,
        "status": status,
        "scheduled_start_at": iso(start),
        "hls_url": f"/hls/{sid}.m3u8" if status == "live" else None,
        "when": "Wednesday 14 October, 6:30 pm",
    }


def loaded(items, lang="en", ok=True, status=200, detail=""):
    return json.dumps({"ok": ok, "status": status, "detail": detail, "lang": lang, "items": items})


def player(status="idle", waiting_s=0, error_kind=""):
    return json.dumps({"status": status, "waiting_s": waiting_s, "error_kind": error_kind})


def script_of(event) -> str:
    return json.loads(str(event.args[0][1]))


def callback_of(event) -> str:
    return str(event.args[1][1])


# ---------------------------------------------------------------------------------------------
# 1. which screen: the phase table
# ---------------------------------------------------------------------------------------------

READY = "ready"


@pytest.mark.parametrize(
    ("args", "phase"),
    [
        # the list has not arrived, or could not
        (dict(load_state="loading"), "loading"),
        (dict(load_state="error", load_error="network"), "load_error"),
        (dict(load_state="error", load_error="server"), "load_error"),
        (dict(load_state="error", load_error="unauthorised"), "load_error"),
        (dict(load_state="error", load_error="no_identity"), "need_identity"),
        # the list says
        (dict(load_state=READY, item_status=None), "gone"),
        (dict(load_state=READY, item_status=None, seen_live=True), "ended"),
        (dict(load_state=READY, item_status="cancelled"), "cancelled"),
        (dict(load_state=READY, item_status="scheduled"), "scheduled"),
        (dict(load_state=READY, item_status="live"), "live_ready"),
        # while it is live, the player decides
        (dict(item_status="live", player="connecting", waiting_s=0), "connecting"),
        (dict(item_status="live", player="connecting", waiting_s=CONNECT_LONG_S - 1), "connecting"),
        (
            dict(item_status="live", player="connecting", waiting_s=CONNECT_LONG_S),
            "connecting_long",
        ),
        (dict(item_status="live", player="playing"), "playing"),
        (dict(item_status="live", player="stalled", waiting_s=0), "playing"),
        (dict(item_status="live", player="stalled", waiting_s=SOFT_WAIT_S - 1), "playing"),
        (dict(item_status="live", player="stalled", waiting_s=SOFT_WAIT_S), "stalled_soft"),
        (dict(item_status="live", player="stalled", waiting_s=DROP_WAIT_S - 1), "stalled_soft"),
        (dict(item_status="live", player="stalled", waiting_s=DROP_WAIT_S), "stalled_drop"),
        (dict(item_status="live", player="stalled", waiting_s=LONG_WAIT_S - 1), "stalled_drop"),
        (dict(item_status="live", player="stalled", waiting_s=LONG_WAIT_S), "stalled_long"),
        (dict(item_status="live", player="failed", error_kind="unreachable"), "failed_unreachable"),
        (dict(item_status="live", player="failed", error_kind="unsupported"), "failed_unsupported"),
        (dict(item_status="live", player="failed", error_kind="blocked"), "failed_blocked"),
        (dict(item_status="live", player="failed", error_kind="media"), "failed_other"),
        (dict(item_status="live", player="failed", error_kind="other"), "failed_other"),
        (dict(item_status="live", player="failed", error_kind="no_stream"), "failed_other"),
        # the list outranks the player: it ended, so say so, whatever the player thinks
        (dict(item_status=None, player="stalled", waiting_s=10, seen_live=True), "ended"),
        (dict(item_status=None, player="playing"), "ended"),  # it was playing, so it was live
        (dict(item_status="cancelled", player="playing"), "cancelled"),
        # a failed refresh of the list must not cut a discourse the elder is listening to
        (dict(load_state="error", load_error="network", player="playing"), "playing"),
        (
            dict(load_state="error", load_error="network", player="stalled", waiting_s=30),
            "stalled_drop",
        ),
    ],
)
def test_the_screen_follows_the_list_and_the_player(args, phase):
    base = dict(
        load_state=READY,
        load_error="",
        item_status="live",
        seen_live=False,
        player="idle",
        waiting_s=0,
        error_kind="",
    )
    assert phase_for(**{**base, **args}) == phase


def test_the_thresholds_are_in_the_order_the_elder_needs():
    assert 0 < SOFT_WAIT_S < DROP_WAIT_S < LONG_WAIT_S
    # a drop the media server notices (its 30 s idle limit) is reported by the list before "may
    # have ended" is ever said by the player
    assert DROP_WAIT_S <= 30 < LONG_WAIT_S


# ---------------------------------------------------------------------------------------------
# 2. the words
# ---------------------------------------------------------------------------------------------

PHASES = [
    "loading",
    "need_identity",
    "load_error",
    "gone",
    "ended",
    "cancelled",
    "scheduled",
    "live_ready",
    "connecting",
    "connecting_long",
    "playing",
    "stalled_soft",
    "stalled_drop",
    "stalled_long",
    "failed_unreachable",
    "failed_unsupported",
    "failed_blocked",
    "failed_other",
]


def test_english_and_telugu_have_exactly_the_same_keys_and_none_is_empty():
    assert set(TEXTS["en"]) == set(TEXTS["te"])
    for language in ("en", "te"):
        for key, value in TEXTS[language].items():
            assert value.strip(), (language, key)


@pytest.mark.parametrize("phase", PHASES)
def test_every_screen_the_elder_can_be_on_has_words_in_both_languages(phase):
    for language in ("en", "te"):
        assert TEXTS[language][f"{phase}_title"], (language, phase)
        assert f"{phase}_body" in TEXTS[language]


def test_the_telugu_is_in_telugu_not_left_in_english():
    allowed_latin = {"Chrome", "Android", "Safari", "iPhone"}
    for key, value in TEXTS["te"].items():
        bare = re.sub(r"\{[a-z_]+\}", "", value)  # a placeholder is filled in, not written
        letters = [c for c in bare if c.isalpha()]
        telugu = [c for c in letters if "ఀ" <= c <= "౿"]
        words = set(re.findall(r"[A-Za-z]+", bare))
        assert telugu or not letters, key
        assert words <= allowed_latin, (key, words - allowed_latin)


def test_the_words_that_take_a_name_and_a_time_take_them():
    for language in ("en", "te"):
        body = TEXTS[language]["scheduled_body"]
        assert "{speaker}" in body and "{when}" in body
        assert body.format(speaker="S", when="W")
    for language in ("en", "te"):
        assert "{" not in TEXTS[language]["ended_body"]


def test_an_unknown_language_reads_english_like_the_rest_of_the_app():
    assert (
        text_for("hi") is TEXTS["en"]
        and text_for("te") is TEXTS["te"]
        and text_for("") is TEXTS["en"]
    )


def test_a_late_joiner_and_a_listener_are_told_what_they_can_and_cannot_hear():
    en = TEXTS["en"]
    assert "cannot be played again" in en["late_note"]
    assert "not recorded" in en["ended_body"]
    assert "behind" in en["delay_note"]


def test_the_certificate_failure_says_what_to_do_in_words_an_elder_can_act_on():
    en = TEXTS["en"]["failed_unreachable_body"]
    assert "certificate" in en and "ask" in en.lower()


# ---------------------------------------------------------------------------------------------
# 3. reading the list
# ---------------------------------------------------------------------------------------------


def parsed(items, **kw):
    return parse_load(loaded(items, **kw), now=NOW)


def test_a_good_list_becomes_items_with_a_when_label_and_a_stream_only_while_live():
    out = parsed([item(SID, "live", -5), item(OTHER, "scheduled", 90)], lang="te")
    assert (out["ok"], out["error"], out["lang"]) == (True, "", "te")
    live, soon = out["items"]
    assert live["id"] == SID and live["stream_url"] == f"http://localhost/hls/{SID}.m3u8"
    assert soon["id"] == OTHER and soon["stream_url"] == ""
    assert live["when"] == "Wednesday 14 October, 6:30 pm"


def test_the_live_one_is_listed_first_then_the_rest_soonest_first():
    out = parsed([item("c", "scheduled", 300), item("b", "scheduled", 60), item("a", "live", -30)])
    assert [i["id"] for i in out["items"]] == ["a", "b", "c"]


def test_a_cancelled_one_stays_listed_so_the_elder_is_told_not_left_wondering():
    out = parsed([item(SID, "cancelled", 60)])
    assert [i["status"] for i in out["items"]] == ["cancelled"]


def test_only_what_the_screen_shows_is_kept_never_an_id_a_key_or_bookkeeping():
    leaky = {
        **item(),
        "speaker_user_id": "u-1",
        "created_by": "u-2",
        "stream_key": "k",
        "reminder_sent_at": "x",
    }
    out = parsed([leaky])
    assert set(out["items"][0]) == {
        "id",
        "title",
        "speaker_name",
        "status",
        "when",
        "stream_url",
        "late",
    }
    assert "u-1" not in json.dumps(out) and "stream_key" not in json.dumps(out)


def test_a_live_one_that_began_long_ago_is_marked_late_and_a_fresh_one_is_not():
    out = parsed([item("old", "live", -(LATE_AFTER_S // 60) - 1), item("new", "live", -1)])
    assert {i["id"]: i["late"] for i in out["items"]} == {"old": True, "new": False}


def test_a_stream_address_is_only_taken_from_this_gateway_and_only_if_it_looks_right():
    bad = item(SID, "live", -5)
    bad["hls_url"] = "https://evil.example/x.m3u8"
    assert parsed([bad])["items"][0]["stream_url"] == ""
    odd = item(SID, "live", -5)
    odd["hls_url"] = "/media/other.m3u8"
    assert parsed([odd])["items"][0]["stream_url"] == ""


def test_a_satsang_that_is_not_live_never_gets_a_stream_even_if_the_server_sent_one():
    odd = item(SID, "scheduled", 60)
    odd["hls_url"] = f"/hls/{SID}.m3u8"
    assert parsed([odd])["items"][0]["stream_url"] == ""


@pytest.mark.parametrize(
    ("status", "detail", "error"),
    [
        (0, "network", "network"),
        (0, "no_identity", "no_identity"),
        (401, "", "unauthorised"),
        (403, "", "unauthorised"),
        (500, "", "server"),
        (404, "", "server"),
    ],
)
def test_a_failed_request_is_one_of_four_plain_kinds(status, detail, error):
    out = parse_load(loaded([], ok=False, status=status, detail=detail), now=NOW)
    assert (out["ok"], out["error"], out["items"]) == (False, error, [])


@pytest.mark.parametrize("raw", ["", "nope", "[]", "null", '{"ok": true, "items": "x"}'])
def test_garbage_from_the_browser_is_a_server_kind_of_failure_not_a_crash(raw):
    out = parse_load(raw, now=NOW)
    assert out["ok"] is False and out["error"] in ("server", "network")


def test_an_item_with_a_status_the_elder_should_never_see_is_dropped():
    out = parsed([item(SID, "ended", -90), item(OTHER, "scheduled", 60)])
    assert [i["id"] for i in out["items"]] == [OTHER]


# ---------------------------------------------------------------------------------------------
# 4. the request, run for real under Node
# ---------------------------------------------------------------------------------------------


def run_js(steps, local=None, fetches=None, tz="UTC"):
    import os

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the request tests did NOT run")
    payload = {"steps": steps, "local": local or {}, "fetches": fetches or []}
    proc = subprocess.run(
        [node, str(RUNNER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf8",  # Telugu month names come back; the default on Windows is cp1252
        env={**os.environ, "TZ": tz},
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout)
    out["results"] = [json.loads(r) if isinstance(r, str) else r for r in out["results"]]
    return out


GOOD_BODY = {"items": [{**item(SID, "live", -5), "speaker_user_id": "u-1", "created_by": "u-2"}]}
LOCAL = {"satsandesh_my_id": ELDER_ID, "sat_prefs": json.dumps({"lang": "te"})}


def test_the_page_asks_once_for_the_list_with_the_elders_own_id_and_nothing_else():
    out = run_js([js_load_sessions()], local=LOCAL, fetches=[{"status": 200, "body": GOOD_BODY}])
    (call,) = out["calls"]
    assert (call["method"], call["url"]) == ("GET", "http://localhost/sessions")
    assert call["headers"] == {"Authorization": f"Bearer {ELDER_ID}"}
    assert ELDER_ID not in call["url"]  # the id is a credential in legacy mode: never in a URL
    assert call["body"] is None


def test_the_answer_is_trimmed_to_what_the_screen_shows_and_carries_the_language():
    out = run_js([js_load_sessions()], local=LOCAL, fetches=[{"status": 200, "body": GOOD_BODY}])
    result = out["results"][0]
    assert (result["ok"], result["lang"]) == (True, "te")
    (shown,) = result["items"]
    assert set(shown) == {
        "id",
        "title",
        "speaker_name",
        "status",
        "scheduled_start_at",
        "hls_url",
        "when",
    }
    assert shown["when"]  # a label in the elder's own language and time zone
    assert "u-1" not in json.dumps(result) and "u-2" not in json.dumps(result)


def test_the_time_is_written_in_the_elders_own_language_and_time_zone():
    body = {"items": [{**item(SID, "scheduled", 0), "scheduled_start_at": "2026-10-14T13:00:00Z"}]}
    te = run_js(
        [js_load_sessions()],
        local={**LOCAL, "sat_prefs": json.dumps({"lang": "te"})},
        fetches=[{"status": 200, "body": body}],
        tz="Asia/Kolkata",
    )["results"][0]["items"][0]["when"]
    en = run_js(
        [js_load_sessions()],
        local={**LOCAL, "sat_prefs": json.dumps({"lang": "en"})},
        fetches=[{"status": 200, "body": body}],
        tz="Asia/Kolkata",
    )["results"][0]["items"][0]["when"]
    assert "6:30" in en or "06:30" in en or "6.30" in en  # 13:00 UTC is 18:30 in India
    assert te != en and re.search(r"[ఀ-౿]", te)  # Telugu month and day names


def test_with_no_identity_nothing_is_requested():
    out = run_js([js_load_sessions()], local={})
    assert out["calls"] == []
    assert out["results"][0]["detail"] == "no_identity"


def test_a_dead_network_and_a_refusal_come_back_as_data_not_exceptions():
    dead = run_js([js_load_sessions()], local=LOCAL, fetches=[{"throw": True}])["results"][0]
    assert (dead["ok"], dead["status"], dead["detail"]) == (False, 0, "network")
    refused = run_js(
        [js_load_sessions()], local=LOCAL, fetches=[{"status": 401, "body": {"detail": "x"}}]
    )["results"][0]
    assert (refused["ok"], refused["status"]) == (False, 401)


def test_the_page_script_writes_nothing_to_the_browser():
    script = js_load_sessions()
    for forbidden in ("setItem", "removeItem", "document.cookie", "POST", "PATCH", "DELETE"):
        assert forbidden not in script, forbidden


# ---------------------------------------------------------------------------------------------
# 5. the listen screen's state
# ---------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _the_clock_is_NOW(monkeypatch):
    """Whether a satsang counts as "joined late" depends on the clock; the tests fix it."""
    monkeypatch.setattr(common, "_now", lambda: NOW)


def listen(session_id=SID):
    """A state that has been told which satsang the address names. (No router runs in a unit test;
    on the page, `boot` reads the id from the address and calls the same method. The state cannot
    hold the route argument as a var of its own: Reflex refuses a state var that shadows one.)"""
    state = SatsangListenState()
    state.open_session(session_id)
    return state


def test_arriving_on_a_scheduled_satsang_says_when_and_who_not_a_dead_player():
    s = listen()
    s.after_load(loaded([item(SID, "scheduled", 90)], lang="en"))
    assert s.phase == "scheduled"
    assert s.title == TEXTS["en"]["scheduled_title"]
    assert "Swami Ananda" in s.body and "Wednesday 14 October" in s.body
    assert (s.show_play, s.show_stop, s.show_session) == (False, False, True)


def test_the_screen_speaks_the_elders_language():
    s = listen()
    s.after_load(loaded([item(SID, "scheduled", 90)], lang="te"))
    assert s.title == TEXTS["te"]["scheduled_title"] and "Swami Ananda" in s.body


def test_a_live_satsang_offers_the_big_button_and_the_honest_notes():
    s = listen()
    s.after_load(loaded([item(SID, "live", -1)]))
    assert s.phase == "live_ready"
    assert (s.show_play, s.show_stop, s.show_notes) == (True, False, True)
    assert s.stream_url == f"http://localhost/hls/{SID}.m3u8"
    assert s.late is False
    s.after_load(loaded([item(SID, "live", -30)]))
    assert s.late is True  # joining 30 minutes in: the late note is shown, not left to surprise


def test_the_same_screen_is_used_for_any_id_and_finds_its_own_satsang():
    s = listen(OTHER)
    s.after_load(loaded([item(SID, "live", -1), item(OTHER, "scheduled", 120, title="Other")]))
    assert s.phase == "scheduled" and s.item["title"] == "Other"


def test_an_id_that_is_not_on_the_list_is_gone_not_a_crash():
    s = listen("nonexistent")
    s.after_load(loaded([item(SID, "live", -1)]))
    assert s.phase == "gone" and s.title == TEXTS["en"]["gone_title"]


def test_after_it_was_live_and_leaves_the_list_it_has_ended():
    s = listen()
    s.after_load(loaded([item(SID, "live", -1)]))
    s.after_load(loaded([]))
    assert s.phase == "ended" and "not recorded" in s.body


def test_a_cancelled_satsang_says_so():
    s = listen()
    s.after_load(loaded([item(SID, "cancelled", 60)]))
    assert s.phase == "cancelled" and (s.show_play, s.show_stop) == (False, False)


def test_a_failed_load_offers_a_retry_and_does_not_pretend():
    s = listen()
    s.after_load(loaded([], ok=False, status=0, detail="network"))
    assert s.phase == "load_error" and s.show_retry is True
    s2 = listen()
    s2.after_load(loaded([], ok=False, status=0, detail="no_identity"))
    assert s2.phase == "need_identity"


def test_a_session_id_in_the_address_is_checked_before_it_is_used():
    for bad in ("", "../../etc", "x" * 200, "a b", "<script>"):
        assert listen(bad).valid_id is False
    assert listen(SID).valid_id is True


def test_the_player_status_drives_the_words_and_the_stop_button():
    s = listen()
    s.after_load(loaded([item(SID, "live", -1)]))
    s.after_status(player("connecting", 0))
    assert (s.phase, s.show_play, s.show_stop) == ("connecting", False, True)
    s.after_status(player("playing"))
    assert (s.phase, s.show_stop) == ("playing", True)
    s.after_status(player("stalled", SOFT_WAIT_S))
    assert s.phase == "stalled_soft"
    s.after_status(player("stalled", DROP_WAIT_S))
    assert s.phase == "stalled_drop"
    s.after_status(player("stalled", LONG_WAIT_S))
    assert (s.phase, s.show_check) == ("stalled_long", True)


def test_a_status_the_player_does_not_have_is_read_as_idle_not_trusted():
    s = listen()
    s.after_load(loaded([item(SID, "live", -1)]))
    s.after_status(player("playing"))
    s.after_status(player("banana"))
    assert s.player == "idle" and s.phase == "live_ready"
    s.after_status("not json at all")  # and garbage changes nothing
    assert s.player == "idle"


def test_a_failure_offers_the_button_again_except_when_the_browser_cannot_play_it_at_all():
    s = listen()
    s.after_load(loaded([item(SID, "live", -1)]))
    s.after_status(player("failed", error_kind="unreachable"))
    assert (s.phase, s.show_play) == ("failed_unreachable", True)
    s.after_status(player("failed", error_kind="unsupported"))
    assert (s.phase, s.show_play) == ("failed_unsupported", False)
    s.after_status(player("failed", error_kind="blocked"))
    assert (s.phase, s.show_play) == ("failed_blocked", True)


def test_when_the_list_says_ended_while_it_is_stalled_the_player_is_stopped_and_it_is_ended():
    s = listen()
    s.after_load(loaded([item(SID, "live", -1)]))
    s.after_status(player("stalled", 40))
    event = s.after_load(loaded([]))
    assert s.phase == "ended"
    assert event is not None and "satsang.stop" in script_of(event)


def test_a_failed_refresh_of_the_list_does_not_cut_off_a_listener():
    s = listen()
    s.after_load(loaded([item(SID, "live", -1)]))
    s.after_status(player("playing"))
    s.after_load(loaded([], ok=False, status=0, detail="network"))
    assert s.phase == "playing"


def test_every_tick_reads_the_player_and_every_fourth_also_refreshes_the_list():
    s = listen()
    scripts = []
    for _ in range(common.LIST_EVERY_TICKS * 2):
        events = s.tick("")
        scripts.append([script_of(e) for e in events])
    assert all("satsang.status" in sc[0] for sc in scripts)
    refreshed = [i + 1 for i, sc in enumerate(scripts) if len(sc) == 2]
    assert refreshed == [common.LIST_EVERY_TICKS, common.LIST_EVERY_TICKS * 2]
    assert "/sessions" in scripts[common.LIST_EVERY_TICKS - 1][1]


def test_opening_the_page_takes_the_id_from_the_address_and_asks_for_the_list(monkeypatch):
    monkeypatch.setattr(SatsangListenState, "_address_id", lambda self: OTHER)
    s = SatsangListenState()
    event = s.boot()
    assert s.valid_id is True and s._sid == OTHER
    assert "/sessions" in script_of(event)
    assert "after_load" in callback_of(event)


def test_opening_the_page_with_a_nonsense_id_asks_for_nothing_and_says_it_is_gone(monkeypatch):
    monkeypatch.setattr(SatsangListenState, "_address_id", lambda self: "../../x")
    s = SatsangListenState()
    assert s.boot() is None
    assert (s.valid_id, s.phase) == (False, "gone")


def test_the_tick_does_nothing_for_an_invalid_id():
    s = listen("not valid!")
    assert s.tick("") is None


def test_stop_and_check_again_do_what_they_say():
    s = listen()
    assert "satsang.stop" in script_of(s.stop())
    assert "/sessions" in script_of(s.check_again())


# ---------------------------------------------------------------------------------------------
# 6. the upcoming screen's state
# ---------------------------------------------------------------------------------------------


def test_the_upcoming_screen_lists_what_is_on_and_what_is_next():
    s = SatsangUpcomingState()
    s.after_load(loaded([item("b", "scheduled", 120), item("a", "live", -5)], lang="te"))
    assert (s.status, s.lang) == ("ready", "te")
    assert [i["id"] for i in s.items] == ["a", "b"]
    assert s.items[0]["href"] == "/satsang/a" and s.items[0]["live"] is True
    assert s.items[1]["live"] is False and s.items[1]["cancelled"] is False


def test_an_empty_list_is_an_empty_screen_with_words_not_a_blank():
    s = SatsangUpcomingState()
    s.after_load(loaded([]))
    assert (s.status, s.items) == ("ready", [])
    assert s.empty_text == TEXTS["en"]["empty"]


def test_the_upcoming_screen_tells_failures_apart():
    s = SatsangUpcomingState()
    s.after_load(loaded([], ok=False, status=0, detail="network"))
    assert (s.status, s.error_text) == ("error", TEXTS["en"]["load_error_title"])
    s.after_load(loaded([], ok=False, status=0, detail="no_identity"))
    assert s.error_text == TEXTS["en"]["need_identity_title"]


def test_the_upcoming_screen_refreshes_itself_about_every_half_minute():
    s = SatsangUpcomingState()
    results = [s.tick("") for _ in range(common.UPCOMING_EVERY_TICKS * 2)]
    due = [i + 1 for i, r in enumerate(results) if r is not None]
    assert due == [common.UPCOMING_EVERY_TICKS, common.UPCOMING_EVERY_TICKS * 2]
    assert common.UPCOMING_EVERY_TICKS * common.TICK_MS / 1000 <= 45


# ---------------------------------------------------------------------------------------------
# 7. the components
# ---------------------------------------------------------------------------------------------


def walk(component):
    yield component
    for child in getattr(component, "children", []):
        yield from walk(child)


def px(component, key):
    value = dict(component.style)[key]
    return int(re.sub(r"\D", "", str(getattr(value, "_var_value", value))))


def luminance(hex_colour):
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg, bg):
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_colour_pair_meets_its_contrast_target_and_the_check_can_fail():
    assert CONTRAST_PAIRS
    for label, fg, bg, minimum in CONTRAST_PAIRS:
        assert contrast(fg, bg) >= minimum, f"{label}: {contrast(fg, bg):.2f} < {minimum}"
    assert max(m for *_, m in CONTRAST_PAIRS) >= 7
    assert contrast("#C0C0C0", "#FFFFFF") < 3


def walk_with_ancestors(component, ancestors=()):
    yield component, ancestors
    for child in getattr(component, "children", []):
        yield from walk_with_ancestors(child, (*ancestors, type(component).__name__))


def test_the_audio_element_is_in_the_page_always_with_the_stream_address_in_data_url():
    found = [
        (c, ancestors)
        for c, ancestors in walk_with_ancestors(satsang_listen.page())
        if type(c).__name__ == "Audio"
    ]
    assert len(found) == 1
    audio, ancestors = found[0]
    text = str(audio)
    assert "satsang-audio" in text and "data-url" in text
    # never inside a conditional that could unmount it while the sound is playing
    assert not {"Cond", "Match"} & set(ancestors), ancestors


def test_the_big_button_is_a_dom_handler_not_a_reflex_event_and_is_huge():
    text = str(satsang_listen.page())
    assert "window.satsang.play()" in text
    play = satsang_listen.play_button()
    assert "on_click" not in play.event_triggers  # no server round trip: iOS needs the tap itself
    assert px(play, "minHeight") >= 160 and px(play, "minWidth") >= 160


def test_the_stop_button_is_a_dom_handler_too():
    stop = satsang_listen.stop_button()
    assert "on_click" not in stop.event_triggers
    assert "window.satsang.stop()" in str(stop)


def test_every_button_on_the_elder_screens_is_a_large_target_in_large_type():
    for build in (satsang_listen.page, satsang_upcoming.page):
        buttons = [c for c in walk(build()) if type(c).__name__ == "Button"]
        for b in buttons:
            assert px(b, "minHeight") >= 88 and px(b, "fontSize") >= 22, dict(b.style)


def test_the_listen_page_loads_the_vendored_player_and_hls_from_its_own_origin():
    text = str(satsang_listen.page())
    assert "/vendor/hls.min.js" in text and "/satsang_player.js" in text
    assert "cdn" not in text.lower() and "unpkg" not in text.lower()


def test_the_vendored_files_are_there_unmodified_with_their_licence():
    hls = ASSETS / "vendor" / "hls.min.js"
    assert hls.is_file() and 300_000 < hls.stat().st_size < 600_000
    import hashlib

    digest = hashlib.sha256(hls.read_bytes()).hexdigest()
    notice = (HERE.parents[2] / "NOTICE").read_text(encoding="utf8")
    assert digest in notice  # the recorded checksum is this file's
    licence = (ASSETS / "vendor" / "hls.LICENSE.txt").read_text(encoding="utf8")
    assert "Apache License" in licence and "Dailymotion" in licence
    assert (ASSETS / "satsang_player.js").is_file()


def test_the_vendored_player_is_not_served_from_a_path_caddy_sends_to_the_media_server():
    caddy = (HERE.parents[2] / "infra" / "caddy" / "Caddyfile").read_text(encoding="utf8")
    assert "handle /hls*" in caddy
    for page in (satsang_listen.page, satsang_upcoming.page):
        for src in re.findall(r'src[=:]"?(/[^"\s,)}]+)', str(page())):
            assert not src.startswith("/hls"), src  # '/hls*' would swallow it


def test_the_entry_card_on_the_home_screen_is_bilingual_and_goes_to_the_list():
    card = satsang_upcoming.entry_card()
    text = str(card)
    # the rendered page source writes Telugu as \uXXXX escapes, so compare with that spelling
    assert 'to:"/satsang"' in text  # exactly the list, not a longer path that merely starts so
    assert json.dumps("సత్సంగం")[1:-1] in text  # the Telugu word
    assert "Satsang" in text
    assert px(card, "minHeight") >= 88


def test_the_screens_have_a_way_back():
    assert "/satsang" in str(satsang_listen.page())
    assert "/" in str(satsang_upcoming.page())


# ---------------------------------------------------------------------------------------------
# 8. the edits to elder_app.py
# ---------------------------------------------------------------------------------------------


def test_the_routes_are_registered_beside_the_elder_screen_and_the_admin_page():
    from elder_app.elder_app import app

    routes = set(app._unevaluated_pages)
    assert {"index", "satsang-admin", "satsang", "satsang/[session_id]"} <= routes


def test_elder_app_py_gained_an_import_two_add_page_lines_and_one_entry_line():
    source = ELDER_APP.read_text(encoding="utf8")
    imports = [ln for ln in source.splitlines() if ln.startswith("from elder_app.pages import")]
    assert len(imports) == 1
    assert {"satsang_admin", "satsang_listen", "satsang_upcoming"} <= set(
        re.findall(r"satsang_\w+", imports[0])
    )
    adds = [ln for ln in source.splitlines() if "app.add_page(" in ln and "satsang_" in ln]
    assert len(adds) == 3, adds  # the admin page, the list, the listen screen
    entries = [ln for ln in source.splitlines() if "satsang_upcoming.entry_card()" in ln]
    assert len(entries) == 1
    # and the Satsang TAB, which shows the circles list, is not touched
    assert 'State.t["tab_satsang"]' in source
    assert "rx.redirect" not in source.split("def bottom_tabs")[1].split("def home_screen")[0]
