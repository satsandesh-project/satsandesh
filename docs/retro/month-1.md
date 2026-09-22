# Month 1 Retrospective and Month 2 Backlog

**Author:** M4 Sainathan (the Week-4 deliverable "Month-1 retro + Month-2
backlog", written 2026-09-22 — sixteen days late, from the repository record
rather than from memory)
**Covers:** Month 1, 2026-08-10 → 2026-09-06 (Weeks 1–4 of the compressed
3-month plan, `docs/SatSandesh_Month1_Schedule.docx`)
**Inputs:** git history, PRs #1–#55, issues #30–#44, `docs/OWNERSHIP.md`,
`docs/adr/0002-chat-backbone.md`, the Month 2–3 dependency map (rev. 4,
2026-09-18), and the two prompt journals that were kept.

A retro written two weeks after the month ended has one advantage: the
consequences are already visible. Where a Month-1 problem has since been
fixed, this says so and links the fix, so the actions list at the end is
only what is still open.

---

## 1. What was planned versus what shipped

The schedule set aside the proposal's fixed roles for "four equal rotating
shares … so no single member owns a component". This table is the plan's
own grid with the outcome written in.

| Wk | Member | Planned | Outcome |
|---|---|---|---|
| 1 | M1 | Monorepo + CI, pre-commit, branch/PR conventions | ✅ PR #9 (20 Aug), #10 (21 Aug). CI ran ruff + a placeholder test only — see §4 |
| 1 | M2 | Docker Compose skeleton + `.env` | ✅ In PR #18 (merged 1 Sep, work from Week 1) |
| 1 | M3 | FastAPI gateway skeleton: health, auth stubs, WS echo | ✅ PR #4 (opened 19 Aug, merged 29 Aug) |
| 1 | M4 | CLAUDE.md, journal template, security checklist, onboarding doc | ⚠️ CLAUDE.md (#11) and onboarding doc (#12) on 24 Aug; journal template one row; checklist five bullets. Both completed 22 Sep (#54, #55) |
| 2 | M1 | Spike A: Matrix / application-service bot | ✅ PR #15 (28 Aug) |
| 2 | M2 | Spike B: custom-lite (FastAPI WS + Postgres outbox) | ✅ `backbone/spike-custom-lite/`, in #18 |
| 2 | M3 | Data model + migrations | ✅ PR #4, #5 (29 Aug) |
| 2 | M4 | Auth + QR onboarding backend; begin elder interviews | ⚠️ Onboarding: PR #24 (2 Sep, closed on conflict) → #29 (merged 5 Sep, authored as "Claude"). Interviews: none |
| 3 | M1 | 1:1 text over WS, sent/delivered | ✅ PR #16 (28 Aug), #25 (2 Sep), #34 (7 Sep) |
| 3 | M2 | Circles + memberships; announcement channels | ✅ Circles in #18; self-join #32 (3 Sep); announcements #38 (6 Sep) |
| 3 | M3 | Offline queue + store-and-forward; Web Push scaffolding | ✅ PR #8 (24 Aug), #14 (28 Aug); client-side resend #31 (3 Sep) |
| 3 | M4 | ADR A-vs-B; taxonomy v1 draft; finish interviews (6–8) | ❌ ADR written by M2 + M1, M4 "not recorded either way"; taxonomy scaffold is M2's (10 Aug), exemplars empty; interviews none |
| 4 | M1 | Reflex elder UI shell | ✅ PR #17 (28 Aug), rewired #19 (1 Sep), Satsang tab #37 (5 Sep) |
| 4 | M2 | Client ↔ gateway end-to-end + staging deploy | ✅ #26 (2 Sep); shared link issue #44 (9 Sep), verified end-to-end |
| 4 | M3 | Audio labels, 30-second undo, quiet-hours default | ✅ Server side, PR #13 (28 Aug). Not surfaced in the UI until Week 5 (#45, open) |
| 4 | M4 | SRS v1 + paper prototype; interview consolidation; retro + Month-2 backlog | ❌ SRS §3/§5 still placeholders; prototype delivered by M1 (28 Aug, Claude Design); consolidation none; this document |

**Counting honestly:** 12 of 16 cells delivered by the assigned member in the
assigned month; 2 partial (both M4, closed 22 Sep); 2 not delivered (both M4).
Three lanes carried their whole share. One did not.

## 2. The exit gate, clause by clause

> *On staging, an onboarded elder account sends a 1:1 text message and posts
> to a circle, receives an announcement, and sees sent / delivered states —
> all through the elder UI shell. CI is green, and the A-vs-B backbone choice
> is recorded in the ADR. Each member explains their PRs in review.*

| Clause | Met? | When | Evidence |
|---|---|---|---|
| On staging | ✅ | 9 Sep | Issue #44 — `http://10.110.11.31:8095`, campus network only, verified end-to-end |
| Onboarded elder account | ✅ | 5 Sep | PR #29; invites moved to Postgres same day (`83eb028`) |
| Sends a 1:1 text | ✅ | 2 Sep | PR #25 |
| Posts to a circle | ✅ | 5 Sep | PR #37 (Satsang tab) |
| Receives an announcement | ✅ | 6 Sep | PR #38 |
| Sees sent / delivered | ✅ | 7 Sep | PR #16, #25, #34 (per-recipient for circles) |
| Through the elder UI shell | ✅ | 1–5 Sep | PR #17, #19, #37 |
| CI is green | ⚠️ | — | Green, but hollow: root `testpaths = ["tests"]` meant `pytest` only ever ran `tests/test_placeholder.py`. The gateway's 206 tests have never run in CI. Fix is PR #51, open since 21 Sep |
| A-vs-B recorded in the ADR | ⚠️ | 31 Aug / 8 Sep | Recorded as Option A on 31 Aug; found self-contradictory (#27, 7 Sep); reversed to Option B on 8 Sep; reversal merged 18 Sep (#43). Recorded, twice, in opposite directions |
| Each member explains their PRs in review | ⚠️ | — | True for three members. M4's two PRs were opened by the shared account and reviewed by others (#29's in-memory-invite finding was Kshitiz's, the fix Veerendra's) |

The product clauses were all met between 2 and 9 September — roughly three
to six days past the end of Week 4. The process clauses are where the month
was weaker than it looked.

## 3. The numbers

| | M1 Kshitiz | M2 Veerendra | M3 Sandesh | M4 Sainathan |
|---|---|---|---|---|
| PRs opened, Month 1 (to 8 Sep) | 18 | 6 | 8 | 6 (+2 as `satsandesh-project`) |
| Commits, all history, by git author | 62 | 106 | 42 (40 of them under the shared project email — see §5.7) | 10 (+3 as "Claude") |
| Prompt journal | none in repo | 10 long-form entries | 1 file, 6 phases | 1 row (backfilled 22 Sep) |
| Code vs docs | code + docs | code + infra + docs | code | docs only until 3 Sep |

40 PRs were opened between 18 Aug and 8 Sep. Two were closed unmerged (#24
superseded, #36 superseded by #48). Four issues were opened, three closed.
Issue #35 (ADR reconciliation) drew 11 comments from three members.

## 4. What went well

- **The product clauses of the gate were met, on a working staging URL,
  with real elders' interaction model** (faces-before-names, two taps,
  undo, delivered states) rather than a demo shell. That is more than the
  compressed plan strictly required.
- **Problems were written down, in the repo, by the people who hit them.**
  `docs/OWNERSHIP.md` is a better post-mortem of the gateway collision than
  this retro could be; ADR 0002's reversal section states its own reasons;
  PR #22 and #26 explain exactly what a restore did and did not do. When the
  final report needs the AI-assisted-SE story, the material exists.
- **The reversal was made on evidence, in writing, by three members
  independently** (issue #35, PR #43). Building the Matrix backbone and then
  retiring it cost real days, but the decision to retire it was the right
  one and was taken properly.
- **Cross-lane review found real bugs** — the in-memory invite dict that
  dropped every pending QR on restart (#29), the WS transaction that was
  never committed (#42), the query that could freeze the gateway (#40). The
  "nothing merges unread" rule did its job where it was applied.
- **Conventions, CODEOWNERS and branch protection all landed** (#10, #21,
  and the 20 Sep repo setting recorded in #49). Late, but in place before
  Month 2's integration week.

## 5. What went wrong

Each item names the evidence and, where one exists, the fix already made.

### 5.1 Two complete gateways, because two documents disagreed about ownership

`README.md` / `docs/work-breakdown.md` gave the gateway and Postgres to M2;
the Month-1 schedule gave the gateway skeleton and data model to M3 and said
the proposal roles were "set aside as agreed" — without updating the README
to say so. Both members built to a written plan. PR #18 removed M3's gateway;
PR #22 restored it; PR #26 swapped the stack onto it and removed M2's. M2's
work-breakdown had flagged the risk on 19 Aug and it happened anyway.

**Fixed by:** `docs/OWNERSHIP.md` (1 Sep) and CODEOWNERS (#21). **Still
open:** the README's ownership table is now correct, but the Month-1
schedule document itself was never amended — anyone reading it cold still
sees "roles set aside". Superseded by the Month 2–3 dependency map, which
returns to fixed lanes explicitly *because* of this month.

### 5.2 Matrix was built, accepted, and reversed inside ten days

ADR 0002 accepted Option A on 31 Aug on the strength of the spikes; by 7 Sep
the ADR's own status was self-contradictory (#27), and on 8 Sep it was
reversed to Option B — the E2EE/moderation conflict, federation being out of
scope, and the two-authorization-models risk had all been in the proposal
since July. The Matrix implementation (`backbone/spike-matrix-a/`, Tuwunel,
`matrix-circle-service`) stayed in `docker-compose.yml` until #48 (21 Sep).

**Cost:** roughly M2's Week 4 plus the reconciliation thread. **Kept:** the
spike as the evidence record, per the ADR. **Lesson for the report:** the
decision criteria were written down before the spikes and were not applied
at decision time — the ADR was decided on spike *results* (which backbone
worked) rather than on the proposal's *constraints* (which backbone is
compatible with server-side stewardship).

### 5.3 CI was green because it tested nothing

`testpaths = ["tests"]` in the root `pyproject.toml`; the only test under
`tests/` is a placeholder. Every "CI green" through Month 1 was that
placeholder passing. The gateway's suite ran on developers' machines only.

**Fix:** PR #51 (open). Until it merges, "CI is green" should not be read as
a gate.

### 5.4 `main` was unprotected for the whole month

Direct pushes and force-pushes were possible until 20 Sep. This is why #43
could merge without every owner's sign-off, and why #18 could remove another
member's directory without a Code Owner review.

**Fixed:** 20 Sep, recorded in #49.

### 5.5 The M4 lane went quiet — and the record shows it exactly

I am writing this, so it is written plainly. Of thirteen M4 lines in the
schedule, three were delivered in the month, three partially, seven not.
The two pieces of real code (QR onboarding, the interview guide) were built
in cloud Claude Code sessions on the shared account, so they were committed
as "Claude" and the PRs were opened by `satsandesh-project` — no activity
appeared under any member's name, and the dependency map correctly reported
"zero commits, zero comments since 2 September". The ADR reversal that
needed four owners' sign-off got three. No elder has been interviewed.
SRS §3 is a placeholder.

None of this had a code dependency on anyone else (`OWNERSHIP.md` §, "M4:
blocked on nobody"). The causes were: docs-first work that did not feed
anything downstream soon enough to be missed; cloud sessions hiding the work
that was done; and no check-in when the interviews (an external-lead-time
item) did not get scheduled in Week 2.

**Fixed 22 Sep:** attribution (local sessions; re-authoring; PRs #53–#55
under `sainathanv`), the security checklist (#54), the journal convention
(#55), this retro. **Not fixed:** everything that needs an elder or the
organisation in the room — see §7.

### 5.6 Prompt journals were kept by two of four members, in two formats, in three locations

`CLAUDE/CLAUDE.md` said one place and one row; `docs/CONVENTIONS.md` said
another place; M2 and the gateway sessions wrote long-form entries wherever
they were working. M1 kept none in the repo. The AI-assisted-SE chapter of
the final report is drawn from these.

**Fixed:** convention + template in `docs/journal/` (#55). **Still open:**
M1's journal; whether M2 and M3 move theirs.

### 5.7 The shared account leaked identity — three different ways

- **Cloud sessions** (§5.5): commits authored "Claude", PRs opened by
  `satsandesh-project`. M4's code.
- **Local git config set to the shared email.** 40 of M3's commits are
  authored `Sandesh <projectsatsandesh@gmail.com>`; GitHub attributes every
  one of them to `satsandesh-project`, not to `Master-ff` (checked via the
  commits API on 22 Sep). The gateway — the largest single body of code in
  Month 1 — shows no contributions under its author's own account.
- **Merges by the shared login.** PRs #1, #2, #3, #11, #12, #17 and others
  were merged by the `Satsandesh` login rather than a named reviewer, so the
  merge record does not say who approved.

The third stops by construction now that branch protection requires a Code
Owner review. The first two need each member to check
`git config user.email` matches an email verified on *their* GitHub account:

```
git config user.name "<your name>"
git config user.email "<the email on your GitHub profile>"
```

### 5.8 Week 4's server-side accessibility work had no UI until Week 5

Undo, audio labels and quiet hours were delivered on the gateway in Week 4
(#13) and could not be reached from the elder app until #45 — a symptom of
the rotation model: the person who built the endpoint was not the person
who owned the screen, and nothing scheduled the join.

**Fixed by:** the Month-2 lane model. M1 owns the whole client slice.

## 6. What the team already changed because of this month

Recorded so the actions list below is only what remains.

| Change | Where | Date |
|---|---|---|
| Rotation → fixed vertical lanes with a versioned contract as the only boundary | Month 2–3 dependency map, rev. 4 | 18 Sep |
| Ownership rules + CODEOWNERS | `docs/OWNERSHIP.md`, `.github/CODEOWNERS` (#21) | 1 Sep |
| Branch protection on `main` | repo setting, logged in #49 | 20 Sep |
| ADR 0002 reversed to custom-lite on Postgres | #43 | 18 Sep |
| Matrix profile retired from the stack | #48 | 21 Sep |
| One canonical dev link | issue #44 | 9 Sep |
| Standalone security checklist, per-lane Week-12 passes | `docs/security-checklist.md` (#54) | 22 Sep |
| Prompt-journal convention and template | `docs/journal/` (#55) | 22 Sep |

## 7. Carry-ins into Month 2

Every Month-1 item still open on 22 Sep, with its owner and what it blocks.
The dependency map's Week-5 table is the source; PR numbers are the state
today.

| Item | Owner | State 22 Sep | Blocks |
|---|---|---|---|
| Elder interviews (6–8) | M4 | Guide in #53 (open). No sessions | SRS §3 (W5), pilot kit (W10), SUS instrument |
| SRS v1 — §3 functional reqs, §5 open questions | M4 | 823-byte scaffold | SRS v2 (W8), pilot kit (W10) |
| Taxonomy exemplars + org workshop | M4 | Scaffold only, exemplars empty | Classifier v1 (W6), eval harness (W8), red-team (W9) |
| Security checklist | M4 | #54 (open) | All four Week-12 passes |
| Prompt-journal template | M4 | #55 (open) | Week-12 AI-SE reflection |
| Written ADR 0002 position | M4 | Not on record | Nothing technical; closes the ADR's "not recorded either way" |
| Interview consolidation | M4 | Nothing to consolidate yet | SRS §3 |
| Ethics approval request for the pilot | M4 → supervisor | Not raised | Pilot (W11) — should have been raised in Week 5 per the map |
| CI actually running the gateway suite | M2 | #51 (open) | Every "CI green" claim |
| `LICENSE` | M2 | #47 (open) | Apache-2.0 release (W12) |
| Accessibility features surfaced in the UI | M1 | #45 (open) | Month-2 exit gate |
| Quiet hours / audio labels reachable in UI | M1 | in #45 | — |
| `CLAUDE/CLAUDE.md` not at repo root, so not auto-loaded; onboarding doc has two stale paths | M4 | Noted in #55's journal | Nothing; hygiene |

## 8. Month 2 backlog — Weeks 5–8, by lane

Condensed from the dependency map, rev. 4. "Depends on" is as the map states
it: *nobody*, a *contract* (soft — a mock stands in), or a *person and week*
(hard). Full task text is in the map; this is the checklist form.

### Week 5 — each lane stands up alone

| Lane | Task | Deliverable | Depends on |
|---|---|---|---|
| M1 | Accessibility pass pulled forward: quiet hours + audio labels in the UI (#45) | All three Month-1 accessibility features reachable | nobody |
| M2 | Repo hygiene: matrix profile off (#48 ✅), `LICENSE` (#47), branch protection (✅ 20 Sep), CI running gateway tests (#51) | Repo matches its decisions | nobody |
| M3 | ASR bring-up: faster-whisper small int8 on CPU; `POST /transcribe` behind `contracts/ai/transcribe.py`; mock updated (#52 open) | Transcript, honest latency, callable mock | nobody |
| M4 | Policy foundation: taxonomy workshop + exemplars; 6–8 interviews; SRS §3; security checklist (#54) | Four carry-ins cleared | nobody on the team; external lead time |

### Week 6 — second layer

| Lane | Task | Deliverable | Depends on |
|---|---|---|---|
| M1 | Voice capture: hold-to-record, upload with progress/retry | Elder records and sends a note | M2 media-store contract (soft) |
| M2 | Media store, 30-day retention, upload/download API, job queue with retries | Notes survive a saturated CPU | nobody |
| M3 | MT bring-up: IndicTrans2 distilled, Te/Hi → En pivot; `POST /translate` | Translation smoke test | bilingual reader (external) |
| M4 | Classifier v1: Qwen2.5 3B 4-bit, zero-shot on pivot text with W5 exemplars; graduated actions; `POST /moderate` | Pivot text in → label + reason | own W5 exemplars |

### Week 7 — third layer

| Lane | Task | Deliverable | Depends on |
|---|---|---|---|
| M1 | Receiver experience: text + audio, original one tap away, speed control, language pick | Language choice holds | M2 renderings contract (soft) |
| M2 | Orchestrator: denoise → transcribe → pivot → moderate → render per receiver | A note becomes N renderings | M3/M4 mocks (soft) |
| M3 | TTS bring-up: VITS + Piper; `POST /render`; latency instrumented | Synthesised audio plays | nobody |
| M4 | Moderator console v1: queue, side-by-side, release/block, append-only audit | A human can review every hold | own W6 classifier |

### Week 8 — integration Monday, then measurement

| Lane | Task | Deliverable | Depends on |
|---|---|---|---|
| All | **Monday:** swap every mock for the real service, run the full path once | — | each other (hard) |
| M1 | Client on the real stack; accessibility pass | Basics reachable | M2's real orchestrator, Monday |
| M2 | Full pipeline on staging, graceful degradation, real domain + HTTPS | Public staging URL | M3/M4 real services, Monday |
| M3 | Honest p90 for a 30-second note; tune int8/batching | A measured number | M2 routing traffic, Monday |
| M4 | Eval harness: 50-rendering adequacy sample per pair, false-hold tracking, ~300-message eval set scaffolded; SRS v2; Month-2 retro; Month-3 backlog | Baseline numbers on record | real renderings, Monday |

**Month-2 exit gate:** on staging, an onboarded elder records a Telugu voice
note; a Hindi-preference receiver gets translated text plus natural audio
with the original one tap away; the message passed a stewardship check whose
decision is visible in the moderator console's audit log; circles and
announcements run on the Postgres backbone; the measured p90 is written
down, whatever it is.

### Open PRs to land before Week 6

#45 (M1), #47, #49, #51 (M2), #52 (M3), #53, #54, #55 (M4).

## 9. Actions

Only what is still open after §6. Owner, then the week it is due.

| # | Action | Owner | Due |
|---|---|---|---|
| 1 | Schedule the 6–8 elder interviews; run the first two | M4 | W5 |
| 2 | Raise ethics approval for the pilot with the supervisor | M4 | W5 |
| 3 | Run the taxonomy workshop with the organisation liaison; fill the exemplars | M4 | W5 |
| 4 | Post a written ADR 0002 position on issue #35 | M4 | W5 |
| 5 | Fill SRS §3 from the first interviews; §5 from this retro's open questions | M4 | W5–W6 |
| 6 | Merge #51 so CI runs the gateway suite; then treat "CI green" as a gate | M2 | W5 |
| 7 | Merge #47 (`LICENSE`) | M2 | W5 |
| 8 | Merge #45 (accessibility surfaced in UI) | M1 | W5 |
| 9 | Create `docs/journal/kshitiz.md`; decide whether M2/M3 move theirs | M1 / M2 / M3 | W5 |
| 10 | Move `CLAUDE/CLAUDE.md` to the repo root; fix the two stale paths in `DEV_ONBOARDING.md` | M4 | W6 |
| 11 | Add a one-line note to the Month-1 schedule's README entry that its "roles set aside" line is superseded by the dependency map | M4 | W6 |
| 12 | Nobody starts a cloud Claude Code session on the shared account for repo work; local sessions or re-author before the PR (`docs/journal/README.md`) | all | now |

---

## Appendix — open questions for SRS §5

Collected from this month's threads so SRS §5 stops being a placeholder.

1. Do 1:1 chats permit category-C (personal) content? The proposal calls
   this "a policy knob the organisation sets"; nobody has asked them.
2. Which two or three pilot languages, exactly? Telugu and Hindi are assumed
   throughout; English pivot is fixed; the third is unstated.
3. Who are the two volunteer moderators and the policy liaison? Named
   people are needed before the Week-7 console has anyone to log in.
4. Does the campus-only staging host (`10.110.11.31`) become the pilot host,
   or does the pilot need a public domain by Week 8? The HTTPS requirement
   for microphone access forces this by Week 6–7.
5. What is the erasure-on-request procedure (DPDP §15) when a message has
   already been rendered into N receivers' languages?
6. Retention of *backups* versus the 30-day purge of originals — decided
   nowhere yet.
