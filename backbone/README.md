# backbone/

**Owner:** Student 2 (Platform & backbone)

Chat backbone — message storage, delivery, and sync.

**Status: decided.** ADR 0002 (`docs/adr/0002-chat-backbone.md`) is
Accepted — **Option A, Matrix on Tuwunel** (not Conduit, not conduwuit).
The circles feature is live on `spike-matrix-a/circle_service/`.
`spike-custom-lite/` remains in the repo as a tested record of the
custom-lite option that was seriously evaluated, but it is no longer
wired up behind the gateway.

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
| `spike-matrix-a/circle_service/` | **Live.** Matrix/Tuwunel-backed. `MatrixCircleStore` in `matrix_circle_store.py`. |
| `spike-matrix-a/services/backbone-spike-a/` | The original Spike A material (Application Service mechanism proof) that ADR 0002's decision was based on — not the circles service itself. |
| `spike-custom-lite/` | The custom-lite spike. Not live; kept as a tested record of what was tried. Still boots via `docker compose --profile spike up` if anyone wants to run it again. |
