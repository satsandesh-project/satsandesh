# Content taxonomy workshop — pack for the organisation

**Owner:** M4 Sainathan
**Status:** v1 draft, 2026-09-22. Prepared for the workshop; nothing in here
is policy until the organisation has edited it and the result is merged
into `docs/policy-taxonomy.md` through a PR the liaison approves.
**Who needs to be in the room:** the organisation's policy liaison (must),
the two volunteer moderators if already nominated (should), the supervisor
(optional), M4 and one other team member to take notes.
**Length:** 90 minutes. Agenda in §1.

## Why the organisation, and why now

The proposal (§7.3) says the taxonomy is "co-designed with the organisation
in Month 1 and versioned in the repository like code", and that the
classifier's prompt contains "a dozen organisation-authored examples" per
category — "prompt engineering, not training". Nobody on the team can
author those examples: they encode what *this* community considers
devotional, organisational, personal, disputational, or harmful. The team
can draft candidates; the organisation says yes, no, or "actually it's
this".

The classifier (Week 6) cannot be built without the exemplars, the
moderator console (Week 7) cannot show notices the organisation has not
approved, and the red-team (Week 9) has nothing to test against. That is
why this is a Week-5 task.

Everything the organisation decides here becomes `policy@<date>` — the
`policy_version` stamped on every moderation decision the system makes
(`contracts/ai/moderation.py`). Changing policy later is a pull request
with the liaison's approval, not a conversation.

---

## 1. Agenda (90 minutes)

| Min | Item | Output |
|---|---|---|
| 0–10 | What the system does with a message, in plain words (§2). Confirm the five categories and their actions. | Categories confirmed or amended |
| 10–40 | Walk the candidate exemplars (§3), category by category. For each: keep, edit, drop, or move. Add the organisation's own. | ≥ 12 agreed exemplars per category |
| 40–60 | Boundary cases (§4): the messages that could go either way. These decide the false-hold rate. | A decision on each |
| 60–80 | Policy knobs (§5): the questions only the organisation can answer. | A decision on each, or a named person and date |
| 80–90 | Sender notices (§6): the words an elder sees when a message is nudged, held or blocked. | Wording approved, or edits captured |

Note-taker records every decision in the tables of §7. M4 turns §7 into
the PR against `docs/policy-taxonomy.md` within two days; the liaison
approves the PR; that merge is the policy.

## 2. What the system does — the two-minute version for the room

Every message (a voice note, after it has been turned into text; or a typed
message) is read by a computer program before it is delivered. The program
puts it into one of five categories and takes the matching action:

| | Category | What happens | Who sees it |
|---|---|---|---|
| **A** | Devotional / positive | Delivered | Everyone it was sent to |
| **B** | Organisational / informational — schedules, seva, announcements | Delivered | Everyone it was sent to |
| **C** | Personal / off-topic — commerce, gossip, private matters | **Not delivered to circles.** The sender gets a private, kindly-worded note saying why, and can rephrase or share it privately. | Only the sender |
| **D** | Argumentative — doctrinal quarrels, criticism of members | **Held.** A human moderator reads it and decides: release or hold. Nothing is deleted. The sender is told it is waiting. | Sender, moderators |
| **E** | Harmful / abusive / unsafe | **Blocked.** A moderator is alerted. The sender is told, with the reason, and can appeal. | Sender, moderators |

Three things that are true regardless of category:

- **Nothing is deleted silently.** Every action is explained to the sender
  in their own language, and every action can be appealed to a human.
- **When the program is unsure, it holds for a human** — it never blocks
  on a guess.
- **Moderators can read messages that are held or blocked.** Elders are
  told this at signup, in text and audio, in their language. That is why
  the app is for satsang, not for private matters.

The organisation's job today is to say what A–E *mean here*, by example.

## 3. Candidate exemplars — for the organisation to keep, edit, drop or move

All invented. Written as the English text the program actually reads (voice
notes are transcribed and translated into English first), so some read
like translations — that is deliberate. Aim for at least twelve kept per
category. The organisation's own additions are worth more than any of
these.

### A — Devotional / positive-constructive → allow

