# Ethics approval request — SatSandesh elder interviews and pilot

**From:** M4 Sainathan V, on behalf of the SatSandesh team (M1 Kshitiz, M2 Veerendra, M3 Sandesh, M4 Sainathan)
**To:** Prof. Korra Sathya Babu (supervisor)
**Date:** 2026-09-22
**Status:** Draft for the supervisor — this file is the on-record copy; the request itself is sent by email.

The proposal (§15, "Research ethics") commits that "the pilot runs under
institute ethics approval; interview and usage data are anonymized in any
publication." The Month 2–3 plan (dependency map rev. 4, Week 10) notes this
"should have been raised back in Week 5". This is that request, raised in
Week 5. It asks for two things, because they have different clocks.

---

## What we are asking for

1. **Now — clearance to begin the 6–8 elder contextual interviews** (Weeks
   5–6). Observation and questionnaire only; no software is installed on
   the elder's phone; no message content is collected. We would like to
   start these as soon as the consent form below is approved, under
   whatever lighter-touch process the institute allows for
   observation-and-interview studies, if one exists.
2. **By Week 10 — approval for the elder pilot** (Week 11, Month 3): 15–30
   elders using the SatSandesh app for at least three weeks, including two
   live sessions, with usage metrics, an SUS questionnaire and observation
   visits.

And a decision from you on one point (§"Questions", below).

## 1. The study, in one paragraph

SatSandesh is an invitation-only, elder-first messaging app for a
devotional community, in which every message passes through a server-side
values-alignment check with human moderator oversight, and voice notes are
translated between Indian languages. The research questions are (a) whether
elders aged 60+ on low-cost Android phones can use a voice-first messaging
app unaided after family-assisted onboarding (target: SUS ≥ 70, voice-note
task success ≥ 90%), and (b) whether a zero-training moderation pipeline on
an English pivot holds up under real community traffic with a tracked
false-hold rate. The proposal (Section 15) states the ethics posture in
full; this request applies it to the two activities that involve people.

## 2. Participants

| | Interviews (Weeks 5–6) | Pilot (Week 11+) |
|---|---|---|
| Who | 6–8 elders, 60+, members of the partner organisation | 15–30 elders, 60+, same organisation; family members who assist onboarding; two volunteer moderators; two bilingual volunteers rating translation quality |
| Recruited by | The organisation's policy liaison, from members who already use WhatsApp | Same, with family-assisted onboarding |
| Where | The elder's home or a familiar quiet space at the organisation | Their own phones, at home; two live sessions at the organisation |
| Duration | One session, 30–45 minutes | Three weeks or more of ordinary use; two observation visits |
| Inclusion | Owns or has access to an Android phone; can hear and speak in Telugu, Hindi or English | Same, plus completed onboarding |
| Exclusion | Anyone who cannot give informed consent themselves | Same |

Family members may be present at interviews at the elder's choice; they are
asked to stay silent during tasks so observations reflect the elder's own
ability.

## 3. What is collected, and what is not

### Interviews
- Interviewer's handwritten or typed notes on a structured guide
  (`docs/research/elder-interview-guide.md`): profile fields (age band,
  languages, education level, phone type), questionnaire answers, task
  completion grades (0–4), timing, observations.
- **Audio recording of the session only with a separate, explicit tick** on
  the consent form; used only to check notes; deleted within 30 days.
- **Not collected:** name on any research record (a participant code is
  used — see §5), phone number, photographs of the person, any content from
  the elder's own phone (we watch them use WhatsApp; we do not read or copy
  their messages), health information.

### Pilot
- **Usage metrics, aggregate only:** messages per day, voice-note task
  success, time-to-first-message after onboarding, session attendance,
  measured latency. No per-person analytics are published.
- **SUS questionnaire** (10 items, read aloud in the elder's language if
  preferred), keyed to participant code.
- **Observation notes** during the two live sessions and visits.
- **Message content, as the product itself handles it.** This is the
  point that needs the most care and is stated plainly: SatSandesh screens
  every message server-side and community moderators can read messages
  that the screen holds for review. Participants consent to this as users
  of the app (§4), in their own language, as text and audio. The research
  use of message content is limited to one thing: a **hand-labelled
  evaluation set of about 300 messages**, drawn from pilot traffic, used to
  *measure* the moderation classifier's accuracy and false-hold rate — never
  to train it. That set is stored with sender identity removed.
