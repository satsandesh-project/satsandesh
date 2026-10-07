"""Tests for load_stats.py: the arithmetic and the correctness audit behind the burst mode of
real_driver.py. Stdlib only, so it runs anywhere:

    python -m pytest infra/ai/e2e/test_load_stats.py

Bare `pytest` in CI only collects tests/ (see .github/workflows), so this file is NOT run by CI.
"""

import pytest
from load_stats import audio_verdict, audit, percentile, summarize


def test_percentile_is_nearest_rank_on_the_sorted_values():
    values = [10, 1, 9, 2, 8, 3, 7, 4, 6, 5]  # deliberately unsorted
    assert percentile(values, 50) == 5
    assert percentile(values, 90) == 9
    assert percentile(values, 100) == 10


def test_percentile_of_one_value_is_that_value():
    assert percentile([7.5], 50) == 7.5
    assert percentile([7.5], 90) == 7.5


def test_percentile_of_nothing_is_an_error_not_zero():
    # "no measurements" must never read as "0 seconds".
    with pytest.raises(ValueError):
        percentile([], 50)


def test_summarize_reports_p50_p90_worst_and_mean():
    s = summarize([float(i) for i in range(1, 11)])
    assert s == {"n": 10, "p50": 5.0, "p90": 9.0, "worst": 10.0, "mean": 5.5}


def test_summarize_of_nothing_carries_no_numbers():
    assert summarize([]) == {"n": 0}


@pytest.mark.parametrize(
    ("stats", "verdict"),
    [
        (None, "no_audio"),
        ({"http": 404}, "no_audio"),
        ({"http": 200, "bytes": 10, "not_a_wav": "boom"}, "not_a_wav"),
        ({"http": 200, "bytes": 64044}, "unmeasured"),  # 200 but no loudness: not "ok"
        ({"http": 200, "bytes": 64044, "rms": 0}, "silent"),
        ({"http": 200, "bytes": 64044, "rms": 12}, "silent"),
        ({"http": 200, "bytes": 64044, "rms": 2400}, "ok"),
    ],
)
def test_audio_verdict(stats, verdict):
    assert audio_verdict(stats) == verdict


def test_audit_clean_run_has_no_findings():
    sent = ["a", "b"]
    views = {"bob": ["a", "b"], "carol": ["b", "a"]}
    assert audit(sent, views) == {"lost": {}, "duplicated": {}, "unexpected": {}}


def test_audit_names_the_reader_who_never_saw_a_note():
    found = audit(["a", "b"], {"bob": ["a", "b"], "carol": ["a"]})
    assert found["lost"] == {"carol": ["b"]}


def test_audit_catches_a_note_delivered_twice_to_one_reader():
    found = audit(["a"], {"bob": ["a", "a"], "carol": ["a"]})
    assert found["duplicated"] == {"bob": ["a"]}
    assert found["lost"] == {}


def test_audit_does_not_call_earlier_messages_in_the_circle_unexpected():
    # The circle is reused across depths: what was already there before the burst is known.
    found = audit(["a"], {"bob": ["old1", "a", "old2"]}, known=["old1", "old2"])
    assert found == {"lost": {}, "duplicated": {}, "unexpected": {}}


def test_audit_still_flags_a_message_that_is_neither_sent_nor_known():
    found = audit(["a"], {"bob": ["old1", "a", "zzz"]}, known=["old1"])
    assert found["unexpected"] == {"bob": ["zzz"]}


def test_audit_catches_a_message_nobody_sent():
    found = audit(["a"], {"bob": ["a", "zzz"]})
    assert found["unexpected"] == {"bob": ["zzz"]}