| # | Candidate | Keep / edit / drop / move |
|---|---|---|
| A1 | Om Sai Ram. May everyone have a peaceful morning. | |
| A2 | Today's thought: whatever work we do, let us offer it to Him and do it with love. | |
| A3 | The bhajan yesterday was so beautiful, I was in tears. Thank you to everyone who sang. | |
| A4 | Please pray for strength for all of us during this festival season. | |
| A5 | I read chapter 12 of the Gita again this morning. Every time it says something new to me. | |
| A6 | Swami's words on patience have helped me a lot this week. Sharing for anyone who needs them. | |
| A7 | Namaskaram to all elders in the circle. It is a blessing to be able to hear your voices. | |
| A8 | I am grateful for this circle. On days I cannot come to the mandir, this keeps me connected. | |
| A9 | Let us all try to sit in silence for ten minutes this evening at seven, wherever we are. | |
| A10 | The children sang the Ganesha bhajan at home today. Recording it in my heart, not on the phone. | |
| A11 | Happy Guru Purnima to all. Let us remember our teachers with gratitude. | |
| A12 | Even when my knees hurt I will keep coming for seva. It is what gives me strength. | |
| A13 | Wonderful satsang today. The point about surrender stayed with me. | |
| A14 | May all beings be happy. Sarve jana sukhino bhavantu. | |

### B — Organisational / informational → allow

| # | Candidate | Keep / edit / drop / move |
|---|---|---|
| B1 | Bhajans this Thursday at 6 pm in the main hall. Please arrive by 5:45 to sit comfortably. | |
| B2 | Seva team for Sunday's Narayana seva: we need six volunteers for serving. Please reply here. | |
| B3 | The satsang on Saturday will be broadcast on the app. You can listen from home. | |
| B4 | Reminder: the medical camp is on the 14th, 9 am to 1 pm, at the community hall. Bring your old prescriptions. | |
| B5 | Next week's Thought for the Day readings will be led by the ladies' wing. | |
| B6 | Transport: the van leaves from the bus stand at 5 pm sharp for the evening programme. | |
| B7 | The library will be closed for cleaning on Monday. Books can be returned Tuesday. | |
| B8 | For those attending the retreat, the packing list is on the notice board and in this circle. | |
| B9 | Please keep phones on silent inside the prayer hall. Thank you for your cooperation. | |
| B10 | Cooking seva starts at 4 am on festival day. Volunteers please confirm by Wednesday. | |
| B11 | Change of plan: Thursday's bhajans are moved to Friday because of the power shutdown. | |
| B12 | The organisation's annual day is on the 2nd. Elders who need a chair, tell the volunteers at the gate. | |
| B13 | The audio of last week's discourse is now available in the app under Sessions. | |

### C — Personal / off-topic → private nudge, not delivered to circles

| # | Candidate | Keep / edit / drop / move |
|---|---|---|
| C1 | I am selling my old scooter, good condition, 25,000. Anyone interested, message me. | |
| C2 | Did you hear that Lakshmi's son is getting divorced? Such a shame for the family. | |
| C3 | Join my chit fund, 20 members, 5,000 per month, very safe, I will explain. | |
| C4 | Anyone know a good plumber near the temple road? Mine has stopped answering. | |
| C5 | My daughter has a job opening in her company for accountants, send CVs to me. | |
| C6 | Forward this to ten people and Swami will bless you. Do not break the chain. | |
| C7 | Watch this movie, it was released last week, very good songs. | |
| C8 | I heard the treasurer bought a new car. Where did that money come from, I wonder. | |
| C9 | Selling homemade pickles, 200 per jar, mango and lemon. Order before Sunday. | |
| C10 | Good morning everyone. *[a forwarded picture of flowers with a generic quote]* | |
| C11 | Which bank gives the best fixed deposit rate now? My son says try the new one. | |
| C12 | My blood pressure was very high yesterday and the doctor changed my tablets to a higher dose. | |
| C13 | Please vote for my nephew's photo in this online contest, link below. | |

### D — Argumentative / disputational → held for human review

| # | Candidate | Keep / edit / drop / move |
|---|---|---|
| D1 | The way the bhajans are conducted now is completely wrong. The old committee did it properly. | |
| D2 | Some people in this circle only come for the free lunch and never do any seva. We all know who. | |
| D3 | Our tradition is the only true one. Those who follow the other path are simply misguided. | |
| D4 | Why was Ramesh made coordinator? He does not even know the bhajans. This is favouritism. | |
| D5 | I disagree strongly with what the speaker said on Saturday. He is misinterpreting the scripture and misleading everyone. | |
| D6 | If the committee does not fix the sound system I will stop coming and I will tell others too. | |
| D7 | Women should not be leading the bhajans. It was never like this before. | |
| D8 | Whoever left the hall dirty yesterday should be ashamed. This is not how we treat Swami's house. | |
| D9 | That is not what the Gita says. You are quoting it wrongly and confusing the elders. | |
| D10 | The new timing is stupid and nobody asked us. Who decided this? | |
| D11 | I am not saying names, but certain families think they own this mandir. | |
| D12 | Politics has entered our organisation. The elections next month will show who is honest. | |

### E — Harmful / abusive / unsafe → blocked, moderator alerted

