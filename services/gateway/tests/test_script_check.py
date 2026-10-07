"""How much of a transcript is written in the script its language is written in.

Found by the Week 8 gate (`infra/ai/GATE_WEEK8.md`): the ASR labels a note `te` and returns the words
in Devanagari (37 of 53 delivered Telugu transcripts had 0% Telugu-script letters); the next stages treat
it as Telugu and a Hindi receiver is handed fluent-looking nonsense, with nothing flagged. This measures
the one thing that is cheap to check and unambiguous: the share of the transcript's LETTERS that are in
the expected script. It is a smell test, not a language identifier: it says nothing about whether the
words are right.

Written before app/script_check.py exists.
"""

import pytest
from app.script_check import script_share

TELUGU = "అఆఇఈ"  # four Telugu letters
DEVANAGARI = "अआइई"  # four Devanagari letters
LATIN = "abcd"


@pytest.mark.parametrize(
    ("text", "language", "expected"),
    [
        (TELUGU, "te", 1.0),
        (DEVANAGARI, "te", 0.0),  # the gate's finding: labelled te, written in Devanagari
        (LATIN, "te", 0.0),
        (DEVANAGARI, "hi", 1.0),
        (LATIN, "hi", 0.0),  # Hindi in Latin transliteration
        (LATIN, "en", 1.0),
        (TELUGU, "en", 0.0),
        (TELUGU + LATIN, "te", 0.5),
        (TELUGU + "abcdefgh", "te", pytest.approx(1 / 3)),
    ],
)
def test_the_share_of_letters_in_the_expected_script(text, language, expected):
    assert script_share(text, language) == expected


def test_digits_punctuation_and_spaces_are_not_letters():
    assert script_share("  " + TELUGU + " 123 , . ? ! ", "te") == 1.0


def test_a_language_with_no_known_script_is_not_judged():
    # Tamil is not one of the pipeline's languages: "unknown", never "wrong".
    assert script_share("தமிழ்", "ta") is None


def test_text_with_no_letters_is_not_judged():
    assert script_share("123 ... ???", "te") is None
    assert script_share("", "te") is None


def test_the_language_tag_may_carry_a_region():
    assert script_share(TELUGU, "te-IN") == 1.0
