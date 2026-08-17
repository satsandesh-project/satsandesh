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

---

## Repository layout

| Path | What it is |
|---|---|
| `services/gateway/` | FastAPI gateway — auth, routing, WebSocket fan-out |
| `services/ai/` | AI contract schemas and mock server (active from Month 2 — **do not archive**) |
| `contracts/ai/` | Shared request/response schemas between gateway and AI services |
| `docs/DECISIONS.md` | Architecture Decision Records — append only |
| `docs/CONVENTIONS.md` | Branching, PR and review rules |
| `docs/journal/` | Per-member prompt journals (one file each, to avoid merge conflicts) |
| `.github/workflows/` | CI: lint, format, test |

---

## Technology stack

All components carry an OSI-approved licence. Total software cost: ₹0.

| Layer | Choice | Licence |
|---|---|---|
| Client + admin console | Reflex (installable PWA) | Apache-2.0 |
| Gateway & AI services | FastAPI + Uvicorn | MIT / BSD |
| Chat backbone | **Undecided** — Matrix/Conduit vs custom-lite (FastAPI WS + Postgres outbox) | Apache-2.0 / — |
| Database | PostgreSQL | PostgreSQL Licence |
| Speech recognition | faster-whisper small (int8), CPU | MIT |
| Translation | IndicTrans2 distilled | MIT |
| Speech synthesis | AI4Bharat Indic-TTS (VITS); Piper for English | MIT |
| Moderation LLM | Qwen2.5 3B or 1.5B Instruct, 4-bit | Apache-2.0 |
| LLM serving | llama.cpp | MIT |
| Live rooms | LiveKit (self-hosted SFU) | Apache-2.0 |
| Broadcast | Nginx + HLS | BSD / MIT |
| Deployment | Docker Compose + Caddy + Let's Encrypt | Apache-2.0 |

Llama-family models are deliberately excluded — their community licence is not OSI open source.

---

## Getting started

Requires Python 3.11+, Docker Desktop, and git.

```bash
git clone <repo-url> SatSandesh
cd SatSandesh
```

Per-service setup and run instructions live in each service's own README:

- `services/gateway/README.md` — install, run, test, endpoint list, environment variables

A repo-wide `docker compose up` is a Month 1 deliverable and is not ready yet.

---

## Working agreement

Every member follows these, every week:

- **Tests first.** Write the test, watch it fail *for the right reason*, then implement.
- **AI writes code; humans own it.** Nothing merges unread. At review, the author explains their PR
  line by line. This is the pedagogy, not a formality.
- **Small, single-concern PRs.** Config changes commit separately from features.
- **Scope-lock your work** to your own service folder. Never touch a teammate's folder or `.git/`.
- **Verify in a real browser**, not only in `TestClient`. An in-process test client can pass while the
  real behaviour is broken.
- **Prompt journal entry per feature**, in your own file under `docs/journal/`.
- **Security checklist every sprint:** authorization on every route, no secrets in code, parameterized
  SQL, upload size limits, dependency audit.

Full conventions: `docs/CONVENTIONS.md`.

---

## Ethics and privacy

- **Informed consent at signup**, plainly worded, in the user's language, as text and audio: messages
  here are screened by a computer program and may be reviewed by a human, to keep this space for
  satsang.
- **No end-to-end encryption, stated openly.** Server-side stewardship and E2EE are mathematically
  incompatible. The promise is a gardened community space, not a private vault. Genuinely private
  conversations belong on other apps, and users are told so.
- **Data minimization.** Original voice recordings purge after 30 days (configurable). Analytics are
  aggregate-only. No advertising technology, no third-party trackers.
- **Moderation with dignity.** Never a silent deletion. Every action explained in the sender's
  language. Appeal available. Full audit trail.
- **Elder dignity.** No dark patterns, no streaks, no red-badge anxiety, no infinite feeds. Quiet
  hours on by default.
- **DPDP Act 2023 alignment.** Notice and consent, purpose limitation, named grievance contact,
  erasure on request.

---

## Open decisions

Tracked here until resolved in `docs/DECISIONS.md`:

- **Chat backbone: Matrix/Conduit (A) vs custom-lite (B).** Two spikes in Month 1 Week 2 decide it.
- **v1 shape.** Whether v1 is full chat, or the broadcast-first *Satsang Companion* scope
  (announcements, Thought for the Day, satsang broadcast, listen-together, Pranam Wall) with chat
  deferred to v2. The constraint stack above argues for the latter.
- **Compute for staging.** Department shared GPU access is being explored.
- **Dependency pinning.** `services/gateway/` uses exact pins + `requirements.txt`;
  `services/ai/` uses floating ranges + pyproject only. Should they converge?
- **Rate limiting on `/ws`** — required before any real deployment.

---

## Licence

Apache-2.0. Any community — ashram, temple, gurudwara, church, mosque — should be able to self-host
its own instance.

## Acknowledgements

Built on open work from AI4Bharat (IIT Madras), OpenAI Whisper, SYSTRAN faster-whisper, the Qwen
team, Matrix.org, LiveKit and Reflex.
