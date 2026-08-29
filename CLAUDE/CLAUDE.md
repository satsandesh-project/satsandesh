# CLAUDE.md

This file is read automatically by Claude Code at the start of every session in this
repo. It exists so that all four of us get consistent behavior from Claude Code,
regardless of who is driving. Full conventions live in `docs/CONVENTIONS.md` — this
file is the condensed version Claude Code should actually follow while coding.

## Project in one paragraph

SatSandesh is an elder-first, multilingual, moderated messaging platform for
devotional communities — WhatsApp-like, but with content stewardship, a voice
translation bridge (ASR → MT → TTS), satsang/bhajan sessions, and an elder-first UI.
Stack: Python end-to-end — Reflex (frontend), FastAPI (gateway + AI services),
PostgreSQL, Docker Compose deployment. See `README.md` for the full picture.

## Ground rules for Claude Code in this repo

- **Never push to `main` directly.** Always work on a branch and open a PR, even for
  trivial changes. If asked to "just fix this quickly," still branch first.
- **Branch naming:** `<type>/<short-description>` — types are `feat`, `fix`, `chore`,
  `docs`, `refactor`. Example: `feat/gateway-auth`.
- **Small, single-concern commits and PRs.** Don't bundle unrelated changes. If a task
  turns out to need two unrelated changes, stop and say so — don't silently combine
  them into one PR.
- **Tests first.** When asked to implement a feature, write or update the test(s)
  before the implementation, unless explicitly told otherwise.
- **Stay inside the current member's service folder** unless the task explicitly says
  otherwise. If a change needs to touch a shared file (`contracts/`, root config,
  another member's folder), stop and flag it rather than editing it directly.
- **Explain, don't just output.** Every non-trivial change should come with a short
  plain-English explanation of what changed and why — the PR author needs to be able
  to explain it line by line in review. Nothing merges unread.
- **No secrets in code, ever.** No API keys, DB passwords, or tokens hard-coded
  anywhere — use `.env` / environment variables, and never commit `.env` itself
  (only `.env.example` with placeholder values).
- **Lint/format before finishing:** code should pass `ruff check .` and
  `ruff format --check .` (config in `pyproject.toml`). Run these before declaring a
  task done.
- **Log the work.** After any notable feature or fix, add a one-line entry to
  `Prompt Journal/Prompt_Journal.md` (date, name, prompt/task) — see template below.

## Security checklist (apply on every route/endpoint touched)

- Authorization check present on every route — no endpoint trusts the caller by default.
- No secrets, tokens, or credentials in source, logs, or commit history.
- All SQL is parameterized — never string-formatted or concatenated queries.
- File/media uploads have an explicit size limit and type check.
- New dependencies are checked for known vulnerabilities before adding
  (`pip-audit` or equivalent) and pinned in `pyproject.toml`.

## Working with a shared Claude Pro account

We're running Claude Code off one shared Claude Pro login rather than separate seats.
This has real implications — Claude Code (the tool) should be aware of these when
giving guidance, and every member should follow them:

- **Coordinate before starting a session.** Post in the team channel when you start
  and finish a Claude Code session so two people aren't burning the same shared
  usage window simultaneously without knowing it.
- **Don't leave long-running or idle sessions open.** Shared Pro usage limits refill
  on a schedule — an idle session someone forgot to close costs the next person
  their turn. Close out when you're done, not just when you step away.
- **Never paste real secrets, user data, or elder pilot data into a Claude Code
  prompt** — even though it's "our own" account, prompts may be logged, and this
  project explicitly promises data minimization to elders. Use placeholder values
  when asking Claude Code for help involving credentials or real records.
- **Don't change the account's settings** (model defaults, memory, connected tools)
  without agreement — a change one member makes for their own workflow affects
  everyone's sessions.
- **If you hit a usage limit mid-task,** don't switch to pasting code into a personal
  Claude account to "get around it" — pause, log where you stopped in the prompt
  journal, and hand off or wait. Keeps the prompt-journal trail (and the eventual
  AI-assisted-SE report) honest and complete.
- **Each person's prompt journal entries should be attributable to them** even
  though the account is shared — always fill in your real name in the journal, not
  "the team."

## Prompt journal

Location: `Prompt Journal/Prompt_Journal.md`. One row per notable prompt/task:

| Date | Name | Prompt |
|---|---|---|
| DD-MM-YYYY | Your name | Short description of what you asked Claude Code to do |

Add an entry any time Claude Code produces a non-trivial chunk of work (a feature, a
fix that took real back-and-forth, an architecture decision). Trivial formatting
fixes don't need an entry.

## When something is genuinely blocked

If a task depends on another member's unfinished work (e.g., a route needs a DB
table another member owns, or a spike result isn't in yet), Claude Code should say
so plainly rather than inventing a placeholder and moving on silently. Stub it
clearly (e.g., `# TODO: blocked on M3 users table, see issue #x`) and stop.
