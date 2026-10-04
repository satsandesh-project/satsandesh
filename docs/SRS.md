# Software Requirements Specification (SRS) — v1

_Status: draft. §5 filled 2026-10-04 (M4). §3 still owed — it depends on the
6–8 elder contextual interviews, which are blocked on ethics approval (§5 Q5)._

## 1. Objectives

See project proposal, Section 4, for O1–O7.

## 2. Scope

See project proposal, Section 5 (in scope / out of scope / assumptions).

## 3. Functional requirements

_Still owed._ Deliberately not written from the proposal alone: the point
of this section is that each requirement carries the elder observation
that motivated it (proposal §11, "each design decision is recorded with
the elder observation that motivated it"). Writing it from the proposal
would produce a second copy of the proposal and lose exactly the evidence
that makes it worth having.

Blocked on the 6–8 contextual interviews, which are blocked on ethics
approval — §5 Q5. The instrument is ready
(`docs/research/elder-interview-guide.md`), as is the consent form
(`docs/research/interview-consent.md`); its Part 7 summary table is what
feeds this section.

## 4. Non-functional requirements

- Voice-note pipeline latency: < 10 s (p90) for a 30-second note.
- Elder usability: SUS ≥ 70; voice-message task success ≥ 90%.
- Reliability: ≥ 99% uptime across the pilot; zero data loss.
- Cost: ₹0 software cost, open source and self-hosted throughout.

## 5. Open questions

Each entry says who can answer it and what it blocks, so an unanswered
question is visible as a dependency rather than a note. **A question is
removed only when the answer lands somewhere durable** — a policy PR, an
ADR, a decision in `docs/`. Answering one in a thread and deleting the
entry is how the project lost its ADR 0002 record once already.

Status as of 2026-10-04 (Month 2, Week 7).

### Needs the organisation

**Q1. Do 1:1 chats permit category-C (personal) content?**
The proposal (§7.3) calls this "a policy knob the organisation sets", so
it is theirs, not ours. Default until they decide:
`docs/policy-taxonomy-workshop.md` knob 1, *circles only* — meaning a
category-C message in a DM is delivered, and in a circle is not.
*Blocks:* nothing today (the default is implemented), but the classifier's
false-hold rate is measured against whichever answer is live, so a late
change moves the Week-8 baseline.
*Answer lands in:* `docs/policy-taxonomy.md` § Policy settings.

**Q2. Which pilot languages, exactly?**
Telugu and Hindi are assumed throughout and the English pivot is fixed;
whether there is a third is unstated. `contracts/ai/language.py` is a
closed enum of `en`/`hi`/`te` and deliberately excludes Tamil and Kannada
until someone decides.
*Blocks:* TTS voice selection, the eval harness's language pairs, and the
pilot kit's printed guides (Week 10).

**Q3. Who are the two volunteer moderators and the policy liaison?**
Named people, not roles. The moderator console needs accounts that exist;
the liaison is the named DPDP grievance contact in the consent form.
*Blocks:* anyone actually logging into the console, and the consent form
cannot be finalised without the grievance contact's name.

**Q4. What goes in the taxonomy exemplars?**
The classifier runs on category descriptions alone until the workshop
happens (`/health/ready` reports `has_exemplars: false`). The candidate
list, boundary cases and policy knobs are ready in
`docs/policy-taxonomy-workshop.md`.
*Blocks:* the classifier being fit for the pilot at all; the Week-8
evaluation baseline; the Week-9 red team.

### Needs the supervisor

**Q5. Ethics approval — interviews now, pilot by Week 10.**
Raised 2026-09-22 (`docs/research/ethics-approval-request.md`), including
four questions needing a decision, of which the first sets the clock:
whether the interviews can begin under a lighter process while the full
pilot application runs in parallel.
*Blocks:* the 6–8 elder interviews, and therefore §3 of this document, the
Week-10 pilot kit and the SUS instrument.

**Q6. Who holds the participant code→identity mapping?**
It must live outside this repository, which goes public under Apache-2.0
in Week 12. Proposed: the supervisor.
*Blocks:* the first interview — a participant cannot be assigned a code
until someone owns the sheet.

### Needs a team decision

**Q7. Does the campus-only staging host become the pilot host?**
`http://10.110.11.31:8095` (issue #44) is reachable on the campus network
only and serves plain HTTP. Browsers refuse `getUserMedia` on a non-secure
origin, so voice capture cannot work on it, and three separate features
have now been worked around because of it (microphone, `getUserMedia`,
clipboard — PR #100).
*Blocks:* Week 8's "a staging URL someone outside the network can open",
and every further plain-HTTP workaround is interest paid on this.

**Q8. Erasure on request (DPDP §15) once a message has been rendered.**
A message that has been translated into N receivers' languages, delivered,
and recorded in the moderation audit log is not a single row. What is
deleted, what is pseudonymised, and what is retained — the audit trail is
append-only by DB trigger and should stay that way, so erasure there means
pseudonymising the actor, not deleting the event.
*Blocks:* nothing before the pilot, but it must be answered before real
elder data exists in Week 11, not after.

**Q9. Backup retention versus the 30-day purge of originals.**
Voice originals purge after 30 days (`MEDIA_RETENTION_DAYS`), but nothing
says how long backups keep them. A 90-day backup silently defeats a 30-day
purge.
*Blocks:* the Week-12 backup/restore drill signing off honestly.

**Q10. Does retention sweep media still under moderation?**
`find_expired_media` takes every row past the retention window with no
filter on moderation state, so a held or appealed message loses its audio
at 30 days — the same number as the proposed appeal window. Raised on
PR #71.
*Blocks:* nothing today (no held messages with real audio yet); becomes
real the moment the console is live on the pilot.

### Answered, kept for the record

**Q11. Chat backbone: Matrix or custom-lite?** → **Custom-lite on
Postgres.** ADR 0002, reversed 2026-09-08, merged in PR #43; the approval
record and its correction are in that ADR.

**Q12. Is a NUDGE delivered?** → **Not to a circle**; in a 1:1 it is, until
Q1 is answered. Documented on `ModerationAction` in
`contracts/chat/moderation.py` (PR #104) rather than only in the policy
file, which is what caused a wrong default once (issue #65).
