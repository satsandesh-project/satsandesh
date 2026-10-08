"""Is a transcript written in the script its language is written in?

Found by the Week 8 gate (`infra/ai/GATE_WEEK8.md`): the ASR labels a voice note `te` and returns the
words in Devanagari (37 of 53 delivered Telugu transcripts had 0% Telugu-script letters). The next
stages trust the label, translate the Devanagari as if it were Hindi, and a receiver is handed
fluent-looking nonsense with nothing flagged.

This measures the one thing that is cheap and unambiguous: the share of the transcript's LETTERS that
are in the expected script. It is a smell test, not a language identifier, and it says nothing about
whether the words are right. Digits, punctuation, spaces and combining marks are not letters. A language
with no script listed here is not judged (`None`: unknown, never wrong).
"""

from __future__ import annotations

import unicodedata

# The pipeline's languages (contracts/ai LanguageCode) and the script each is written in.
SCRIPT_NAMES = {"te": "Telugu", "hi": "Devanagari", "en": "Latin"}


def script_name(language: str) -> str | None:
    return SCRIPT_NAMES.get(language.split("-")[0].lower())


def script_share(text: str, language: str) -> float | None:
    """The fraction (0..1) of `text`'s letters in `language`'s script, or None when it cannot be
    judged (a language with no listed script, or text with no letters)."""
    name = script_name(language)
    if name is None:
        return None
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    wanted = name.upper()
    in_script = sum(1 for ch in letters if unicodedata.name(ch, "").split(" ")[0] == wanted)
    return in_script / len(letters)
