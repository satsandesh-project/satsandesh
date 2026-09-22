# Prompt Journal — Sainathan (M4, Stewardship, quality & pilot)

Convention and field definitions: `docs/journal/README.md`. Newest entry at
the bottom.

> **Backfill note (2026-09-22).** Until today this journal was one row:
> `16-08-2026 | Sainathan V | Create a README.md file for the project based on
> the project proposal doc`. Entries dated before 2026-09-22 were reconstructed
> from git history, PR reviews and commit messages, not from the prompts as
> typed — where a prompt is quoted it is the recorded one; otherwise it is a
> summary of what the commit shows was asked. Corrections for those entries
> are what the history shows was changed, not what I remember rejecting. From
> 2026-09-22 on, entries are written in the session.

---

## 2026-08-16 → 2026-09-01 — Project README, v1 to v4 (Week 1 · PRs #1, #2, #20)

**Asked for:** *(recorded)* "Create a README.md file for the project based on
the project proposal doc."

**Produced:** A 220-line README (`87293a2`, 17 Aug) covering description,
scope and ethics. Then three same-day revisions on 18 Aug: v2 cut it to ~100
lines (−120), v3 restored the cut (+120), v4 cut it again harder (−165). PR #1
(v3) and PR #2 (v4) merged 18 Aug. One more edit on 1 Sep removed the
supervisor row from the roles table (PR #20).

**Corrected:** The v2→v3→v4 swing is the visible record of not knowing how
long a README should be — Claude's first draft reproduced the proposal; the
team wanted an entry point, not a second proposal. Settled at the short
version. The README has since been substantially rewritten by M2 as the stack
changed, so little of v4's text survives.

**Decided differently:** Nothing recorded.

**Verified by:** Review on PRs #1/#2 (merged by the shared account).

**Where it ran:** local (commits authored as Sainathan V; PRs #1/#2 merged via
the shared `Satsandesh` login, #20 opened as `sainathanv`).

## 2026-08-18 → 2026-08-19 — Prompt journal v1/v2 (Week 1 · PR #3)

**Asked for:** A prompt-journal template for the team, per the Month-1
schedule.

**Produced:** A three-column table (`Date | Name | Prompt`) with one row, first
at repo root (`c5f63dc`), then moved into `Prompt Journal/` (`da8661e`, PR #3).

**Corrected:** Nobody used it — including me. M2 wrote ten long-form entries in
`docs/prompt-journal.md` with a note saying it "will likely need consolidating
with M4's later prompt-journal template"; the gateway sessions kept
`services/gateway/PROMPT_JOURNAL.md`; `docs/CONVENTIONS.md` (M1, 21 Aug)
specified a third location, `docs/journal/<your-name>.md`. A one-line table
cannot hold what the AI-assisted-SE chapter needs (what went wrong), so the
format was the problem, not the discipline. Replaced 2026-09-22 — see the
last entry.

**Decided differently:** —

**Verified by:** —

**Where it ran:** local.

## 2026-08-24 — Team CLAUDE.md conventions (Week 1 · PR #11)

**Asked for:** A `CLAUDE.md` the whole team's Claude Code sessions would follow:
ground rules (branch/PR discipline, tests first, stay in your folder, no
secrets), a short security checklist, rules for the shared Claude Pro
account, the journal convention, what to do when blocked.

**Produced:** `CLAUDE/CLAUDE.md`, 95 lines (`7c04b77`). Merged 24 Aug.

**Corrected:** Placed at `CLAUDE/CLAUDE.md`, not the repo root — Claude Code
auto-loads a root `CLAUDE.md` (and per-directory ones like
`clients/elder-app/CLAUDE.md`), so the team file is *not* actually read at
session start unless someone points at it. Not noticed until the 2026-09-22
audit. Follow-up: move to root in its own PR.

**Decided differently:** The five-bullet security checklist was written as a
section here rather than a standalone document, on the theory that a
checklist nobody sees is worse than a short one in the file Claude reads.
Given the file isn't auto-read either, that theory failed twice. Standalone
version: `docs/security-checklist.md` (PR #54).

**Verified by:** Review on PR #11.

**Where it ran:** local.

## 2026-08-24 — Developer onboarding guide (Week 1 · PR #12)

**Asked for:** A fresh-clone-to-first-PR guide for a new contributor:
prerequisites, setup, `.env`, repo structure, how to contribute.

**Produced:** `Dev_Onboarding/DEV_ONBOARDING.md`, 215 lines (`f84862b`).
Merged 24 Aug.

**Corrected:** Two references were already stale by Week 4: `cd
satsandesh-main` (the clone directory is `satsandesh`) and "see `CLAUDE.md` in
the repository root" (it's in `CLAUDE/`). Follow-up with the CLAUDE.md move.

**Decided differently:** —

**Verified by:** Review on PR #12. Not verified by actually following it on a
clean machine — that's the test that matters for an onboarding doc, and it's
still owed.

**Where it ran:** local.

## 2026-09-02 → 2026-09-05 — Family-assisted QR onboarding endpoints (Week 2 · PR #24 closed → PR #29 merged)

**Asked for:** The Month-1 Week-2 M4 task: auth + family-assisted QR
onboarding backend. A family member with a session creates an invite, gets a
QR, the elder scans it and receives a session.

**Produced:** First as PR #24 (2 Sep) against the then-existing `gateway/`.
That gateway was deleted the same week in favour of `services/gateway/`
(M3's), so PR #24 conflicted and was closed. Rebuilt 3 Sep as `a81436f`:
`POST /onboarding/invite` (signed, time-limited HMAC-SHA256 invite token over
`JWT_SECRET`), `GET /onboarding/qr/{token}` (PNG), `POST /onboarding/activate`
(single-use; inserts the `users` row); `qrcode[pil]` pinned; 11 tests on the
shared `client`/`login_as`/`db_session` fixtures. Merged as PR #29 on 5 Sep.

**Corrected:**
- `ruff` PLR0402 on the test import (`33a92b1`) — caught by CI, one-line fix.
- Review by Kshitiz on PR #29 found that invites lived in an in-memory dict,
  so a gateway restart silently dropped every pending invite — including
  ones whose QR an elder had already been shown. Veerendra fixed it
  (`83eb028`, 5 Sep): an `invites` table plus migration, single-use
  consumption as one `DELETE … RETURNING`, and two further bugs found only by
  running it against real Postgres (`ObjectDeletedError` under
  expire-on-commit; documented inline). The in-memory dict was Claude's
  "honest shortcut" with a comment — the comment did not make it acceptable
  for a flow whose whole point is that the QR is already in an elder's hands.

**Decided differently:** Kept the existing UUID-stub bearer auth
(`get_current_user`) rather than porting the deleted gateway's HMAC session
scheme — one auth implementation, even a stub, over two. Recorded in the
commit message.

**Verified by:** 11/11 `test_onboarding.py` locally at PR time; after the
Postgres fix, 189/189 gateway tests and a live run on the deployment server
(Veerendra's verification, not mine).

**Where it ran:** cloud. Commits are authored `Claude <noreply@anthropic.com>`
and both PRs were opened by `satsandesh-project`. This is why the dependency
map (rev. 4) records zero M4 activity — the work exists, the attribution
doesn't. See the 2026-09-22 attribution entry.

## 2026-09-07 — Elder interview guide (Weeks 2–3 · merged as PR #53 on 2026-09-22)

**Asked for:** A structured guide for the 6–8 elder contextual interviews:
profile, current phone-usage questionnaire, observed tasks with grading,
self-report, interviewer observations, and a summary table that feeds SRS §3.

**Produced:** `docs/research/elder-interview-guide.md`, 272 lines: 8 parts,
18 questionnaire items, 5 baseline + 5 SatSandesh-specific observed tasks on
a 0–4 scale, SRS-metrics table, and a reference section on observed elder
usage patterns with their SRS implications.

**Corrected:** Nothing in the content. The process: it sat on the
`claude/m4-tasks-month-1-kr0w7t` branch, unmerged and un-PR'd, for fifteen
days — and during those days the team's re-check found "no trace of
interviews in the repository", which was true.

**Decided differently:** —

**Verified by:** Not by use — no interview has been run with it yet. That is
the verification that counts and it is the Week-5 task.

**Where it ran:** cloud (authored as Claude). Re-authored and merged under my
name on 2026-09-22 — next entry.

## 2026-09-22 — Fixing attribution; interview guide PR #53 (Week 5)

**Asked for:** *(verbatim, abridged)* "It is mentioned that Member 4 has not
recorded any activity but all the activities he has done got committed via
the Sandesh GitHub account due to the link between Claude Code and the
SatSandesh GitHub account. How can I rewire this setup so that whatever code
and PRs Claude Code gives for Member 4 is reflected on my GitHub
contributions? Guide me step by step, and summarise the work assigned to M4."

**Produced:** A diagnosis from `git config`, the Windows credential store and
the GitHub commits API: local sessions already commit as
`Sainathan V <sainathan.sssihl@gmail.com>` and push as `sainathanv`, and
GitHub links that email to my profile — local was never the problem. Cloud
sessions commit as `Claude <noreply@anthropic.com>` and open PRs as whichever
GitHub account the shared Claude login is connected to (`satsandesh-project`).
Six-step fix: work in Local sessions; pin identity at repo level; if a cloud
branch exists, re-author before the PR:

```
git rebase origin/main --exec "git commit --amend --no-edit --reset-author"
git push --force-with-lease origin <branch>
```

Plus the M4 Week 5–12 task table from the dependency map.

**Corrected:** My Step-3 commands were given with a literal
`claude/<branch-name>` placeholder, and I ran them verbatim, so `checkout`
and `push` failed. Because the checkout failed I was still on `main`, so the
`rebase --exec` ran on `main` — which rebased away the merge commit and
re-authored the interview guide as me. That was the desired end state,
reached by accident; the next instruction should have named the branch.

**Decided differently:** Did not push `main`. Moved the re-authored commit
onto `m4/elder-interview-guide` and opened PR #53 from it, so `main` keeps
going through PRs even for a docs file.

**Verified by:** GitHub API on the pushed commit: `author.login: sainathanv`.
PR #53 opened by `sainathanv` (via the REST API with the stored credential —
`gh` is not installed on this machine).

**Where it ran:** local.

## 2026-09-22 — Month-1 leftover audit for M4 (Week 5 · no PR)

**Asked for:** "Check if anything is leftover in the part of Member 4 from the
Month 1 schedule." (`docs/SatSandesh_Month1_Schedule.docx`)

**Produced:** All 13 M4 lines from the schedule checked against `origin/main`
by file, size, author and date. Result: 3 delivered (CLAUDE.md, onboarding
doc, QR onboarding backend — the last misattributed), 3 partial (journal
template, security checklist, taxonomy scaffold — the scaffold is M2's), 7
undelivered (interviews ×2, ADR position, SRS v1, prototype — done by M1,
interview consolidation, retro + Month-2 backlog). Ordered by what each item
blocks.

**Corrected:** Nothing to correct in the audit; the finding is the
correction. Three items that could be done without external input were
picked to start with: security checklist, journal template, retro.

**Verified by:** Each row cites the file path, byte size and last commit.

**Where it ran:** local.

## 2026-09-22 — Standalone security checklist (Week 5 · PR #54)

**Asked for:** "Start with the security checklist."

**Produced:** `docs/security-checklist.md` — Part A per-PR gate (the original
five bullets plus role-escalation, bounded inputs/fan-out, no real elder data
in prompts, second reviewer on auth code); Part B per-lane Week-12 passes
(B1 platform, B2 identity & sessions, B3 elder client, B4 speech AI, B5
stewardship & pilot) with every item naming the file that answers it and the
checks still open on 22 Sep recorded inline; Part C CI tooling (`pip-audit`,
`gitleaks`, `bandit` — CI runs only ruff + pytest today); Part D sign-off
table. `CLAUDE/CLAUDE.md` security section now points at it.

**Corrected:** Two claims were checked against the code before committing and
changed: (1) the invite-token comparison was written as "verify it is
constant-time" — it already uses `hmac.compare_digest`, so the item now says
so; (2) "check the `require_role` on `/onboarding/invite`" — there isn't one,
the route uses `get_current_user` only, so any authenticated account can
issue invites; recorded as an open item rather than a check.

**Decided differently:** Opening the PR by script (REST API + stored
credential, same as PR #53) was refused twice by the desktop app's auto-mode
classifier as "excess sensitive detail". Rather than work around it, I
opened the PR by hand — and took the point: the first PR body enumerated the
live unauthenticated surfaces of a staging host in a public repo. Final body
says "open items are recorded as unticked checks in the owning lane" and
leaves the detail inside the document.

**Verified by:** Every Part-B item that names a file was read on `main` on
22 Sep (Caddyfile, `docker-compose.yml`, `.env.example`, `app/auth.py`,
`app/onboarding.py`, `app/ws.py`, `app/config.py`, `ai-services/main.py`,
`.github/workflows/ci.yml`, `.gitignore`).

**Where it ran:** local.

## 2026-09-22 — Prompt-journal convention, template and this backfill (Week 5 · PR pending)

**Asked for:** "Move on to the prompt-journal template."

**Produced:** `docs/journal/README.md` (why, where, when, the seven fields,
three rules, a note on cloud-session attribution), `docs/journal/_template.md`,
and this file — `Prompt Journal/Prompt_Journal.md` moved here with `git mv`
and backfilled from history. `CLAUDE/CLAUDE.md`'s "Log the work" bullet and
"Prompt journal" section repointed at `docs/journal/`. `docs/CONVENTIONS.md`
already said `docs/journal/<your-name>.md`, so no change there.

**Corrected:** The template is not the table I wrote in August. The fields
were taken from what M2 and the gateway sessions were already writing
(`Date / Prompt (summarized) / Verification`, `Asked for / Produced / What
was wrong / Decided differently`) — the convention should describe the
practice that exists, not the one nobody followed.

**Decided differently:** Did not move `docs/prompt-journal.md` (M2) or
`services/gateway/PROMPT_JOURNAL.md` (M3) into this folder. `README.md` and
the ADR link to the former; both are other members' files. The index in
`README.md` links to them where they are.

**Verified by:** Not applicable — docs. The real test is whether the next
three PRs from other members carry an entry.

**Where it ran:** local.
