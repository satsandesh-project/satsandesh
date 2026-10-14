"""assets/satsang_player.js: the satsang listener's player, run for real under Node.

The file is plain JavaScript on purpose (not a string in Python), so these tests load the very
file the browser loads. It is driven against a fake audio element, a fake `Hls` class that the
test can fire events into, and a clock the test moves: nothing sleeps, nothing touches a network.
If Node is missing the tests are SKIPPED, which pytest prints; they are not silently passing.

What the player is responsible for, and these tests pin:

- **It starts inside the tap.** `play()` must call `audio.play()` synchronously: iOS Safari
  refuses to start audio that is not requested during a touch, and a round trip through the
  Reflex server would lose it. (Whether a real iPhone accepts it was NOT checked: no iPhone was
  available.)
- **It reports what it knows and nothing more:** idle, connecting, playing, stalled, failed, with
  how long it has been waiting and why it failed. The words the elder reads are chosen elsewhere.
- **It keeps trying** when the network drops mid-discourse, and it gives up only for a reason a
  retry cannot fix (a stream that never loaded, a browser that cannot play it).
- **It tells "never loaded" from "dropped".** A manifest that fails before any sound is the
  signature of a phone that does not trust the site's certificate (or has no route), and is
  reported as `unreachable` so the screen can say so; the same failure after sound is a drop,
  and is retried.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
RUNNER = HERE / "satsang_player_runner.js"
PLAYER = HERE.parent / "assets" / "satsang_player.js"

URL = "https://stream.example/hls/abc.m3u8"
MANIFEST_FATAL = {"fatal": True, "type": "networkError", "details": "manifestLoadError"}
NETWORK_FATAL = {"fatal": True, "type": "networkError", "details": "fragLoadError"}
MEDIA_FATAL = {"fatal": True, "type": "mediaError", "details": "bufferAppendError"}
OTHER_FATAL = {"fatal": True, "type": "otherError", "details": "internalException"}
NON_FATAL = {"fatal": False, "type": "networkError", "details": "fragLoadError"}


def run(steps, **scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the player tests did NOT run")
    payload = {"hls": "supported", "native": False, "steps": steps, **scenario}
    proc = subprocess.run(
        [node, str(RUNNER)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout)
    assert "error" not in out, out["error"]
    return out


def play(*after):
    return [{"call": "play"}, *after]


def snap():
    return {"call": "status"}


def test_the_player_is_a_plain_file_the_browser_loads():
    assert PLAYER.is_file()
    source = PLAYER.read_text(encoding="utf8")
    assert "window.satsang" in source or "root.satsang" in source
    assert "localStorage" not in source and "sessionStorage" not in source  # it stores nothing


def test_before_anything_is_tapped_it_is_idle():
    out = run([snap()])
    assert out["results"][0] == {"status": "idle", "waiting_s": 0, "error_kind": ""}


def test_a_tap_starts_audio_inside_the_tap_not_later():
    out = run(play())
    # in this order, all during the one call: nothing waits for the network or the server
    assert out["log"] == [
        "Hls.new",
        f"hls.loadSource:{URL}",
        "hls.attachMedia:true",
        "audio.play",
    ]
    assert out["final"]["status"] == "connecting"


def test_sound_arriving_means_playing_and_waiting_resets():
    out = run(play({"audio": "playing"}, {"advance_s": 5}, snap()))
    assert out["results"][-1] == {"status": "playing", "waiting_s": 0, "error_kind": ""}


def test_a_stall_after_sound_counts_up_and_clears_when_sound_returns():
    out = run(
        play(
            {"audio": "playing"},
            {"audio": "waiting"},
            {"advance_s": 9},
            snap(),
            {"advance_s": 30},
            snap(),
            {"audio": "playing"},
            snap(),
        )
    )
    first, second, cleared = [r for r in out["results"] if r is not None]
    assert (first["status"], first["waiting_s"]) == ("stalled", 9)
    assert (second["status"], second["waiting_s"]) == ("stalled", 39)
    assert (cleared["status"], cleared["waiting_s"]) == ("playing", 0)


def test_a_stall_event_is_ignored_before_any_sound_it_is_just_connecting():
    out = run(play({"audio": "waiting"}, {"advance_s": 3}, snap()))
    assert out["results"][-1]["status"] == "connecting"


def test_connecting_with_no_sound_reports_how_long_it_has_been_trying():
    out = run(play({"advance_s": 21}, snap()))
    assert out["results"][-1] == {"status": "connecting", "waiting_s": 21, "error_kind": ""}


def test_a_stream_that_never_loads_is_reported_unreachable_and_stops_trying():
    out = run(play({"hls": ["x", MANIFEST_FATAL]}, snap()))
    assert out["results"][-1] == {"status": "failed", "waiting_s": 0, "error_kind": "unreachable"}
    assert out["hls"] == [{"destroyed": True}]


def test_a_manifest_timeout_before_any_sound_is_also_unreachable():
    timeout = {"fatal": True, "type": "networkError", "details": "manifestLoadTimeOut"}
    out = run(play({"hls": ["x", timeout]}, snap()))
    assert out["results"][-1]["error_kind"] == "unreachable"


def test_a_network_failure_after_sound_is_a_drop_and_is_retried_not_given_up_on():
    out = run(play({"audio": "playing"}, {"hls": ["x", NETWORK_FATAL]}, {"advance_s": 2}, snap()))
    assert out["results"][-1]["status"] == "stalled"
    assert out["log"].count("hls.startLoad") == 1
    assert out["hls"] == [{"destroyed": False}]


def test_after_five_quick_retries_it_keeps_trying_on_a_timer_instead_of_giving_up():
    errors = [{"hls": ["x", NETWORK_FATAL]}] * 7
    out = run(play({"audio": "playing"}, *errors, snap()))
    assert out["results"][-1]["status"] == "stalled"  # never "failed": the speaker may come back
    assert out["log"].count("hls.startLoad") == 5
    assert out["timers"] == [5000]  # one retry is scheduled
    after = run(play({"audio": "playing"}, *errors, {"fire_timers": True}))
    assert after["log"].count("hls.startLoad") == 6  # and the timer really retries


def test_a_non_fatal_error_is_left_to_the_player_to_absorb():
    out = run(play({"audio": "playing"}, {"hls": ["x", NON_FATAL]}, snap()))
    assert out["results"][-1]["status"] == "playing"
    assert "hls.startLoad" not in out["log"]


def test_a_media_error_is_recovered_a_few_times_then_reported():
    out = run(play({"audio": "playing"}, *([{"hls": ["x", MEDIA_FATAL]}] * 4), snap()))
    assert out["log"].count("hls.recoverMediaError") == 3
    assert out["results"][-1] == {"status": "failed", "waiting_s": 0, "error_kind": "media"}


def test_any_other_fatal_error_is_reported_as_other():
    out = run(play({"hls": ["x", OTHER_FATAL]}, snap()))
    assert out["results"][-1]["error_kind"] == "other"


def test_a_browser_without_hls_js_support_uses_native_hls():
    out = run(play({"audio": "playing"}, snap()), hls="unsupported", native=True)
    assert out["audio_src"] == URL
    assert "Hls.new" not in out["log"] and out["log"].count("audio.play") == 1
    assert out["results"][-1]["status"] == "playing"


def test_a_native_error_before_sound_is_unreachable():
    """On an iPhone the media stack, not the page, fetches the stream, and it does not honour a
    certificate warning the page was let through: this is how an untrusted certificate shows up
    there."""
    out = run(play({"audio": "error"}, snap()), hls="unsupported", native=True)
    assert out["results"][-1] == {"status": "failed", "waiting_s": 0, "error_kind": "unreachable"}


def test_a_native_error_after_sound_is_retried():
    out = run(
        play({"audio": "playing"}, {"audio": "error"}, snap()), hls="unsupported", native=True
    )
    assert out["results"][-1]["status"] == "stalled"
    assert out["timers"] == [3000]


def test_a_browser_that_cannot_play_hls_at_all_says_so():
    out = run(play(snap()), hls="unsupported", native=False)
    assert out["results"][-1] == {"status": "failed", "waiting_s": 0, "error_kind": "unsupported"}


def test_no_audio_element_or_no_stream_address_is_a_failure_not_a_crash():
    assert run(play(snap()), no_audio=True)["results"][-1]["error_kind"] == "no_stream"
    assert run(play(snap()), no_url=True)["results"][-1]["error_kind"] == "no_stream"


def test_a_tap_the_browser_refuses_is_reported_as_blocked():
    out = run(play({"advance_s": 1}, snap()), play_rejects="NotAllowedError")
    assert out["results"][-1] == {"status": "failed", "waiting_s": 0, "error_kind": "blocked"}


def test_stopping_tears_everything_down_and_goes_idle():
    out = run(play({"audio": "playing"}, {"call": "stop"}, snap()))
    assert out["results"][-1] == {"status": "idle", "waiting_s": 0, "error_kind": ""}
    assert out["hls"] == [{"destroyed": True}]
    assert "audio.pause" in out["log"]
    assert out["audio_src"] == ""


def test_a_late_event_after_stop_changes_nothing():
    out = run(
        play(
            {"audio": "playing"}, {"call": "stop"}, {"audio": "waiting"}, {"advance_s": 50}, snap()
        )
    )
    assert out["results"][-1]["status"] == "idle"


def test_tapping_twice_replaces_the_first_player_rather_than_stacking_two():
    out = run([{"call": "play"}, {"call": "play"}, snap()])
    assert out["hls"] == [{"destroyed": True}, {"destroyed": False}]
    assert out["log"].count("Hls.new") == 2


def test_the_status_carries_only_three_fields_and_never_the_stream_address():
    out = run(play({"audio": "playing"}, snap()))
    assert set(out["results"][-1]) == {"status", "waiting_s", "error_kind"}
    assert URL not in json.dumps(out["results"])
