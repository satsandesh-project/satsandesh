"""
Interactive 1-5 adequacy rating CLI for services/ai/mt/bakeoff/results.json.

This tool does not judge translation quality itself -- every rating comes
from a real person typing a number, because adequacy is a bilingual-fluency
judgment call, not something to simulate.

Usage:
    cd services/ai
    PYTHONPATH=../.. ./.venv/Scripts/python.exe mt/bakeoff/rate_adequacy.py
    PYTHONPATH=../.. ./.venv/Scripts/python.exe mt/bakeoff/rate_adequacy.py --redo flores:te-en:0

Ratings are saved to ratings.json immediately after each entry, so quitting
partway through (Ctrl+C or typing 'q' at the rating prompt) never loses
already-saved progress.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BAKEOFF_DIR = Path(__file__).resolve().parent
_RESULTS_PATH = _BAKEOFF_DIR / "results.json"
_RATINGS_PATH = _BAKEOFF_DIR / "ratings.json"


def load_results() -> list[dict]:
    if not _RESULTS_PATH.is_file():
        print(f"{_RESULTS_PATH} does not exist -- run run_batch.py first.")
        raise SystemExit(1)
    return json.loads(_RESULTS_PATH.read_text(encoding="utf-8"))


def load_ratings() -> dict:
    if not _RATINGS_PATH.is_file():
        return {}
    return json.loads(_RATINGS_PATH.read_text(encoding="utf-8"))


def save_ratings(ratings: dict) -> None:
    _RATINGS_PATH.write_text(json.dumps(ratings, ensure_ascii=False, indent=2), encoding="utf-8")


def prompt_rating(entry_id: str) -> int | None:
    """Returns an int 1-5, or None if the user typed 'q' to quit."""
    while True:
        raw = input(f"Adequacy rating for {entry_id} (1-5, or 'q' to quit): ").strip()
        if raw.lower() == "q":
            return None
        try:
            value = int(raw)
        except ValueError:
            print("  Not a number -- enter an integer 1-5, or 'q' to quit.")
            continue
        if not (1 <= value <= 5):
            print("  Out of range -- enter an integer 1-5, or 'q' to quit.")
            continue
        return value


def rate_entry(entry: dict) -> dict | None:
    print()
    print(f"--- {entry['id']} ({entry['source_type']}, {entry['source_language']}) ---")
    print(f"Source:      {entry['source_text']}")
    if entry.get("reference_text"):
        print(f"FLORES ref:  {entry['reference_text']}  (context only, not a required match)")
    print(f"Translation: {entry['translation']}")

    rating = prompt_rating(entry["id"])
    if rating is None:
        return None

    note = input("Optional note (Enter to skip): ").strip()
    return {"rating": rating, "note": note or None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--redo",
        metavar="ID",
        help="re-rate a specific entry id even if it already has a saved rating",
    )
    args = parser.parse_args()

    results = load_results()
    ratings = load_ratings()

    if args.redo:
        matches = [r for r in results if r["id"] == args.redo]
        if not matches:
            print(f"No result with id {args.redo!r} found in {_RESULTS_PATH}.")
            raise SystemExit(1)
        outcome = rate_entry(matches[0])
        if outcome is not None:
            ratings[args.redo] = outcome
            save_ratings(ratings)
            print(f"Saved rating for {args.redo}.")
        else:
            print("Quit without saving.")
        return

    to_rate = [r for r in results if r["id"] not in ratings]
    skipped = len(results) - len(to_rate)
    if skipped:
        print(
            f"Skipping {skipped} entr{'y' if skipped == 1 else 'ies'} already rated "
            f"(use --redo <id> to re-rate one specifically)."
        )
    if not to_rate:
        print("Nothing left to rate.")
        return

    print(f"{len(to_rate)} entries left to rate. Type 'q' at any prompt to stop and save progress.")

    try:
        for entry in to_rate:
            outcome = rate_entry(entry)
            if outcome is None:
                print("\nStopped. Progress so far is already saved.")
                break
            ratings[entry["id"]] = outcome
            save_ratings(ratings)
    except KeyboardInterrupt:
        print("\nInterrupted. Progress so far is already saved.")
        sys.exit(130)

    print(f"\n{len(ratings)}/{len(results)} entries rated so far. Saved to {_RATINGS_PATH}.")


if __name__ == "__main__":
    main()
