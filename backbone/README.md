# backbone/

**Owner:** Student 2 (Platform & backbone)

Chat backbone — message storage, delivery, and sync.

**Status: superseded.** ADR 0002 (`docs/adr/0002-chat-backbone.md`)
originally accepted Option A, Matrix on Tuwunel, and `spike-matrix-a/circle_service/`
was genuinely live on that basis for a period. The team has since
superseded that decision — see the ADR's "Update (2026-09-05)" section —
and `services/gateway/`'s own Postgres implementation is the one going
forward. Neither backbone here is wired behind the shipping gateway:
`spike-matrix-a/` is kept as the archived record of how Option A was
built (see its own README), and `spike-custom-lite/` remains as a tested
record of the custom-lite option that was seriously evaluated.

## `interfaces.py` — the contract

`interfaces.py` defines `CircleBackbone`: what any backbone must provide
for circles and announcements. It is stdlib-only and depends on no
concrete implementation.

The gateway depends on this file and nothing else in here — verified by
an AST-based check (not a plain grep, which false-positives on this
repo's own docstring prose), not just by eye. That boundary is exactly
why ADR 0002 landing on Week 4, three weeks after circles started, cost a
service swap (one env var, one new implementation) rather than a gateway
rewrite.

| | |
|---|---|
| `interfaces.py` | The contract. |
| `spike-matrix-a/circle_service/` | **Archived, not live.** Matrix/Tuwunel-backed. `MatrixCircleStore` in `matrix_circle_store.py`. See `spike-matrix-a/README.md`. |
| `spike-custom-lite/` | The custom-lite spike. Not live; kept as a tested record of what was tried. Still boots via `docker compose --profile spike up` if anyone wants to run it again. |

The original Spike A material (Application-Service mechanism proof,
separate from the circles service itself) lives at the repo root,
`services/backbone-spike-a/` — findings in `docs/SPIKE_A_FINDINGS.md`.
