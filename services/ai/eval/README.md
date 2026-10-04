# services/ai/eval — the evaluation harness

**Owner:** M4 Sainathan · **Status:** Week-8 scaffold, built in Week 7.
Scoring is complete and tested. **There is no data yet, deliberately.**

Two measurements the proposal commits to, kept separate because they
answer different questions and are owned by different lanes:

| | Question | Target | Measures |
|---|---|---|---|
| **Adequacy** | Did the *translation* preserve the meaning? | ≥ 85 % meaning preserved, **per language pair** | M3's lane |
| **Stewardship** | Did the *classifier* decide correctly? | false-hold rate tracked as a first-class defect | M4's lane |

## Why the headline number is the false-hold rate, not accuracy

Accuracy hides exactly what matters here. A classifier that holds every
message scores 20 % on a balanced five-class set and is 100 % useless; one
that allows everything scores well on a corpus that is mostly devotional.
The two *directional* rates are the ones that say whether the product is
usable and whether it is safe:

- **false hold** — gold says A or B (it belongs in the circle) and the
  classifier did not allow it. **NUDGE and BLOCK count, not only HOLD**:
  from the sender's side, "my message did not arrive" is the same harm
  whichever action caused it.
- **missed harm** — gold says E and the classifier allowed it.

Every rate is returned with its numerator and denominator, so a report says
"3 of 112" rather than "2.7 %" — a number the reader can check instead of
one they must trust. An empty sample has **no** rate (`None`), not 0 %,
because 0 % reads as success.

## Rules the code enforces, so they cannot be forgotten under deadline

- **Provenance is never mixed silently.** `synthetic` rows were invented by
  the same people who wrote the policy; folding them into a pilot baseline
  flatters it. A reported baseline uses `Provenance.PILOT`; `redteam` rows
  (Week 9) are scored separately, because a filter that catches 100 % of
  deliberate attacks and holds half of ordinary devotional speech is not a
  good filter.
- **Unscored items are counted, not dropped.** A run that failed to
  classify a third of the set must not look clean on the rest.
- **Degraded verdicts are tracked apart.** A fail-closed hold is the
  classifier *not answering*, not judging badly; counting those as
  decisions would make an outage look like an opinion.
- **Rater disagreement is reported, not resolved by majority.** Two
  bilingual readers disagreeing about whether meaning survived is itself a
  finding about the translation.
- **A rating carries its rater, and a gold label its author.** A score with
  no author is a number somebody typed.
- **No identities.** A row carries a participant *code* (`E07`), validated
  by pattern — `participant_code="Lakshmi"` is rejected. These files are
  committed and the repository goes public under Apache-2.0 in Week 12; the
  code→identity sheet lives outside the repo
  (`docs/research/interview-consent.md` Part D).

## What is deliberately missing

**Data.** Both sets are empty and will stay so until there is something
honest to put in them:

- **Adequacy** needs real renderings from the live MT and TTS path. As of
  2026-10-04 the real MT and render services have never run (no
  `HF_TOKEN` — M2's note on #99), so "a note becomes N renderings" has only
  been shown against the mock. Scoring mock output would produce a baseline
  that measures the mock.
- **The ~300-message moderation set** must be drawn from *consented pilot
  traffic* (proposal §13) — which does not exist until Week 11. Before
  then the only honest rows are `synthetic`, and those cannot be a
  baseline.
- **Bilingual raters** have not been named (SRS §5 Q3), and they sign the
  same consent as pilot elders (`docs/security-checklist.md` B5).

A harness with no data is the correct state for Week 7. Building it now
means integration Monday is wiring, not writing.

## Usage

```python
from services.ai.eval.metrics import score_moderation, score_adequacy
from services.ai.eval.schema import EvalSet, Provenance

evalset = EvalSet.model_validate_json(open("sets/pilot-w11.json").read())
print(score_moderation(evalset.moderation, only=Provenance.PILOT).summary())
for row in score_adequacy(evalset, only=Provenance.PILOT):
    print(row.summary())
```

## Tests

```bash
cd services/ai && PYTHONPATH=../.. ./.venv/Scripts/python.exe -m pytest eval/tests -q
```

12 tests, no model and no network: every scoring rule above, including the
ones easiest to get quietly wrong — a nudged deliverable message counting
as a false hold, a held *personal* message not counting as one, an empty
sample having no rate, and a participant name being rejected where a code
belongs.

## Next

1. **Week 8, Monday:** point the adequacy sampler at real renderings once
   the pipeline runs end to end; take the first baseline and write it down
   whatever it says.
2. **Week 9:** `redteam` rows from round 1, scored separately.
3. **Week 11:** the real ~300-message set from consented pilot traffic.
