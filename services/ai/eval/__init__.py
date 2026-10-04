"""
services/ai/eval — the Week-8 evaluation harness (M4).

Two measurements the proposal commits to, kept deliberately separate
because they answer different questions and have different owners:

  adequacy      Did the *translation* preserve the meaning? Judged by
                bilingual volunteers, 50 renderings per language pair.
                Target: >= 85% of pilot voice notes "meaning preserved".
                M3's lane is what it measures; this harness only samples,
                stores and scores.

  stewardship   Did the *classifier* decide correctly? Measured against a
                hand-labelled set of ~300 messages drawn from consented
                pilot traffic. The headline number is the FALSE-HOLD RATE,
                which the proposal calls "a first-class defect" -- not
                accuracy, because an over-blocking filter that is 95%
                accurate is still a product that silences elders.

See README.md for what is deliberately missing and why.
"""
