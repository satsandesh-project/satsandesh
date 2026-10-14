"""Tests for elder_app.pages.satsang_admin -- the admin page for scheduling a satsang (Week 9).

Three kinds of test, because the page has three kinds of code:

1. The BROWSER scripts (the strings handed to rx.call_script) run for real under Node against a
   fake DOM, sessionStorage and fetch (tests/satsang_admin_js_runner.js). They are where the secrets
   are handled, so they are where the security properties are checked: the administrator's token and
   a stream key are written into the browser and nowhere else. If Node is missing those tests are
   SKIPPED, which pytest prints; they are not silently passing.
2. The Python State: what a result does to the screen, and what it must never hold.
3. The component tree: legibility numbers (type size, target size, contrast) and the one-line edit
   to elder_app.py.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from elder_app.pages import satsang_admin as page_mod
from elder_app.pages.satsang_admin import (
    CONTRAST_PAIRS,
    ROUTE,
    SatsangAdminState,
    describe_failure,
    page,
    shape_sessions,
)

HERE = Path(__file__).parent
RUNNER = HERE / "satsang_admin_js_runner.js"
ELDER_APP_PY = HERE.parent / "elder_app" / "elder_app.py"

SID = "01a133af-1fe1-7429-a95d-dcbb6c9fe7c3"
OTHER_SID = "01a133b2-362c-7a72-a22e-eef6f77f3410"
SPEAKER_ID = "11111111-2222-4333-8444-555555555555"
ADMIN_ID = "99999999-8888-4777-8666-555555555555"
TOKEN = "tok-test-0000-not-a-real-token"
KEY = "KEY-SECRET-xyzzy-not-a-real-key"
INGEST = f"rtmp://10.110.11.31:1935/live/{SID}?key={KEY}"
AUTHED = {"satsang_admin_token": TOKEN}


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def run_js(steps, dom=None, storage=None, fetches=None, tz="UTC", clipboard_fails=False):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the browser-script tests did NOT run")
    payload = {
        "steps": steps,
        "dom": dom or {},
        "storage": storage or {},
        "fetches": fetches or [],
        "clipboard_fails": clipboard_fails,
    }
    env = {**os.environ, "TZ": tz}
    proc = subprocess.run(
        [node, str(RUNNER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout)
    out["results"] = [json.loads(r) if isinstance(r, str) else r for r in out["results"]]
    out["raw"] = proc.stdout
    return out


def script_of(event) -> str:
    """The JavaScript source an rx.call_script event carries."""
    return json.loads(str(event.args[0][1]))


def callback_of(event) -> str:
    return str(event.args[1][1])


def secret_dom(**extra):
    return {
        "satsang-secret-url": {"dataset": {}, "textContent": ""},
        "satsang-secret-toggle": {"textContent": "Show"},
        **extra,
    }


def created_body():
    return {
        "session": {
            "id": SID,
            "title": "Thursday discourse",
            "speaker_user_id": SPEAKER_ID,
            "speaker_name": "Swami Ananda",
            "scheduled_start_at": "2026-10-14T13:00:00Z",
            "status": "scheduled",
            "created_by": ADMIN_ID,
        },
        "stream_key": KEY,
        "ingest_url": INGEST,
    }


def row(sid=SID, status="scheduled", title="Thursday", when="2026-10-14T13:00:00Z", **extra):
    return {
        "id": sid,
        "title": title,
        "speaker_name": "Swami Ananda",
        "status": status,
        "scheduled_start_at": when,
        "when": "Wed 14 Oct, 6:30 pm",
        **extra,
    }


def ok(data=None, status=200):
    return json.dumps({"ok": True, "status": status, "detail": "", "data": data})


def failed(status, detail=""):
    return json.dumps({"ok": False, "status": status, "detail": detail, "data": None})


# ---------------------------------------------------------------------------------------------
# 1. the browser scripts, run for real
# ---------------------------------------------------------------------------------------------


def test_signing_in_keeps_the_token_in_the_tab_and_empties_the_field():
    out = run_js(
        [page_mod.js_sign_in()],
        dom={"satsang-token": {"value": f"  {TOKEN} "}},
    )
    assert out["results"][0]["ok"] is True
    assert out["store"] == {"satsang_admin_token": TOKEN}
    assert out["dom"]["satsang-token"]["value"] == ""
    assert out["calls"] == []
    assert TOKEN not in json.dumps(out["results"])


def test_an_empty_token_is_refused_and_nothing_is_stored():
    out = run_js([page_mod.js_sign_in()], dom={"satsang-token": {"value": "   "}})
    assert out["results"][0] == {"ok": False, "status": 0, "detail": "empty", "data": None}
    assert out["store"] == {}


def test_signing_out_forgets_the_token_and_wipes_a_key_on_screen():
    dom = secret_dom()
    dom["satsang-secret-url"]["dataset"] = {"value": INGEST, "shown": "1"}
    dom["satsang-secret-url"]["textContent"] = INGEST
    out = run_js([page_mod.js_sign_out()], dom=dom, storage=dict(AUTHED))
    assert out["store"] == {}
    assert KEY not in json.dumps(out["dom"])


def test_boot_reports_whether_a_token_is_held_and_wipes_any_old_key():
    dom = secret_dom()
    dom["satsang-secret-url"]["dataset"] = {"value": INGEST}
    with_token = run_js([page_mod.js_boot()], dom=dom, storage=dict(AUTHED))
    without = run_js([page_mod.js_boot()], dom=secret_dom())
    assert with_token["results"][0]["data"] == {"signed_in": True}
    assert without["results"][0]["data"] == {"signed_in": False}
    assert KEY not in json.dumps(with_token["dom"])


def test_loading_the_list_sends_the_bearer_token_and_returns_no_user_ids():
    body = {
        "items": [
            {
                "id": SID,
                "title": "Thursday",
                "speaker_user_id": SPEAKER_ID,
                "speaker_name": "Swami Ananda",
                "scheduled_start_at": "2026-10-14T13:00:00Z",
                "status": "scheduled",
                "created_by": ADMIN_ID,
                "reminder_sent_at": None,
            }
        ]
    }
    out = run_js(
        [page_mod.js_load()],
        storage=dict(AUTHED),
        fetches=[{"status": 200, "body": body}],
        tz="Asia/Kolkata",
    )
    call = out["calls"][0]
    assert (call["method"], call["url"]) == ("GET", "http://localhost/sessions/manage")
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    (item,) = out["results"][0]["data"]
    assert set(item) == {"id", "title", "speaker_name", "status", "scheduled_start_at", "when"}
    assert item["when"]  # a label in the browser's own time zone
    assert SPEAKER_ID not in out["raw"] and ADMIN_ID not in out["raw"]
    assert TOKEN not in json.dumps(out["results"])


def test_no_call_is_made_without_a_token():
    for js in (page_mod.js_load(), page_mod.js_get_key(SID), page_mod.js_act(SID, "end")):
        out = run_js([js])
        assert out["calls"] == []
        assert out["results"][0]["status"] == 0 and out["results"][0]["detail"] == "no_token"


@pytest.mark.parametrize("status", [401, 403, 500])
def test_a_refusal_is_passed_on_with_its_status(status):
    out = run_js(
        [page_mod.js_load()],
        storage=dict(AUTHED),
        fetches=[{"status": status, "body": {"detail": "Forbidden"}}],
    )
    assert out["results"][0]["ok"] is False and out["results"][0]["status"] == status


def test_a_dead_network_is_reported_as_status_zero_not_thrown():
    out = run_js([page_mod.js_load()], storage=dict(AUTHED), fetches=[{"throw": True}])
    assert out["results"][0] == {"ok": False, "status": 0, "detail": "network", "data": None}


def form(title="  Thursday discourse ", speaker=SPEAKER_ID, when="2026-10-14T18:30"):
    return {
        "satsang-title": {"value": title},
        "satsang-speaker": {"value": speaker},
        "satsang-when": {"value": when},
        **secret_dom(),
    }


def test_scheduling_sends_the_time_as_an_unambiguous_utc_instant():
    out = run_js(
        [page_mod.js_schedule()],
        dom=form(),
        storage=dict(AUTHED),
        fetches=[{"status": 201, "body": created_body()}],
        tz="Asia/Kolkata",
    )
    call = out["calls"][0]
    assert (call["method"], call["url"]) == ("POST", "http://localhost/sessions")
    assert call["headers"]["Content-Type"] == "application/json"
    # 18:30 on a device in India is 13:00 UTC; the server must not have to guess a zone
    assert call["body"] == {
        "title": "Thursday discourse",
        "speaker_user_id": SPEAKER_ID,
        "scheduled_start_at": "2026-10-14T13:00:00.000Z",
    }


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("satsang-title", "   ", "title"),
        ("satsang-title", "x" * 121, "title"),
        ("satsang-speaker", "", "speaker"),
        ("satsang-speaker", "not-a-user-id", "speaker"),
        ("satsang-when", "", "when"),
        ("satsang-when", "next thursday", "when"),
        ("satsang-when", "2026-10-14", "when"),  # a date with no time: the browser parses it as UTC
        ("satsang-when", "Oct 14 2026 6pm", "when"),  # lenient to parse, not what the field gives
    ],
)
def test_a_bad_form_is_refused_in_the_browser_before_anything_is_sent(field, value, problem):
    dom = form()
    dom[field]["value"] = value
    out = run_js([page_mod.js_schedule()], dom=dom, storage=dict(AUTHED))
    assert out["calls"] == []
    assert out["results"][0]["detail"] == f"invalid:{problem}"
    assert out["dom"][field]["value"] == value  # nothing was cleared


def test_a_successful_schedule_puts_the_key_on_the_page_masked_and_nowhere_else():
    out = run_js(
        [page_mod.js_schedule()],
        dom=form(),
        storage=dict(AUTHED),
        fetches=[{"status": 201, "body": created_body()}],
    )
    result = out["results"][0]
    assert result["ok"] is True and result["status"] == 201
    assert result["data"] == {"id": SID, "title": "Thursday discourse"}
    assert KEY not in json.dumps(result) and INGEST not in json.dumps(result)
    url_el = out["dom"]["satsang-secret-url"]
    assert url_el["dataset"]["value"] == INGEST
    assert KEY not in url_el["textContent"] and url_el["textContent"] != ""
    assert url_el["dataset"]["shown"] == "0"
    for field in ("satsang-title", "satsang-speaker", "satsang-when"):
        assert out["dom"][field]["value"] == ""
    assert TOKEN not in json.dumps(out["dom"]) and TOKEN not in json.dumps(result)


def test_a_failed_schedule_keeps_the_form_so_nothing_is_retyped():
    out = run_js(
        [page_mod.js_schedule()],
        dom=form(),
        storage=dict(AUTHED),
        fetches=[{"status": 404, "body": {"detail": "Speaker not found"}}],
    )
    assert out["results"][0]["ok"] is False and out["results"][0]["status"] == 404
    assert out["results"][0]["detail"] == "Speaker not found"
    assert out["dom"]["satsang-title"]["value"] == "  Thursday discourse "
    assert out["dom"]["satsang-secret-url"]["dataset"].get("value") is None


def test_a_pydantic_422_detail_list_is_not_dumped_on_the_screen():
    out = run_js(
        [page_mod.js_schedule()],
        dom=form(),
        storage=dict(AUTHED),
        fetches=[{"status": 422, "body": {"detail": [{"loc": ["body", "title"], "msg": "x"}]}}],
    )
    assert out["results"][0]["status"] == 422 and out["results"][0]["detail"] == ""


def test_asking_for_a_new_key_posts_to_that_session_and_shows_it_masked():
    body = created_body()
    out = run_js(
        [page_mod.js_get_key(SID)],
        dom=secret_dom(),
        storage=dict(AUTHED),
        fetches=[{"status": 200, "body": body}],
    )
    assert (out["calls"][0]["method"], out["calls"][0]["url"]) == (
        "POST",
        f"http://localhost/sessions/{SID}/key",
    )
    assert out["results"][0]["data"] == {"id": SID, "title": "Thursday discourse"}
    assert out["dom"]["satsang-secret-url"]["dataset"]["value"] == INGEST
    assert KEY not in json.dumps(out["results"])


def test_the_speaker_can_name_the_session_in_a_field_instead():
    dom = secret_dom(**{"satsang-key-session": {"value": f"  {SID} "}})
    out = run_js(
        [page_mod.js_get_key(None)],
        dom=dom,
        storage=dict(AUTHED),
        fetches=[{"status": 200, "body": created_body()}],
    )
    assert out["calls"][0]["url"] == f"http://localhost/sessions/{SID}/key"
    assert out["dom"]["satsang-key-session"]["value"] == ""


@pytest.mark.parametrize("bad", ["", "abc", "../../etc", f"{SID}/end", "1; drop table"])
def test_a_session_id_that_is_not_a_uuid_is_never_put_into_a_url(bad):
    out = run_js(
        [page_mod.js_get_key(None)],
        dom=secret_dom(**{"satsang-key-session": {"value": bad}}),
        storage=dict(AUTHED),
    )
    assert out["calls"] == [] and out["results"][0]["detail"] == "invalid:session"
    out = run_js([page_mod.js_act(bad, "end")], storage=dict(AUTHED))
    assert out["calls"] == [] and out["results"][0]["detail"] == "invalid:session"


@pytest.mark.parametrize("action", ["end", "cancel"])
def test_ending_and_cancelling_post_to_the_right_path(action):
    out = run_js(
        [page_mod.js_act(SID, action)],
        storage=dict(AUTHED),
        fetches=[{"status": 200, "body": {"id": SID}}],
    )
    assert (out["calls"][0]["method"], out["calls"][0]["url"]) == (
        "POST",
        f"http://localhost/sessions/{SID}/{action}",
    )
    assert out["results"][0]["ok"] is True


def test_only_end_and_cancel_exist_as_actions():
    for bad in ("delete", "key", "ingest/publish", "../x", ""):
        with pytest.raises(ValueError):
            page_mod.js_act(SID, bad)


def test_show_then_hide_toggles_the_mask_and_the_button_label():
    dom = secret_dom()
    dom["satsang-secret-url"]["dataset"] = {"value": INGEST, "shown": "0"}
    dom["satsang-secret-url"]["textContent"] = "•" * 12
    out = run_js([page_mod.js_toggle_secret(), page_mod.js_toggle_secret()], dom=dom)
    first = run_js([page_mod.js_toggle_secret()], dom=dom)
    assert first["dom"]["satsang-secret-url"]["textContent"] == INGEST
    assert first["dom"]["satsang-secret-toggle"]["textContent"] == "Hide"
    assert KEY not in out["dom"]["satsang-secret-url"]["textContent"]
    assert out["dom"]["satsang-secret-toggle"]["textContent"] == "Show"


def test_copy_puts_the_real_address_on_the_clipboard_and_reports_failure_honestly():
    dom = secret_dom()
    dom["satsang-secret-url"]["dataset"] = {"value": INGEST, "shown": "0"}
    good = run_js([page_mod.js_copy_secret()], dom=dom)
    assert good["clipboard"] == INGEST and good["results"][0]["ok"] is True
    assert INGEST not in json.dumps(good["results"])
    bad = run_js([page_mod.js_copy_secret()], dom=dom, clipboard_fails=True)
    assert bad["results"][0]["ok"] is False and bad["clipboard"] is None
    nothing = run_js([page_mod.js_copy_secret()], dom=secret_dom())
    assert nothing["results"][0]["ok"] is False


def test_hiding_wipes_every_trace_of_the_key_from_the_page():
    out = run_js(
        [page_mod.js_schedule(), page_mod.js_toggle_secret(), page_mod.js_hide_secret()],
        dom=form(),
        storage=dict(AUTHED),
        fetches=[{"status": 201, "body": created_body()}],
    )
    assert KEY not in json.dumps(out["dom"])


def test_a_key_left_on_screen_wipes_itself_after_two_minutes():
    out = run_js(
        [page_mod.js_schedule(), {"fire_timers": True}],
        dom=form(),
        storage=dict(AUTHED),
        fetches=[{"status": 201, "body": created_body()}],
    )
    assert out["timers"] == []  # the one timer was fired
    el = out["dom"]["satsang-secret-url"]
    assert KEY not in json.dumps(out["dom"]) and "value" not in el["dataset"]
    assert "hidden" in el["textContent"].lower()
    armed = run_js(
        [page_mod.js_schedule()],
        dom=form(),
        storage=dict(AUTHED),
        fetches=[{"status": 201, "body": created_body()}],
    )
    assert armed["timers"] == [120_000]


def test_neither_the_token_nor_a_key_is_ever_written_to_local_storage():
    for name in dir(page_mod):
        value = getattr(page_mod, name)
        if isinstance(value, str) and name.startswith("_JS"):
            assert "localStorage" not in value, name
    assert "localStorage" not in Path(page_mod.__file__).read_text(encoding="utf8")


# ---------------------------------------------------------------------------------------------
# 2. Python: shaping, messages, State
# ---------------------------------------------------------------------------------------------


def test_the_list_splits_into_upcoming_and_past_and_is_ordered_for_a_person():
    raw = json.dumps(
        [
            row("a", "ended", "Old", "2026-10-01T13:00:00Z"),
            row("b", "scheduled", "Later", "2026-10-20T13:00:00Z"),
            row("c", "cancelled", "Newer cancelled", "2026-10-05T13:00:00Z"),
            row("d", "scheduled", "Sooner", "2026-10-15T13:00:00Z"),
            row("e", "live", "On air", "2026-10-14T13:00:00Z"),
        ]
    )
    upcoming, past = shape_sessions(raw)
    assert [r["title"] for r in upcoming] == ["On air", "Sooner", "Later"]  # live first
    assert [r["title"] for r in past] == ["Newer cancelled", "Old"]  # newest first


def test_each_row_carries_a_word_for_its_status_and_only_the_controls_that_can_work():
    upcoming, past = shape_sessions(
        json.dumps(
            [row("a", "live"), row("b", "scheduled"), row("c", "ended"), row("d", "cancelled")]
        )
    )
    by_id = {r["id"]: r for r in upcoming + past}
    assert by_id["a"]["status_label"] == "LIVE NOW"
    assert (by_id["a"]["can_end"], by_id["a"]["can_cancel"], by_id["a"]["can_key"]) == (
        True,
        False,
        False,
    )
    assert (by_id["b"]["can_end"], by_id["b"]["can_cancel"], by_id["b"]["can_key"]) == (
        False,
        True,
        True,
    )
    for sid, label in (("c", "Ended"), ("d", "Cancelled")):
        r = by_id[sid]
        assert r["status_label"] == label
        assert not (r["can_end"] or r["can_cancel"] or r["can_key"])


def test_user_ids_are_dropped_even_if_the_script_ever_leaked_them():
    leaky = row(speaker_user_id=SPEAKER_ID, created_by=ADMIN_ID, stream_key=KEY)
    upcoming, _ = shape_sessions(json.dumps([leaky]))
    assert SPEAKER_ID not in json.dumps(upcoming) and ADMIN_ID not in json.dumps(upcoming)
    assert KEY not in json.dumps(upcoming)


@pytest.mark.parametrize(
    "raw", ["", "not json", "{}", "null", '[{"id": 1}]', "[1, 2]", '[{"id": "a"}]']
)
def test_garbage_from_the_browser_gives_an_empty_list_not_a_crash(raw):
    assert shape_sessions(raw) == ([], [])


@pytest.mark.parametrize(
    ("op", "status", "detail", "expect"),
    [
        ("load", 0, "no_token", "sign in"),
        ("load", 0, "network", "reach the server"),
        ("load", 401, "", "token was not accepted"),
        ("load", 403, "", "not for an administrator"),
        ("schedule", 404, "Speaker not found", "speaker"),
        ("schedule", 422, "scheduled_start_at is in the past", "in the past"),
        ("schedule", 422, "", "check the title"),
        ("schedule", 0, "invalid:title", "title"),
        ("schedule", 0, "invalid:speaker", "user id"),
        ("schedule", 0, "invalid:when", "date and time"),
        ("key", 404, "Not found", "no such satsang"),
        ("key", 409, "a live satsang cannot be re-keyed", "already"),
        ("key", 0, "invalid:session", "session id"),
        ("end", 409, "a scheduled satsang cannot be ended", "cannot be ended"),
        ("cancel", 409, "a live satsang cannot be cancelled", "cannot be cancelled"),
        ("end", 500, "", "went wrong"),
    ],
)
def test_every_failure_has_a_plain_sentence(op, status, detail, expect):
    result = {"ok": False, "status": status, "detail": detail, "data": None}
    assert expect.lower() in describe_failure(op, result).lower()


def test_a_failure_message_never_echoes_a_token_or_an_unexpected_server_string():
    nasty = {"ok": False, "status": 500, "detail": f"boom {TOKEN} <script>", "data": None}
    message = describe_failure("load", nasty)
    assert TOKEN not in message and "<script>" not in message


def test_state_holds_no_field_that_could_carry_a_token_or_a_key():
    names = set(SatsangAdminState.get_fields())
    for forbidden in ("token", "key", "ingest", "url", "secret_value", "password"):
        assert not [n for n in names if forbidden in n.lower()], forbidden


def test_every_script_hands_its_result_to_the_matching_callback():
    s = SatsangAdminState()
    assert "after_boot" in callback_of(s.boot())
    assert "after_sign_in" in callback_of(s.sign_in())
    assert "after_load" in callback_of(s.load())
    assert "after_schedule" in callback_of(s.schedule())
    assert "after_key" in callback_of(s.get_key_for(SID))
    assert "after_key" in callback_of(s.get_my_key())
    s.confirm_kind, s.confirm_id = "end", SID
    ev = s.confirm()
    assert "after_action" in callback_of(ev) and SID in script_of(ev)
    assert "after_copy" in callback_of(s.copy_secret())
    assert "/sessions/manage" in script_of(s.load())


def test_booting_with_a_token_loads_the_list_and_without_one_shows_the_sign_in():
    s = SatsangAdminState()
    assert s.after_boot(ok({"signed_in": False})) is None
    assert s.signed_in is False
    ev = s.after_boot(ok({"signed_in": True}))
    assert s.signed_in is True and "after_load" in callback_of(ev)


def test_signing_in_with_nothing_typed_says_so_and_does_not_sign_in():
    s = SatsangAdminState()
    s.after_sign_in(failed(0, "empty"))
    assert s.signed_in is False and s.notice_is_error is True and s.notice


def test_a_good_token_signs_in_and_loads_the_list():
    s = SatsangAdminState()
    ev = s.after_sign_in(ok())
    assert s.signed_in is True and "after_load" in callback_of(ev)


def test_a_rejected_token_signs_the_page_out_again():
    s = SatsangAdminState()
    s.signed_in = True
    s.upcoming = [row()]
    s.after_load(failed(401))
    assert s.signed_in is False and s.upcoming == [] and s.notice_is_error is True


def test_a_forbidden_token_keeps_the_page_but_says_why():
    s = SatsangAdminState()
    s.signed_in = True
    s.after_load(failed(403))
    assert s.signed_in is True and "administrator" in s.notice and s.notice_is_error is True


def test_a_loaded_list_fills_both_sections_and_clears_the_notice_of_a_failed_load():
    s = SatsangAdminState()
    s.notice, s.notice_is_error = "old problem", True
    s.after_load(ok([row("a", "live"), row("b", "ended")]))
    assert [r["id"] for r in s.upcoming] == ["a"] and [r["id"] for r in s.past] == ["b"]
    assert s.notice == ""


def test_scheduling_success_opens_the_secret_panel_names_the_satsang_and_reloads():
    s = SatsangAdminState()
    ev = s.after_schedule(ok({"id": SID, "title": "Thursday discourse"}, 201))
    assert s.secret_open is True and s.secret_for == "Thursday discourse"
    assert "after_load" in callback_of(ev)
    assert s.notice_is_error is False and "Thursday discourse" in s.notice


@pytest.mark.parametrize("first", ["schedule", "key", "action", "copy"])
def test_the_message_for_what_you_just_did_survives_the_reload_that_follows_it(first):
    """Found in the browser, not by the per-handler tests: scheduling says 'copy the address now'
    and then reloads the list, and a successful reload used to wipe that sentence."""
    s = SatsangAdminState()
    if first == "schedule":
        s.after_schedule(ok({"id": SID, "title": "Thursday"}, 201))
    elif first == "key":
        s.after_key(ok({"id": SID, "title": "Thursday"}))
    elif first == "action":
        s.acting = "end"
        s.after_action(ok({"id": SID}))
    else:
        s.after_copy(ok())
    said = s.notice
    assert said and not s.notice_is_error
    s.after_load(ok([row()]))
    assert s.notice == said


def test_a_successful_reload_does_clear_an_earlier_error():
    s = SatsangAdminState()
    s.after_load(failed(500))
    assert s.notice_is_error is True
    s.after_load(ok([row()]))
    assert s.notice == "" and s.notice_is_error is False


def test_scheduling_failure_says_what_to_fix_and_opens_nothing():
    s = SatsangAdminState()
    s.after_schedule(failed(0, "invalid:speaker"))
    assert s.secret_open is False and s.notice_is_error is True and "user id" in s.notice


def test_a_new_key_opens_the_panel_and_a_refusal_does_not():
    s = SatsangAdminState()
    s.after_key(ok({"id": SID, "title": "Thursday"}))
    assert s.secret_open is True and s.secret_for == "Thursday"
    s2 = SatsangAdminState()
    s2.after_key(failed(404, "Not found"))
    assert s2.secret_open is False and s2.notice_is_error is True


def test_ending_asks_first_and_only_then_acts():
    s = SatsangAdminState()
    s.ask_end(SID, "Thursday")
    assert (s.confirm_kind, s.confirm_id, s.confirm_title) == ("end", SID, "Thursday")
    s.dismiss_confirm()
    assert (s.confirm_kind, s.confirm_id) == ("", "")
    assert s.confirm() is None  # nothing pending: nothing is sent
    s.ask_end(SID, "Thursday")
    ev = s.confirm()
    assert SID in script_of(ev) and '"end"' in script_of(ev)
    assert (s.confirm_kind, s.confirm_id) == ("", "")  # asking again needs a new tap


def test_cancelling_a_satsang_also_asks_first():
    s = SatsangAdminState()
    s.ask_cancel(SID, "Thursday")
    assert s.confirm_kind == "cancel"
    assert '"cancel"' in script_of(s.confirm())


def test_after_an_action_the_list_reloads_and_the_result_is_said():
    s = SatsangAdminState()
    ev = s.after_action(ok({"id": SID}))
    assert "after_load" in callback_of(ev) and s.notice_is_error is False and s.notice
    s.after_action(failed(409, "a scheduled satsang cannot be ended"))
    assert s.notice_is_error is True


def test_hiding_the_secret_closes_the_panel_and_wipes_the_browser_copy():
    s = SatsangAdminState()
    s.secret_open, s.secret_for = True, "Thursday"
    ev = s.hide_secret()
    assert s.secret_open is False and s.secret_for == ""
    assert "satsang-secret-url" in script_of(ev)


def test_signing_out_forgets_everything_on_screen():
    s = SatsangAdminState()
    s.signed_in, s.secret_open, s.upcoming = True, True, [row()]
    ev = s.sign_out()
    assert (s.signed_in, s.secret_open, s.upcoming, s.past) == (False, False, [], [])
    assert "removeItem" in script_of(ev)


def test_copy_says_whether_it_worked():
    s = SatsangAdminState()
    s.after_copy(ok())
    assert s.notice_is_error is False and "copied" in s.notice.lower()
    s.after_copy(failed(0, "copy_failed"))
    assert s.notice_is_error is True and "show" in s.notice.lower()


# ---------------------------------------------------------------------------------------------
# 3. the component tree: legibility and the secret treatment
# ---------------------------------------------------------------------------------------------


def walk(component):
    yield component
    for child in getattr(component, "children", []):
        yield from walk(child)


def luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(fg: str, bg: str) -> float:
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_colour_pair_the_page_uses_meets_its_contrast_target():
    assert CONTRAST_PAIRS, "the page declares no colour pairs: nothing is being checked"
    for label, fg, bg, minimum in CONTRAST_PAIRS:
        assert contrast_ratio(fg, bg) >= minimum, (
            f"{label}: {contrast_ratio(fg, bg):.2f} < {minimum}"
        )
    # body text is held to AAA (7:1), not just AA
    assert max(m for *_, m in CONTRAST_PAIRS) >= 7
    assert (
        contrast_ratio("#6E6047", "#FFFCF6") >= 4.5
    )  # the palette's muted ink would be the weakest


def test_the_contrast_check_can_fail():
    assert contrast_ratio("#C0C0C0", "#FFFFFF") < 3  # a pair that should be rejected is rejected


def test_the_page_builds_and_carries_the_ids_the_browser_scripts_read():
    text = str(page())
    for ident in (
        "satsang-token",
        "satsang-title",
        "satsang-speaker",
        "satsang-when",
        "satsang-key-session",
        "satsang-secret-url",
        "satsang-secret-toggle",
    ):
        assert ident in text, ident


def test_the_secret_panel_says_it_is_a_secret_and_is_always_in_the_page():
    text = str(page())
    assert "This is a secret" in text
    assert "Anyone who has it can broadcast" in text
    # the elements the scripts fill exist whether or not the panel is showing, so a script that
    # runs before the next render still finds them
    assert "satsang-secret-url" in text and "display" in text


def px(component, key: str) -> int:
    """A style value such as '64px' as a number. Reflex stores style keys in camelCase."""
    value = dict(component.style)[key]
    return int(re.sub(r"\D", "", str(getattr(value, "_var_value", value))))


def test_every_button_is_a_large_target_in_large_type():
    buttons = [c for c in walk(page()) if type(c).__name__ == "Button"]
    assert len(buttons) >= 8
    for b in buttons:
        assert px(b, "minHeight") >= 64 and px(b, "fontSize") >= 20, dict(b.style)


def test_every_text_input_is_large_and_labelled():
    page_text = str(page()).replace(" ", "")
    idents = (
        "satsang-token",
        "satsang-title",
        "satsang-speaker",
        "satsang-when",
        "satsang-key-session",
    )
    for ident in idents:
        assert f'htmlFor:"{ident}"' in page_text, ident
    inputs = [c for c in walk(page()) if type(c).__name__ == "Input"]
    assert len(inputs) == len(idents)
    for c in inputs:
        assert px(c, "minHeight") >= 56 and px(c, "fontSize") >= 20, dict(c.style)


def test_the_token_field_is_a_password_field_and_not_remembered_by_the_browser():
    text = str(page()).replace(" ", "")
    assert 'type:"password"' in text
    assert 'autoComplete:"off"' in text


# ---------------------------------------------------------------------------------------------
# 4. registration: the edit to elder_app.py is two lines and nothing else
# ---------------------------------------------------------------------------------------------


def test_the_page_has_its_own_route_and_package():
    assert ROUTE == "/satsang-admin"
    assert (HERE.parent / "elder_app" / "pages" / "__init__.py").is_file()


def test_the_app_serves_both_the_elder_screen_and_the_admin_page():
    from elder_app.elder_app import app

    routes = set(app._unevaluated_pages)
    assert "index" in routes and "satsang-admin" in routes


def test_elder_app_py_only_gained_the_import_and_the_add_page_line():
    """Two lines mention the page: the import (add_page needs the function in scope) and the
    add_page call. There is a navigation structure (the elders' bottom tabs, which already has a
    Satsang tab for the listener screen), and the admin page deliberately has NO entry in it: an
    elder is never shown a way into an admin page."""
    source = ELDER_APP_PY.read_text(encoding="utf8")
    lines = [ln for ln in source.splitlines() if "satsang_admin" in ln or "satsang-admin" in ln]
    assert len(lines) == 2, lines
    assert any(ln.startswith("from elder_app.pages") and "import" in ln for ln in lines)
    assert sum("add_page(" in ln for ln in lines) == 1
    assert "/satsang-admin" not in source  # the route is named once, in the page's own file


def test_the_page_module_does_not_import_the_elder_app_it_is_kept_independent_of():
    source = Path(page_mod.__file__).read_text(encoding="utf8")
    assert "elder_app.elder_app" not in source and "from elder_app import" not in source
