# Prompt journals

**Owner of the convention:** M4 Sainathan (Stewardship, quality & pilot)
**Status:** v1, 2026-09-22 — Month-1 Week-1 deliverable ("prompt-journal
template"), delivered late. Consolidates three conventions that had drifted
apart: the one-row table in `CLAUDE/CLAUDE.md`, the
`docs/journal/<your-name>.md` rule in `docs/CONVENTIONS.md`, and the
free-form entries M2 and the gateway sessions were actually writing.

## Why this exists

The proposal commits to "shared prompt journals" as a mitigation for
*AI-generated code hides security flaws* and *nothing merges unread*, and the
final report (Week 12) has a chapter on AI-assisted software engineering
drawn from them. That chapter is only as honest as the journals — so the
point of an entry is **what Claude got wrong and what you decided
differently**, not a log of what it produced. An entry that says "asked for
X, got X, merged" is fine but adds nothing to the report; the corrections do.

## Where

One file per member, first name, lower case:

| Member | File | Notes |
|---|---|---|
| M1 Kshitiz | `docs/journal/kshitiz.md` | not yet created |
| M2 Veerendra | `docs/prompt-journal.md` | 10 long-form entries, Weeks 1–4. Stays where it is — `README.md` and the ADR link to it. Moving it is M2's call. |
| M3 Sandesh | `services/gateway/PROMPT_JOURNAL.md` | Phase-by-phase entries for the gateway sessions. Same — stays put unless M3 moves it. |
| M4 Sainathan | `docs/journal/sainathan.md` | Moved here from `Prompt Journal/Prompt_Journal.md` and backfilled. |

New journals go in this folder. Existing ones are linked from here rather
than moved, so nobody's history gets rewritten by someone else's PR.

## When to write an entry

- Every PR, at the latest when you open it. `docs/CONVENTIONS.md` already
  lists this under "Before every PR".
- Any architecture or policy decision Claude Code was involved in, merged or
  not — a rejected suggestion is often the most useful entry.
- Any session that ended blocked or at a usage limit (`CLAUDE/CLAUDE.md`,
  "Working with a shared Claude Pro account"): record where you stopped so
  the next person, or you tomorrow, can pick it up.
- Not for: formatting-only fixes, typo commits, `ruff` nudges.

## Format

Copy `docs/journal/_template.md`. One `##` heading per entry, newest at the
bottom. The fields:

| Field | What goes there |
|---|---|
| **Heading** | `## YYYY-MM-DD — <task in a few words> (Week N · PR #x)` — PR omitted if there isn't one. |
| **Asked for** | The prompt, summarised in your own words. Verbatim is fine for short prompts. If the work took several prompts, the arc of them. |
| **Produced** | What Claude Code delivered — files, tests, the shape of the change. Numbers where they exist (tests added, lines, time). |
| **Corrected** | What was wrong or what you rejected, and how you found out (a test, a review comment, running it, reading it). *This is the field the report is built from.* Write "nothing" honestly if that's true. |
| **Decided differently** | Where you deliberately departed from what was asked or suggested, and why. Scope you cut, a shortcut you took and flagged, a suggestion you declined. |
| **Verified by** | How you know it works: test count, a live run, a teammate's review, a screenshot. "Not verified" is an acceptable answer that the reviewer needs to see. |
| **Where it ran** | `local` (your machine, your git identity) or `cloud` (claude.ai/code / Cloud toggle — commits come out authored as "Claude", see below). |

Optional: **Time** (wall-clock, honestly), **Follow-ups** (issues opened).

## Three rules

1. **Your real name, every entry.** The Claude account is shared; the journal
   is how work stays attributable to a person.
2. **No secrets, no real elder data, no pilot transcripts** — same rule as
   prompts themselves. Describe the data, don't paste it.
3. **Record mistakes deliberately, not smoothed over.** Both existing
   journals say this in their own words. The report needs the mistakes.

## A note on cloud sessions and attribution

Work done in a cloud session (claude.ai/code, or the desktop app with
**Cloud** selected) is committed as `Claude <noreply@anthropic.com>` and the
PR is opened by whichever GitHub account the Claude login is connected to —
on the shared account that is `satsandesh-project`, not you. That is how M4's
Month-1 code (PR #24 → #29) and the interview guide came to show no activity
under any member's name. Either work in **Local** sessions, or re-author the
branch before opening the PR (see `docs/journal/sainathan.md`, entry
2026-09-22, for the exact commands). Either way, say which in **Where it ran**.
