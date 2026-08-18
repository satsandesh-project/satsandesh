# SatSandesh

**A moderated, multilingual, elder-first messaging platform for devotional communities.**

Open source, self-hosted, built as seva. No advertising, no engagement traps, no data sold.

> **Status: early development.** Month 1 of a 3-month build. Nothing here is usable yet.
> Working title — alternatives under consideration: Satsang Setu, Prema Vaani, Seva Sandesh.

---

## What this is

India's devotional communities live on general-purpose WhatsApp groups, where satsang content is
diluted by forwards, commerce, arguments and misinformation. Elders in particular find the noise
stressful and the interface unforgiving.

SatSandesh is a private, invitation-only communication app that differs by design in four ways:

1. **Content stewardship** — every message passes a values-aligned screening step, so the space stays
   devotional and free of disputes, commercial chatter and explicit material.
2. **A language bridge** — voice notes spoken in one Indian language are delivered to each receiver as
   text *and* natural speech in that receiver's own chosen language.
3. **Satsang and bhajan sessions** — one-to-many broadcast, plus bhajan rooms with a single-lead
   "floor" so participants never talk over one another.
4. **Elder-first experience** — voice-driven, large targets, forgiving, honest about ₹7,000 Android
   phones and rural bandwidth.

The guiding engineering principle is **assemble, don't rebuild**: mature open source supplies message
delivery, live audio, speech recognition, translation and synthesis. Team effort goes into the
differentiating parts — the elder experience, the language bridge, stewardship and the satsang
experience.

---

## Current constraints (read before planning anything)

This repository is being built under tighter constraints than the original proposal assumed. Where
the two disagree, **this section wins**.

| Item | Proposal assumed | Reality |
|---|---|---|
| Timeline | 8 months, 16 fortnightly sprints | **3 months** |
| Partner organization | Named liaison + 2 volunteer moderators + pilot recruitment | **None.** Built independently as seva |
| GPU | 16 GB RTX-class server | **RTX 2050, 4 GB** (laptop-class) |
| Team | Four students, one supervisor | Four members, **basic programming experience**, rotating shares |
| Hyperlink sharing | In scope | **Deferred out of v1** (see below) |

Consequences that follow directly from the above:

- **The 7B moderation model does not fit.** Plan around a 3B or 1.5B instruct model at 4-bit
  (~1–2 GB). Smaller models are weaker at nuanced judgement, so the design leans on
  sender-side warnings rather than autonomous model decisions wherever possible.
- **The speech stack runs on CPU.** faster-whisper small (int8), IndicTrans2 distilled and VITS TTS
  are all CPU-viable. Slower, but workable.
- **Latency target is 20–30 s, not under 10 s**, for a 30-second voice note. Voice notes are
  asynchronous by nature; this is an acceptable trade.
- **A laptop GPU is not a server.** It throttles under sustained load and cannot be the always-on
  staging host. Staging needs a separate box; department shared compute is being explored.
- **No partner org means no ready-made pilot.** Recruiting ~15 real elders (family, local bhajan
  groups, community contacts) is the single most important non-code milestone.

---

## Scope

**In scope for v1:** Android-first installable web app (PWA); 3 languages at launch (Telugu, Hindi,
English); organization-managed registration, no public sign-up; voice notes, text, and media from a
curated approved library; pilot scale of a few dozen users.

**Out of scope for v1:** end-to-end encryption (a deliberate, disclosed decision — see *Ethics*);
native iOS app; video calling; personal photo and video sharing; **hyperlink sharing**.

**Why links are excluded from v1:** the stewardship pipeline reads text. It cannot see what is inside
a shared YouTube or WhatsApp link. One shared URL makes the "clean space" promise only as good as
whatever sits behind it. Links return in a later version behind an allowlist or admin curation —
that hole is far easier to close before a pilot than after one.

---

## How stewardship works

The model is a **parcel stamp, not an archive**. A message is read in transit, judged, and passed on.
Content that passes is never registered anywhere beyond normal message storage.

- **Explicit / vulgar / abusive** → blocked, with a clear notice to the sender stating why and how to
  request a review. Never a silent deletion.
- **Argumentative or disputable** → the *sender* is warned before it sends ("this reads like a
  complaint about someone — send anyway, or rephrase?"). The sender decides. No deletion.
- **Personal / off-topic** → a private, kindly-worded nudge, in the sender's own language.
- **Everything else** → passes.

Two properties this design must preserve:

1. **Appeals need something to appeal to.** Zero retention makes review impossible and the false-positive
   rate unmeasurable. The intended resolution is a short **quarantine for blocked messages only**
   (e.g. 72 hours, visible only to whoever handles appeals, auto-purged). Passing messages are never
   quarantined.
2. **Over-blocking is a first-class defect.** An elder's message about a sick spouse vanishing without
   explanation is worse than a dispute getting through. False-hold rate is a tracked metric, not an
   afterthought.