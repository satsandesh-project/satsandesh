# Repo-wide notes

## Two workstreams, one person, two project documents

This repository intentionally contains two separate workstreams, both belonging
to the same person (M3), built under two different planning documents:

- **`services/ai/`** (+ `contracts/ai/`) — built under the original project
  proposal's **Student 3: Speech & Language AI** role (ASR, MT, TTS, moderation,
  GPU serving). Under the Month 1 schedule, this work is scheduled for **Month 2**,
  not Week 1 — it was built ahead of that schedule. It is a complete, tested
  deliverable (contracts, mock server, golden fixtures, docs), not abandoned or
  misplaced work from a different teammate.
- **`services/gateway/`** — M3's actual **Month 1, Week 1** task under the
  compressed schedule: the FastAPI gateway skeleton. This is the active
  workstream going forward.

Recorded here so that neither folder is later mistaken for orphaned or
stray work — including by M3 in a future session, which is exactly the
confusion this note exists to prevent.

**Future shape:** `services/gateway/` is expected to eventually proxy to
`services/ai/`, using the Pydantic contracts defined in `contracts/ai/`. See
[`services/ai/README.md`](../services/ai/README.md) for the contract API
reference and [`services/ai/DECISIONS.md`](../services/ai/DECISIONS.md) for the
design rationale behind those contracts specifically.

## Dependency pinning diverges between the two services — kept deliberately

`services/gateway/` pins exact versions in `pyproject.toml` and additionally
keeps a fully-pinned `requirements.txt` (direct + transitive) as a lock file.
`services/ai/` uses floating lower-bound ranges (`fastapi>=0.110`) and has no
`requirements.txt` at all. This is a real inconsistency between the two
services, kept intentionally rather than reconciled:

- The gateway is going to be containerized by a teammate, and Dockerfiles
  install from `requirements.txt` — that needs to be a reproducible, fully
  resolved set of versions, not a range pip re-resolves at image-build time.
- The gateway depends on `python-jose[cryptography]`, a security-sensitive
  package where a floating range is a real risk (a transitive resolution
  change landing in a container build without anyone reviewing it). `services/ai/`
  has no comparably sensitive dependency today.

**Open question for the team, not a decision made here:** should `services/ai/`
converge on the same exact-pin + `requirements.txt` approach once it's also
containerized, for consistency across services? Not changed as part of this
work — `services/ai/`'s `pyproject.toml` and lack of `requirements.txt` are
untouched.
