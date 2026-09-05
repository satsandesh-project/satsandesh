# backbone/spike-matrix-a/ — archived, superseded

**Status: archived.** This directory holds Spike A's real, working Matrix
(Tuwunel) Application-Service implementation — `circle_service/`, a
FastAPI service backing `backbone/interfaces.py`'s `CircleBackbone`
against a real Tuwunel homeserver. It's genuine, tested, credited work
(PR #15), not a stub.

It is kept for the historical record, not because it's wired into
anything. ADR 0002 originally decided Option A (Matrix/Tuwunel) on the
strength of these findings — but the team has since **superseded** that
decision: see `docs/adr/0002-chat-backbone.md`'s "Update (2026-09-05) —
decision superseded" section. `services/gateway/`'s existing Postgres
implementation is the one going forward. Nothing in the shipping app
imports or runs anything in `circle_service/` anymore, and the `tuwunel`
/ `matrix-circle-service` entries that used to run it have been removed
from `docker-compose.yml`.

The full findings — including the real Conduit join-bug reproduction,
the E2EE/application-service incompatibility, and the shared-machine
rootless-Docker setup notes — live at
[`docs/SPIKE_A_FINDINGS.md`](../../docs/SPIKE_A_FINDINGS.md), the
canonical, complete copy. A second, older, incomplete copy of that same
document (and a duplicate nested copy of `services/backbone-spike-a/`,
the standalone Application-Service bot Spike A also produced) used to
live inside this directory — both removed as redundant; `services/backbone-spike-a/`
at the repo root remains, unduplicated, as the real bot spike.

If Matrix integration is ever revisited, start by reading ADR 0002's
"Update" section for the specific schema-mismatch and delivery-path
findings that made the cutover real, multi-day work, not a quick swap.