| # | Candidate | Keep / edit / drop / move |
|---|---|---|
| E1 | You are a useless old fool and everyone laughs at you behind your back. | |
| E2 | People of that caste should not be allowed inside the hall. | |
| E3 | Your bank account has been blocked. Call this number immediately and share your OTP to unblock it. | |
| E4 | I know where you live. Stop talking about me or you will regret it. | |
| E5 | Send 2,000 to this number now, the temple urgently needs it — *[from an unknown sender, not the office]* | |
| E6 | I do not want to live any more. There is no point in anything. | |
| E7 | *[sexual content directed at a member]* | |
| E8 | Those people are the reason for all our problems. They should be driven out of this town. | |
| E9 | This herbal powder cures diabetes in 30 days, stop your medicines and buy from me. | |
| E10 | Your grandson has had an accident, send money to this account for the hospital. *[scam pattern]* | |
| E11 | I will make sure that woman is thrown out of the committee if it is the last thing I do. | |
| E12 | *[a photo of a member taken without their knowledge, with a mocking caption]* | |

Note on E6: this is not "abusive", it is a person in distress. It is in E
because it must reach a human immediately, not because the sender has done
anything wrong. **The organisation should decide who that human is** (§5,
knob 9) and the sender notice for this case must be written separately
(§6).

## 4. Boundary cases — these decide the false-hold rate

The proposal calls over-blocking "a first-class defect". These are the
messages a well-meaning program will get wrong. For each, the organisation
decides the category; the decision goes into the exemplar list *as the
example that teaches the program the difference*.

| # | Message | Could be | The question |
|---|---|---|---|
| X1 | Please pray for my husband, he is in hospital after a heart attack. | A or C | Prayer requests mention illness. Is a prayer request devotional (A), or does "do not share medical matters" (C) apply? |
| X2 | I want to kill my ego completely. Swami, help me destroy this pride. | A or E | Devotional idiom with violent words. Must be A. Include so the program learns it. |
| X3 | I disagree with the speaker's interpretation of that verse — I always understood it as being about detachment, not renunciation. | A or D | Respectful doctrinal disagreement. Is discussion of scripture welcome (A) or is any disagreement D? |
| X4 | The sound was too low again on Saturday, many elders at the back could not hear. Can something be done? | B or D | Feedback to organisers. Complaint or contribution? |
| X5 | Our seva group is selling handmade diyas, all proceeds go to the annadanam fund. 50 rupees each. | B or C | Commerce, but for the organisation. Who may sell, and for what? |
| X6 | Happy birthday Kamala amma! 80 years of seva. May you have many more. | A or C | Personal celebration in a circle. Welcome, or off-topic? |
| X7 | I am feeling very alone since my wife passed. Coming to bhajans is the only thing that helps. | A or C or E | Grief shared in a devotional frame. Almost certainly A — but should a volunteer be quietly told? |
| X8 | Please do not forward messages from outside into this circle, we have had wrong information spread before. | B or D | A member policing others. Organisational (B) or criticism (D)? |
| X9 | The Prime Minister spoke about our tradition in his speech yesterday, sharing the clip. | B or C or D | Politics-adjacent. Where is the line? |
| X10 | Swami said devotees should not waste money on flowers, give it to the poor instead. So why does the committee spend so much on decoration? | D | Scripture used to criticise. D — but a respectful version of X4. Confirm. |
| X11 | I think Rama is the greatest, others say Krishna. What do you all think? | A or D | Playful comparison. Harmless, or an invitation to a quarrel? |
| X12 | Whoever is spreading rumours about the accounts should come and see the books themselves, they are open every Sunday. | B or D | Defensive but informative. |
| X13 | Om Namah Shivaya ×108 *[a message that is only a mantra repeated]* | A | Confirm A, and whether repeated mantra-only messages need any limit. |
| X14 | Can someone drop me to the hospital on Tuesday for my check-up? I cannot manage the bus any more. | B or C | Practical help request between members. This is what community is for — or off-topic? |
| X15 | I recorded yesterday's discourse on my phone, sending it here for those who missed it. | B or C | Member-shared media versus the curated library. Allowed? |

## 5. Policy knobs — questions only the organisation can answer

