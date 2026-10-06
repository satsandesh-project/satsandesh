"""Arithmetic and the correctness audit for the burst mode of real_driver.py. Stdlib only.

Kept apart from the driver so it can be tested without a running stack
(test_load_stats.py). The two rules it enforces:

  * no measurements is an error or an empty summary, never a number: an empty list must not
    read as "0 seconds";
  * "the audio endpoint answered 200" is not "the audio is speech": a rendering counts as ok
    only when its loudness was measured and is above the silence floor.
"""

from __future__ import annotations

import math

# 16-bit PCM RMS. Piper speech measured in the thousands in the earlier proofs; a generated
# silence is 0. 50 is far below any speech and far above digital silence.
SILENCE_RMS = 50


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in (0, 100]) of the values; raises on an empty list."""
    if not values:
        raise ValueError("percentile of no measurements")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "p50": round(percentile(values, 50), 2),
        "p90": round(percentile(values, 90), 2),
        "worst": round(max(values), 2),
        "mean": round(sum(values) / len(values), 2),
    }


def audio_verdict(stats: dict | None) -> str:
    """ok | silent | no_audio | not_a_wav | unmeasured, for one fetched rendering."""
    if not stats or stats.get("http") != 200:
        return "no_audio"
    if "not_a_wav" in stats:
        return "not_a_wav"
    if "rms" not in stats:
        return "unmeasured"
    return "silent" if stats["rms"] < SILENCE_RMS else "ok"


def audit(sent_ids: list[str], views: dict[str, list[str]]) -> dict:
    """Correctness findings from what each reader saw: ids a reader never saw (lost), ids a
    reader saw more than once (duplicated), and ids nobody here sent (unexpected). Each is a
    {reader: [ids]} map; all three empty means clean."""
    sent = set(sent_ids)
    lost: dict[str, list[str]] = {}
    duplicated: dict[str, list[str]] = {}
    unexpected: dict[str, list[str]] = {}
    for reader, seen in views.items():
        missing = [i for i in sent_ids if i not in seen]
        if missing:
            lost[reader] = missing
        twice = sorted({i for i in seen if seen.count(i) > 1})
        if twice:
            duplicated[reader] = twice
        extra = sorted({i for i in seen if i not in sent})
        if extra:
            unexpected[reader] = extra
    return {"lost": lost, "duplicated": duplicated, "unexpected": unexpected}
