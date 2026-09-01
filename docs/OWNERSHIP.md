# Ownership & Collision Prevention

**Status:** Proposed — needs all four members to agree before it means anything.
**Amends:** `docs/CONVENTIONS.md` (which stays in force; this adds to it).
**Written:** 2026-09-01, after Month 1 produced two complete, incompatible
implementations of the same component.

---

## 1. What actually went wrong

Not laziness, not miscommunication, and not "rotation is bad." The mechanism
was specific and boring:

> **Two documents in this project both describe who owns what, and they
> contradict each other about the single most important component.**

**Document A — `README.md` § "Team & ownership" and `docs/work-breakdown.md`**
(both live in this repo, both restate the proposal's Section 12 roles):

| | Owns |
|---|---|
| Student 2 | **Gateway**, Matrix/custom backbone, **PostgreSQL**, Docker, deployment, push, backups |
| Student 3 | ASR/MT/TTS services, pipeline latency, GPU serving — *no gateway, no data model* |

**Document B — `SatSandesh_Month1_Schedule.docx`**, which states outright:
*"Roles from the proposal are set aside as agreed."*

| Week | | Task |
|---|---|---|
| 1 | M3 | **FastAPI gateway skeleton** |
| 2 | M3 | **Data model + migrations: users, circles, memberships, messages** |
| 1 | M2 | Docker Compose skeleton |
| 3 | M2 | Circles + memberships, announcement channels |

So: **Document A says the gateway and Postgres are M2's. Document B says the
gateway skeleton and data model are M3's.**

M2 built a gateway, a backbone, a schema, Docker and deployment — correct
under Document A. M3 built a gateway, a schema, WS delivery and offline sync —
correct under Document B. Both followed a real, written plan. Neither was
freelancing. The plans disagreed, and nothing in the repo forced that
disagreement to surface until two finished implementations collided.

Document B says the proposal roles were "set aside as agreed" — but
Document A, which is what you actually read when you clone this repo, was
never updated to say so. That gap is the whole story.

### It was flagged in advance, in writing, and still happened

`docs/work-breakdown.md` (2026-08-19, by M2):

> *"Week 2's 'data model + migrations' task (users / circles / memberships
> schema, Student 3) **has not landed in this repo** — no such schema exists
> on `main` or anywhere in the tree, confirmed by search. ... **If a canonical
> users/circles schema arrives later, these will need reconciling** — flagged
> here rather than discovered as a conflict during a merge."*

That is an accurate, early, well-intentioned warning. It still didn't prevent
anything, for two reasons worth naming:

1. **It was true that the schema wasn't in `main` — but not that it didn't
   exist.** M3 had built it. It sat in PRs #4 and #5, unmerged, for ten days.
   Unmerged work is invisible work.
2. **A warning in a doc nobody is required to read is not a control.** It
   needed to be a blocking review request, not a paragraph.

---

## 2. The rules

Each one maps to something that actually happened this month, not to general
best practice.

### R1 — One source of truth for ownership

`.github/CODEOWNERS` is it. If a claim about who owns what isn't in that file,
it isn't binding. `README.md`'s table and `docs/work-breakdown.md` are
**descriptive** — useful context, not authority. When they drift from
CODEOWNERS, CODEOWNERS wins and the prose gets fixed.

*Prevents: the exact A-vs-B conflict above.*

### R2 — Push to the shared repo daily, even when unfinished

Branch early, push early, open a **draft PR** on day one with a one-line
"what I'm building." Work living only on a laptop or in a personal repo does
not exist as far as the team can tell, and Month 1 lost two weeks of
parallel-build time to exactly that.

*Prevents: two people building the same thing without either one able to see it.*

### R3 — Review within 48 hours, or say why not

PRs #4 and #5 sat open for ten days. Everything downstream of them was
built against a `main` that didn't contain them — including work by people
who had no way to know the schema already existed. A stale PR is not a
neutral state; it actively misleads everyone else about what exists.

*Prevents: invisible-work collisions, and the "it isn't in main so I'll build my own" fork.*

### R4 — Never delete another owner's code inside a feature merge

If two implementations collide, that is an **ADR conversation**, not a merge
commit. Removing code that someone else authored and merged requires that
person's explicit approval **on that PR** — CODEOWNERS now requests it
automatically.

Month 1's `Remove services/gateway (M3's) -- gateway/ (M2's) is the one going
forward` deleted ~4,100 lines of merged, tested, CI-green code belonging to
someone who wasn't on the review. Whatever the right technical outcome was,
that was the wrong way to reach it.

*Prevents: overwriting.*

### R5 — An ADR past its time-box is an escalation, not an obstacle to route around

ADR 0002 (Matrix vs custom-lite) was due Week 3. Work depending on it started
Week 2. Everyone did the sensible local thing — M2 built behind an interface,
M3 built directly on Postgres — and those sensible local choices are exactly
what diverged.

When a decision is late and people are blocked, it goes to the supervisor
that week. It does not get worked around silently in four directions.

*Prevents: divergence-by-improvisation.*

### R6 — One ADR file per decision, one status

At one point two copies of ADR 0002 existed simultaneously: one saying
`Proposed`, one saying *"Option A formally confirmed by the whole team."*
Both were in the project. Both couldn't be true.

An ADR changes status in a PR that everyone is a code owner on (already
configured), or it hasn't changed status.

*Prevents: "we agreed" meaning different things to different people.*

---

## 3. Turn this on (5 minutes, one person, needs admin)

CODEOWNERS only requests reviews. To make it actually block, on
`github.com/satsandesh-project/satsandesh` → **Settings → Branches → Add rule**
for `main`:

- ☑ Require a pull request before merging
- ☑ Require approvals — **1**
- ☑ **Require review from Code Owners**  ← *this is the one that matters*
- ☑ Require status checks to pass — select `lint-and-test`

This repo is **public**, so all of the above is free. It was unavailable
earlier in the project when the repo was private, which is part of why the
rules stayed honour-system.

---

## 4. The unresolved bit — a genuine team decision

**Which role table governs from Month 2 onward?**

- **Option 1 — proposal roles** (`README.md`, `work-breakdown.md`): fixed
  long-term ownership. M2 platform, M3 speech/AI, etc. Matches how M2 and M4
  have actually worked. Cost: M3 has spent a month on chat backbone work that
  isn't his under this reading.
- **Option 2 — Month 1 schedule's rotation**: everyone rotates through every
  layer. Matches how M1 and M3 have actually worked, and is better pedagogy.
  Cost: it is what produced this collision, and needs R1–R6 to be survivable.

**Whichever wins, the losing document must be edited to say it is superseded**,
not just quietly ignored — that is the failure this whole document exists to
prevent.

Related: `docs/BACKBONE_DECISION_BRIEF.md` for what each backbone
implementation actually contained, and what was lost in the swap.

---

## 5. Where each member stands right now

| | Blocked on | Has outstanding |
|---|---|---|
| **M1** Kshitiz | nobody | — (Week 3/4 re-wired to the current gateway, merged) |
| **M2** Veerendra | nobody | he is the current foundation |
| **M3** Sandesh | the gateway swap | W3 offline-sync, W4 undo / audio-labels / quiet-hours — built and tested, currently orphaned, need re-pointing at `gateway/` |
| **M4** Sainathan | nobody | SRS v1, paper prototype, interview consolidation, taxonomy, Month-1 retro — never had a code dependency |

Only M3 is genuinely blocked, and it is the same position M1 was in before
his Week 3/4 work was re-wired — real work stranded against a backbone that
no longer exists, not work that was never done.
