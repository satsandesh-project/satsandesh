# backbone/

**Owner:** Student 2 (Platform & backbone)

Chat backbone — message storage, delivery, and sync.

**Status: decided, then reversed.** ADR 0002
(`docs/adr/0002-chat-backbone.md`) originally accepted **Option A, Matrix
on Tuwunel**, and `spike-matrix-a/circle_service/` was live on that basis
for a period. On 2026-09-08 the decision was **reversed to Option B** —
see the ADR's "Reversal (2026-09-08)" section — and `services/gateway/`'s
own Postgres implementation is the backbone that ships. The `tuwunel` /
`matrix-circle-service` containers were retired from `docker-compose.yml`
on 2026-09-20. Neither spike here is wired behind the gateway any more:
`spike-matrix-a/` stays as the record ADR 0002 points to (and the
starting spec if Option A is ever revisited); `spike-custom-lite/` stays
as a tested record of the custom-lite option.

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
| `spike-matrix-a/circle_service/` | **Retired, not live.** Matrix/Tuwunel-backed. `MatrixCircleStore` in `matrix_circle_store.py`. Kept unchanged as ADR 0002's record; no longer in `docker-compose.yml`. |
| `spike-matrix-a/services/backbone-spike-a/` | The original Spike A material (Application Service mechanism proof) that ADR 0002's decision was based on — not the circles service itself. |
| `spike-custom-lite/` | The custom-lite spike. Not live; kept as a tested record of what was tried. Still boots via `docker compose --profile spike up` if anyone wants to run it again. |