| # | Question | Options | Default if not decided today |
|---|---|---|---|
| 1 | Do **1:1 chats** allow category C (personal) content, or does C apply only to circles? | Circles only / everywhere / everywhere except family circles | Circles only (proposal §7.3: "a policy knob the organisation sets") |
| 2 | **Prayer requests that mention illness** (X1, X7) | A always / A but flag to a volunteer / C | A, no flag |
| 3 | **Commerce for the organisation** (X5) — who may sell what, in which circles? | Office only / seva groups with liaison approval / anyone for org causes | Office only |
| 4 | **Member-shared media** (X15) — recordings, photos, forwards | Curated library only (proposal) / members may share audio of org events / open | Curated library only |
| 5 | **Political content** (X9) — where is the line? | Block all mention of parties and elections / allow news, block opinion / case by case | Hold (D) for a human |
| 6 | **Respectful doctrinal discussion** (X3, X11) | Welcome (A) / hold (D) / welcome in designated circles only | A if respectful, D if it names a person |
| 7 | **Feedback to organisers** (X4, X12) | B / D / B in a designated "suggestions" circle, D elsewhere | B |
| 8 | **Nudge before hold?** Should a D message first get a private "please rephrase" nudge, and only be held if re-sent unchanged? | Yes / no | No — hold immediately, moderator decides |
| 9 | **A person in distress** (E6, X7) — who is told, and how fast? | Moderators only / a named volunteer / the elder's chosen contact from the well-being check-in | Moderators, with a named volunteer to escalate to — **name the person today** |
| 10 | **Hold SLA** — how long may a message wait for a human before the sender is told "still waiting"? | 1 h / 4 h / 24 h | 4 h; sender notified at 24 h |
| 11 | **Appeal window** — how long after a block can the sender appeal? | 7 days / 30 days / no limit | 30 days |
| 12 | **Who are the two volunteer moderators**, and have they read the moderator code of conduct? | Names | **Name them today** — Week 7's console needs someone to log in |
| 13 | **Repeat behaviour** — what happens after the third E in a month? | Nothing automatic, moderators decide / temporary mute / removal from circles by the office | Moderators decide |
| 14 | **Pilot languages** — Telugu, Hindi, English are assumed. Is there a third Indian language in the community? | | Te / Hi / En |
| 15 | **Grievance contact** (DPDP) — the named person elders can go to. | Name and phone | The policy liaison |

## 6. Sender notices — the words an elder actually sees

Drafted in English; the organisation edits the tone. Final versions are
translated into Telugu and Hindi by a bilingual reader and recorded as
audio, because the elder may not read. Every notice states the reason and
what the sender can do. None of them say "deleted".

**C — nudge (private, not delivered to the circle):**
> This looks like a personal or business matter. SatSandesh circles are kept
> for satsang, so this was not sent to the group. You can rephrase it, or
> share it with the person directly.

**D — held for a human:**
> Your message is waiting for a volunteer to read it before it goes to the
> group, because it may be about a disagreement. Nothing has been deleted.
> You will be told when it is sent or if there is a question.

**D — released after review:**
> Your message has been sent to the group. Thank you for your patience.

**D — not released after review:**
> A volunteer read your message and felt it might start an argument in the
> group, so it was not sent. You can rephrase it, or reply to this note if
> you would like to talk to the volunteer.

**E — blocked:**
> This message was not sent because it may be hurtful or unsafe. A volunteer
> has been informed. If you think this is a mistake, reply to this note and
> a person will look at it.

**E6 — distress (this one is not a "block" notice; it is an outreach):**
> Thank you for telling us. Someone from the community will call you today.
> You are not alone.

*(Requires knob 9 to be decided; the promise "will call you today" must be
true before this text ships.)*

**Any category — still waiting after the hold SLA:**
> Your message is still waiting for a volunteer. We are sorry for the delay.
> Nothing has been deleted.

## 7. Decision record — filled in during the workshop

Copy the rows the organisation decided. This section becomes the PR.

**Exemplars kept per category** (write the final numbers; attach edited
lists):

| A | B | C | D | E |
|---|---|---|---|---|
| | | | | |

**Boundary decisions:**

| Case | Decided category | Note |
|---|---|---|
| X1 | | |
| … | | |

**Knobs:**

| # | Decision | Decided by | Or: owner and date |
|---|---|---|---|
| 1 | | | |
| … | | | |

**Notices:** ☐ approved as drafted  ☐ edits captured (attach)

**Policy version to stamp:** `policy@YYYY-MM-DD`
**Liaison sign-off on the resulting PR:** name, date

## 8. After the workshop — what M4 does with this

1. Within two days: PR against `docs/policy-taxonomy.md` — exemplars
   section filled with the *agreed* lists (organisation-authored, edited),
   boundary decisions added as exemplars in their decided category, knobs
   recorded as a "Policy settings" section, notices as a "Sender notices"
   section, changelog line `policy@<date>: first organisation-approved
   taxonomy`. Liaison approves the PR.
2. Same week: `contracts/ai/moderation.py` `policy_version` default and
   the mock updated to `policy@<date>`; fixtures in
   `services/ai/tests/fixtures/` extended with one request/decision pair
   per category from the agreed exemplars.
3. Week 6: classifier v1 prompt built from the agreed exemplars — no other
   examples.
4. Week 9: red-team round 1 tries to get C/D/E past the filter and A/B
   held by it; every devotional idiom that trips the filter goes back into
   the exemplar list via the same PR route, with the liaison's approval.
