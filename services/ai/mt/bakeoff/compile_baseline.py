"""
Compiles services/ai/mt/bakeoff/results.json + ratings.json into
docs/MT_BASELINE.md: a results table, mean adequacy overall and split by
language pair (te-en vs hi-en) and source type (FLORES vs community), and an
honest statement against the proposal's >=4.0/5 adequacy target.

Run only once real ratings exist (see rate_adequacy.py). This script does
not itself judge anything -- it only aggregates numbers a human already
entered.

Usage:
    cd services/ai
    PYTHONPATH=../.. ./.venv/Scripts/python.exe mt/bakeoff/compile_baseline.py
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

_BAKEOFF_DIR = Path(__file__).resolve().parent
_RESULTS_PATH = _BAKEOFF_DIR / "results.json"
_RATINGS_PATH = _BAKEOFF_DIR / "ratings.json"
_OUT_PATH = Path(__file__).resolve().parents[4] / "docs" / "MT_BASELINE.md"

ADEQUACY_TARGET = 4.0


def _language_pair(source_language: str) -> str:
    return {"te": "te-en", "hi": "hi-en"}.get(source_language, f"{source_language}-en")


def main() -> None:
    if not _RESULTS_PATH.is_file():
        print(f"{_RESULTS_PATH} does not exist -- run run_batch.py first.")
        raise SystemExit(1)
    if not _RATINGS_PATH.is_file():
        print(f"{_RATINGS_PATH} does not exist -- run rate_adequacy.py first.")
        raise SystemExit(1)

    results = {r["id"]: r for r in json.loads(_RESULTS_PATH.read_text(encoding="utf-8"))}
    ratings = json.loads(_RATINGS_PATH.read_text(encoding="utf-8"))

    rated = []
    for entry_id, rating in ratings.items():
        if entry_id not in results:
            continue
        result = results[entry_id]
        rated.append(
            {
                **result,
                "language_pair": _language_pair(result["source_language"]),
                "rating": rating["rating"],
                "note": rating.get("note"),
            }
        )

    if not rated:
        print("No rated entries found -- nothing to compile.")
        raise SystemExit(1)

    overall_mean = mean(r["rating"] for r in rated)

    by_pair: dict[str, list[int]] = {}
    by_source_type: dict[str, list[int]] = {}
    for r in rated:
        by_pair.setdefault(r["language_pair"], []).append(r["rating"])
        by_source_type.setdefault(r["source_type"], []).append(r["rating"])

    lines = []
    lines.append("# MT pivot adequacy baseline")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by "
        "`services/ai/mt/bakeoff/compile_baseline.py` from real human ratings in "
        "`ratings.json` (gitignored, never committed) against `results.json` "
        "(gitignored, never committed) -- both local-only. This document is the "
        "only artifact from that data meant to be shared."
    )
    lines.append("")
    lines.append(f"**{len(rated)} rated entries.** Adequacy target (proposal): >= {ADEQUACY_TARGET}/5.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Overall mean adequacy: {overall_mean:.2f}/5** "
                  f"({'MEETS' if overall_mean >= ADEQUACY_TARGET else 'BELOW'} the {ADEQUACY_TARGET} target)")
    lines.append("")
    lines.append("| Language pair | n | Mean adequacy |")
    lines.append("|---|---|---|")
    for pair in sorted(by_pair):
        scores = by_pair[pair]
        lines.append(f"| {pair} | {len(scores)} | {mean(scores):.2f} |")
    lines.append("")
    lines.append("| Source type | n | Mean adequacy |")
    lines.append("|---|---|---|")
    for source_type in sorted(by_source_type):
        scores = by_source_type[source_type]
        lines.append(f"| {source_type} | {len(scores)} | {mean(scores):.2f} |")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| id | source type | language pair | rating | note |")
    lines.append("|---|---|---|---|---|")
    for r in rated:
        note = (r["note"] or "").replace("|", "\\|")
        lines.append(f"| {r['id']} | {r['source_type']} | {r['language_pair']} | {r['rating']} | {note} |")
    lines.append("")
    lines.append(
        "Source text and translations themselves are not reproduced in this table "
        "(FLORES text is public and could be, but community-sourced text is not, "
        "and this table doesn't split the two apart) -- see the local, gitignored "
        "`results.json` for full text if you're the person who ran this."
    )
    lines.append("")

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {_OUT_PATH} from {len(rated)} rated entries (overall mean {overall_mean:.2f}/5).")


if __name__ == "__main__":
    main()