- **Voice recordings:** original audio is kept for 30 days for playback
  and translation, then purged automatically (proposal §15, "data
  minimisation"). The evaluation set uses transcribed text, not audio.
- **Not collected:** end-to-end-encrypted private conversations — there
  are none, and participants are told this (§4); location; contacts;
  anything from other apps on the phone.

## 4. Consent

- **Interviews:** `docs/research/interview-consent.md` — plain-language,
  read aloud in the elder's language, with an audio version, a separate
  tick for audio recording, and a witnessed verbal-consent option for
  elders who prefer not to sign. Family member may co-sign as witness, not
  in place of the elder.
- **Pilot / app use:** the in-app consent at signup, text and audio, in the
  user's language, using the proposal's wording verbatim:

  > "Messages here are screened by a computer program and may be read by
  > community moderators, to keep this space for satsang. Please do not
  > share private or medical matters here."

  plus the statement that there is no end-to-end encryption and why
  (server-side stewardship is incompatible with it), and that truly
  private conversations should stay on the apps they already use. A
  printed large-type copy and the audio version are part of the pilot kit
  (Week 10). No pre-ticked boxes; a family member may help the elder
  through the screen but the elder accepts.
- **Withdrawal:** at any time, by telling the liaison, a moderator, or any
  team member, in person or by voice note — no form. On withdrawal the
  account is disabled, their messages and renderings are deleted, and any
  of their messages in the evaluation set are removed. Audit-log rows are
  retained but pseudonymised (a moderator's decision record cannot be
  silently deleted; the identity behind it can be).

## 5. Anonymisation and data handling

- Every participant gets a code (`E01`…) at first contact. The code→name
  mapping is a single sheet held by the supervisor, **outside the
  repository and outside the app**. Research notes, SUS responses and the
  evaluation set carry only codes.
- The repository is public (Apache-2.0 release, Week 12). **Nothing
  identifying is committed** — not names, not phone numbers, not audio, not
  the mapping sheet, not raw notes. Consolidated interview findings go into
  the SRS as patterns ("5 of 7 hesitated at…"), never as quotes tied to a
  person.
- The team works on a shared AI coding assistant account; the team's
  written rule (`CLAUDE/CLAUDE.md`) is that no elder or pilot data is ever
  pasted into a prompt.
- Backups are encrypted; the app is self-hosted on the organisation's
  infrastructure; no third-party analytics or advertising technology.
- Retention: voice originals 30 days; interview audio (if recorded) 30
  days; research notes and the evaluation set until the final report is
  accepted, then the mapping sheet is destroyed and the data is
  unlinkable.
- Grievance contact (DPDP Act 2023): the organisation's policy liaison,
  named on the consent form and in the app.

## 6. Risks and how they are handled

| Risk | Mitigation |
|---|---|
| Elder feels tested or embarrassed during tasks | Tasks are framed as testing the app, not the person; "we stop whenever you like" is said at the start and repeated; family member may be present |
| An elder shares something private or medical in the app despite the notice | Category-C content is nudged privately to the sender and not delivered to circles; moderators are bound by a written code of conduct; no message is deleted silently, every action is explained, appeal exists |
| Over-blocking causes distress ("my message disappeared") | Never silent: every hold or block sends a kindly-worded notice in the sender's language with the reason; false-hold rate is a tracked metric and treated as a defect |
| Re-identification from published findings | Codes only; findings reported as patterns; small-cohort figures reported as counts, not with demographic cross-tabs |
| Data breach | Self-hosted, encrypted backups, 30-day audio purge, no identifying data in the public repo, security checklist run on every lane before the pilot (`docs/security-checklist.md`) |
| Elder cannot read the consent form | Read aloud and available as audio in Telugu/Hindi/English; witnessed verbal consent accepted |
| Dependence — elder comes to rely on the app and the pilot ends | Pilot elders are told the app continues after the study if the organisation chooses to keep it; if it is withdrawn they are told two weeks in advance |

## 7. Timeline

| When | What |
|---|---|
| Week 5–6 (now) | Interviews, on approval of the consent form |
| Week 8 | SRS v2 incorporating interview findings; evaluation harness scaffolded (no pilot data yet) |
| Week 10 | Pilot kit (consent, guides, SUS instrument) finalised; **pilot approval needed by end of this week** |
| Week 11 | Pilot begins: onboarding, two live sessions, observation |
| Week 12 | Final report; anonymised findings; public release |

## 8. Questions for you

1. Does the institute have a lighter process for observation-and-interview
   studies that would let the interviews start this week, with the full
   pilot application running in parallel?
2. Is witnessed verbal consent (with the witness signing) acceptable in
   place of the elder's signature, for elders who prefer it?
3. Are you willing to hold the code→identity mapping sheet, or should the
   organisation's liaison hold it?
4. Anything the institute's process needs that is missing here — we will
   add it before the pilot application.

Attachments: `docs/research/interview-consent.md`,
`docs/research/elder-interview-guide.md`, proposal Section 15.
