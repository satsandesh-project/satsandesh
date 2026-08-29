# Spike A: Application Service Mechanism — Findings

**Date:** 2026-08-21
**Scope:** Prove (or disprove) that a Matrix Application Service (AS) — a bot a
homeserver automatically forwards events to — actually works on Conduit, since
the moderation-bot architecture in the proposal depends on this mechanism.

## What was tested

1. A minimal FastAPI bot ([`app/bot.py`](../services/backbone-spike-a/app/bot.py))
   implementing the AS HTTP contract: `PUT /_matrix/app/v1/transactions/{txnId}`,
   `GET /_matrix/app/v1/users/{userId}`, `GET /_matrix/app/v1/rooms/{roomAlias}`,
   plus a debug `GET /received` to inspect what arrived.
2. Unit tests ([`tests/test_bot.py`](../services/backbone-spike-a/tests/test_bot.py))
   covering token auth (accept/reject), transaction storage, and namespace
   query responses — all in-process, no live Conduit needed. **4/4 passing.**
3. A real Conduit container, a real AS registration
   (`services/backbone-spike-a/registration.yaml`, real tokens generated
   locally into a gitignored `.env`), registered via Conduit's actual
   mechanism, and a real end-to-end test: human account invites the bot's
   user into a room, sends a message, check whether it reaches the bot.

## What worked

- **The core transaction-push mechanism works.** Conduit forwarded events to
  the bot's `/_matrix/app/v1/transactions/{txnId}` endpoint correctly, with
  the right `Authorization: Bearer <hs_token>` header, and the bot's 200
  response was accepted. This is the fundamental thing the architecture
  depends on, and it works.
- Confirmed via `/received` (captured before an in-process restart cleared
  the in-memory store):
  ```json
  [
    {
      "content": {"is_direct": false, "membership": "invite"},
      "event_id": "$KbbRLQr77LHQcAQExg-jS2PQoQaqyvx_Q2dKt-Xdg_o",
      "room_id": "!oivW_p4HOjIkdjIPaW-LoUSdHnttE8XfzieLLAZB0PE",
      "sender": "@shyam:localhost",
      "state_key": "@satsandesh_bot:localhost",
      "type": "m.room.member",
      "unsigned": {}
    },
    {
      "content": {"is_direct": true, "membership": "invite"},
      "event_id": "$aiLKHXbdxHIwvlwGONHDc2w0UurOd00hndLwtzuErkY",
      "room_id": "!HK4TPs-zOyq4LmFhbjKJCqw6Z4YYYSI8Zf6yRtsKPB8",
      "sender": "@shyam:localhost",
      "state_key": "@satsandesh_bot:localhost",
      "type": "m.room.member",
      "unsigned": {}
    }
  ]
  ```
- The bot's server log confirms both transactions returned `200 OK` in under
  a second, with the correct `access_token`/Bearer auth applied.
- The AS registration mechanism itself works as documented: Conduit takes a
  registration via a chat command (`register-appservice`) in its
  auto-created `#admins` room — not a config file, not a CLI flag, not an
  admin HTTP API. See "harder/easier than expected" below.

## What didn't work

**Full message interception is blocked** — not by our bot or registration,
but by what looks like a genuine bug/limitation in this Conduit build:

1. The bot's own virtual user (`@satsandesh_bot:localhost`, the AS's
   `sender_localpart`) is **not auto-provisioned** on invite. It had to be
   explicitly registered via the AS-specific `m.login.application_service`
   registration flow before it could do anything as itself. Not
   necessarily a bug — arguably spec-correct — but not something the
   proposal's architecture discussion anticipated as a setup step.
2. Once registered, **every attempt for the bot to join the room it was
   invited to failed** with `500: "No server available to assist in
   joining."` Conduit's own logs show why: it routes *every* room join
   through a federation-assisted join path — `Joining ... over
   federation.` — even for a room that is 100% local to this same server,
   with every member on `localhost`. That path has no remote server to
   consult, so it always fails.
   - Tried: both join endpoints (`/rooms/{roomId}/join` and
     `/join/{roomIdOrAlias}`), with and without an explicit `server_name`
     query hint, with federation both disabled and enabled in
     `conduit.toml`. Same failure every time.
   - Conclusion: this is very likely a real bug in this specific
     (archived, unmaintained) Conduit build's local-join handling, not a
     mistake in our registration or bot code.
3. Practical effect: since the AS's bot user never actually **joins** the
   room (only ever sits in `invite` state), Conduit only forwards the
   `m.room.member` invite event itself — not the subsequent
   `m.room.message` events sent afterward. Per the Matrix AS spec, message
   forwarding requires the AS's user to be **joined**, not merely invited.

**Bonus finding, unrelated to the join bug:** room IDs delivered in the AS
transaction payload omit the server-name suffix (e.g. `!oivW_p4Hoj...`
instead of the spec-correct `!oivW_p4Hoj...:localhost`), while Conduit's own
internal logs and the Client-Server join API both expect/use the full
`!id:localhost` form. Another small but concrete data point on this build's
spec fidelity.

## Time / effort

Rough breakdown, for comparing against M2's Spike B next week:

- **Environment recreation (unplanned):** the Conduit container and its
  config directory from the earlier verified setup no longer existed on
  this machine, and neither Docker nor Python were on `PATH` in the shell
  environment used. Finding real binary paths, recreating the container,
  and re-registering test users ate a meaningful chunk of the session —
  this wasn't part of the spike's actual scope, just environment drift.
- **Bot + tests (the actual spike work):** small and fast. A 4-endpoint
  FastAPI app plus its test suite came together in well under an hour,
  including a round of research into Conduit's actual AS registration
  mechanism (config vs. CLI vs. admin API vs. chat command — it's the
  chat command, confirmed via docs before writing anything).
- **Debugging the join failure:** the largest single chunk of investigation
  time. Trying endpoint variants, checking both federation settings, and
  reading Conduit's server logs to pin down "it's routing local joins
  through federation" took real effort and would have taken a lot longer
  without direct log access.
- **Overall:** roughly half the time went to environment/infra friction
  unrelated to the core question, not to the AS mechanism itself.

## The archived-Conduit fact, and what it implies

The original Conduit project is **archived by its maintainers**. It still
runs fine — today's test proves that — but the join bug found in Step 3 is
exactly the kind of thing that would never get fixed upstream: there's no
one left to file it with. Maintained forks exist (conduwuit/Tuwunel,
Continuity) that plausibly fix exactly this kind of bug.

**If the team picks Option A (Conduit-based AS architecture) for real:
switch to a maintained fork before building anything production-facing.**
This isn't a hypothetical risk — it's the literal bug that blocked full
verification of this spike. We did not test whether a fork fixes it (that
would be a good, cheap follow-up before committing to Option A), but
building the moderation-bot architecture on software that can't receive a
bug fix for its core join mechanism is a real risk, not a compliance
checkbox.

## Harder / easier than the proposal assumed

- **Harder:** the proposal describes the AS mechanism as "a bot that
  homeservers can automatically forward events to," which is accurate for
  the transaction-push half, but glosses over the fact that *receiving
  messages* (not just invite notifications) requires the bot to actively
  **join** every room it's meant to moderate — an extra step with its own
  failure mode, as we found.
- **Harder:** setup mechanics for Conduit specifically — no config file or
  CLI for AS registration, only a chat command. Not documented anywhere
  obvious; took targeted research before writing any code, exactly the
  order of operations Step 0 asked for.
  **Easier:** everything the bot itself needed to do — HTTP token auth,
  transaction storage, namespace query responses — is a genuinely small,
  well-specified contract. The FastAPI implementation and its test suite
  were straightforward once the endpoints' expected behavior was clear.

## Recommendation (not a decision — that's Week 3's ADR)

The AS mechanism is real and does what the proposal needs: a homeserver
will push room events to an external bot with no polling required, and the
push contract is small enough to implement and test cleanly. That part of
Option A checks out.

What I'd flag before committing to it: (1) don't build on archived Conduit
for anything beyond another spike — the join bug found here is a concrete
reason, not a theoretical one; (2) budget real time for the "bot must join
every room it moderates" step specifically, since it has its own
auth/provisioning/failure surface the proposal doesn't call out; (3) a
cheap next step, if the team wants more confidence before the ADR, is
re-running this exact Step 3 against conduwuit to see if the join bug is
Conduit-specific — I didn't do that here since we'd agreed to keep this
spike scoped to a single environment, but it's a small follow-up.

---

## Follow-up investigation — room ID fix + fork retest (2026-08-21)

*Appended after the original findings above, which are unchanged. This
follow-up tests a specific theory raised after the fact: that the two
findings above — the missing server-name suffix on room IDs in transaction
payloads, and the join failure — were actually one bug, not two.*

### Theory

`_join_room_as_bot` in `app/bot.py` passed `event["room_id"]` straight from
the transaction payload into the join URL, unqualified. If Conduit's join
handler doesn't recognize an unqualified ID as local, that alone could
explain the "over federation" routing and the resulting failure.

### Test 1: does a fully-qualified room ID fix the join?

**Result: no.** This was re-confirmed twice with direct evidence from the
bot's own code path (not manual reproduction):

1. Added `_fully_qualified_room_id()` to `app/bot.py`, which appends the
   homeserver's own domain (read from `BOT_USER_ID`, not hardcoded) when a
   room ID lacks one. Added two unit tests
   (`test_fully_qualified_room_id_appends_domain_when_missing`,
   `test_fully_qualified_room_id_leaves_already_qualified_id_untouched`) —
   both pass.
2. Fed the bot's real `/_matrix/app/v1/transactions/{txnId}` endpoint the
   *exact original, unqualified* invite event from the Step 3 run, through
   its actual production code path (HTTP parse → qualify → join call), not
   a manual curl reproduction of the join alone.
3. The bot's own log shows the fix worked correctly — it built the
   properly-qualified URL:
   ```
   httpx.HTTPStatusError: Server error '500 Internal Server Error' for url
   'http://localhost:6167/_matrix/client/v3/rooms/!oivW_p4HOjIkdjIPaW-LoUSdHnttE8XfzieLLAZB0PE:localhost/join'
   ```
4. Conduit's log for that exact request, domain suffix and all:
   ```
   join_room_by_id{sender_user="@satsandesh_bot:localhost"
     room_id="!oivW_p4HOjIkdjIPaW-LoUSdHnttE8XfzieLLAZB0PE:localhost"}:
     Joining !oivW_p4HOjIkdjIPaW-LoUSdHnttE8XfzieLLAZB0PE:localhost over federation.
   Returning an error: 500 Internal Server Error: No server available to assist in joining.
   ```
5. Repeated with the second known invited room after adding a robustness
   fix (below) — identical result, both bot-side and Conduit-side:
   ```
   # bot.log
   failed to join room !HK4TPs-zOyq4LmFhbjKJCqw6Z4YYYSI8Zf6yRtsKPB8: Server error
   '500 Internal Server Error' for url 'http://localhost:6167/_matrix/client/v3/rooms/
   !HK4TPs-zOyq4LmFhbjKJCqw6Z4YYYSI8Zf6yRtsKPB8:localhost/join'
   INFO: 127.0.0.1:50924 - "PUT /_matrix/app/v1/transactions/followup-test-final HTTP/1.1" 200 OK

   # conduit container log, same request
   join_room_by_id{sender_user="@satsandesh_bot:localhost"
     room_id="!HK4TPs-zOyq4LmFhbjKJCqw6Z4YYYSI8Zf6yRtsKPB8:localhost"}:
     Joining !HK4TPs-zOyq4LmFhbjKJCqw6Z4YYYSI8Zf6yRtsKPB8:localhost over federation.
   Returning an error: 500 Internal Server Error: No server available to assist in joining.
   ```

**Conclusion: the theory is ruled out.** The missing-domain-suffix finding
and the join failure are two separate, unrelated issues, not one bug. The
domain-suffix fix is correct and worth keeping (it's a real latent bug —
passing an unqualified ID to a join call is wrong regardless), but it does
not touch the actual cause of the join failure, which is entirely
server-side in Conduit's local-join handling.

### Bonus fix found during this reproduction

The original join-on-invite code had no error handling: an unhandled join
failure crashed the whole transaction handler, returning `500` to the
homeserver for an event we *had* successfully received and stored — and
skipped marking the transaction processed, meaning a real homeserver would
retry it forever. Fixed by catching the join failure specifically and
logging it, while still acknowledging the transaction (`200 {}`), per the
last log excerpt above. Added
`test_invite_join_failure_does_not_break_transaction_ack` to cover this.
All 7 tests in `test_bot.py` pass after these changes.

### Test 2: fork retest (Tuwunel) — run, and it resolved the join bug

**Result: the join bug does not exist on Tuwunel.** Recommended by a
teammate's own ADR 0002 research (Student 2's Spike B document, shared
separately) as the actual target if the team pursues Option A — conduwuit
is archived too, Tuwunel is its maintained successor.

Setup: stood up `ghcr.io/matrix-construct/tuwunel:latest` in a second
container (`tuwunel`, port 6168), alongside the untouched Conduit
container (`conduit`, port 6167) — nothing from the original setup was
replaced. Registered a fresh AS (`satsandesh-spike-a-tuwunel`, its own
tokens) against it, ran a second bot instance (port 9001) pointed at
Tuwunel. Registration mechanism differs slightly from Conduit — Tuwunel
uses `!admin appservices register` / `unregister` / `list` in its
management room, not Conduit's `@conduit:server:` mention syntax.

**The join succeeded — direct proof, both rooms tested:**
```json
{"content":{"membership":"join"},"room_id":"!2z0PKiBNggCusblTgE:localhost","sender":"@satsandesh_bot:localhost", ...}
{"content":{"membership":"join"},"room_id":"!3BMz082n2mSQWBsEau:localhost","sender":"@satsandesh_bot:localhost", ...}
```
No `"No server available to assist in joining"` error, no federation
misrouting — the exact failure mode that blocked Conduit simply doesn't
happen here. **Confirms the bug is Conduit-specific, not a fundamental
limitation of the Application Service model.**

**One new, real compatibility gap found along the way:** Tuwunel calls an
appservice endpoint Conduit never did — `POST
/_matrix/app/unstable/org.matrix.msc3984/keys/query` (an experimental MSC
for proxying E2EE key queries through the AS). Our bot didn't implement
it, got a `404`, which the MSC says homeservers should treat as `{}` —
but rather than rely on that fallback, we added a real handler (returns
empty key sets, since this AS doesn't handle E2EE) and covered it with
two tests. Worth knowing: different Matrix server implementations expect
different endpoint surfaces beyond the core AS spec, and Tuwunel's is
larger than Conduit's.

**Then a second real blocker appeared — exactly the one the ADR already
predicted.** The first successful join delivered messages, but as
`m.room.encrypted` events (Element defaults new rooms to E2EE-on):
```json
{"type":"m.room.encrypted","content":{"algorithm":"m.megolm.v1.aes-sha2","ciphertext":"AwgAEpAB..."}}
```
Unreadable by the bot, by design — this is precisely the "appservice bots
and E2EE don't mix" constraint from ADR 0002. Confirmed empirically, not
just theoretically.

**Final test — a room created with encryption explicitly off — worked
completely:**
```json
{
  "content": {"body": "hello plaintext test", "m.mentions": {}, "msgtype": "m.text"},
  "event_id": "$kK-y8X0XMTp_gyE2jaZJLgPljSXACv2c0u5Ui4-lQu8",
  "room_id": "!gTliJtIDgTIMdCdvnB:localhost",
  "sender": "@bob2:localhost",
  "type": "m.room.message"
}
```
Plaintext, readable, exact typed content. This is the full chain working:
invite → bot auto-joins → plaintext message delivered to the bot.

### Final honest status

**Full message interception works — on Tuwunel, with E2EE explicitly
disabled.** It does not work on archived Conduit (confirmed bug in its
local-join handling) and does not work with E2EE enabled (by design, not
a bug — moderation/translation requires server-side-readable content).
Both conditions are addressable: don't build on archived Conduit anyway
(independently recommended), and disabling E2EE for moderated rooms is a
deliberate, documentable architecture decision, not a blocker to route
around. The core mechanism the whole proposal depends on — a homeserver
automatically forwarding room events to an external bot, which can then
run them through moderation/translation and re-inject a response — is now
proven to work end-to-end, on the homeserver the team would actually use.

---

## Migration to the shared team machine (2026-08-28)

*Appended after the two sections above, which are unchanged.* The full
Conduit + Tuwunel + bot setup was rebuilt on the team's shared Linux
machine (previously only local to one laptop). Reproducing it there
surfaced three infrastructure-level issues worth documenting — none of
them changed the mechanism-level findings above, but each one would
otherwise cost real time for whoever sets this up next.

### The shared machine has no traditional Docker permission path

The account this runs under has no `sudo` at all (confirmed: "not in the
sudoers file"), and this is a 40+ user shared server, not a private team
box — normal `usermod -aG docker` was a dead end. **Resolved without any
admin involvement**: rootless Docker was already available
(`dockerd-rootless-setuptool.sh`), with every prerequisite
(`newuidmap`/`newgidmap`, subuid/subgid ranges) already provisioned.
Running the setup tool as a plain user gives a fully working Docker
daemon at `unix:///run/user/<uid>/docker.sock` — verified with
`docker run hello-world`. No root-equivalent access was ever granted.

### Rootless Docker blocks containers from reaching the host

The first attempt reused the same `host.docker.internal` approach that
worked locally. It doesn't work here: rootless Docker's networking
sandbox (`rootlesskit`) is started with `--disable-host-loopback` by
default — an intentional security measure that prevents containers from
reaching back out to services running directly on the host. Since the
bot was running as a bare host process, Conduit/Tuwunel genuinely could
not reach it, confirmed directly in Conduit's own log:
```
Could not send request to appservice "satsandesh-spike-a" at
http://host.docker.internal:9000: error sending request for url (...)
```
**Fix:** containerized the bot itself (new `Dockerfile`,
`.dockerignore`) and put it on the same user-defined Docker network as
the homeservers, so they reach each other by container name over
Docker's internal networking rather than crossing the host boundary at
all. This sidesteps the restriction entirely rather than working around
it, and is arguably the more correct setup regardless of rootless vs.
rootful Docker.

### `docker network connect` needs a container restart before DNS works

Attaching an already-running container to a new network with `docker
network connect` does not reliably wire up Docker's embedded DNS for
that container immediately — the homeserver containers could not
resolve the bot containers' names until they were restarted after being
connected. Confirmed directly:
```
Could not send request to appservice ... at http://spike-bot-tuwunel:9000:
... Dns(ResponseCode(ServFail))
```
`docker restart` on the affected containers resolved it immediately,
verified with `docker run --rm --network spike-net busybox nslookup
spike-bot-tuwunel` succeeding afterward.

### One more real code bug, unrelated to networking

The bot's `HOMESERVER_URL` config still pointed at `localhost:<port>` —
correct when the bot ran directly on the host, wrong once it moved into
its own container, where `localhost` refers to the bot's own container,
not the homeserver's. Fixed by pointing it at the homeserver's container
name and *internal* port instead (e.g. `http://tuwunel:8008`, not the
externally-published `6168`).

### Final result, reproduced on the shared machine

With all three fixes applied, the exact same end-to-end proof from the
original Tuwunel test above was reproduced cleanly on the shared
machine: invite → bot auto-joins → plaintext message delivered,
confirmed on two separate rooms. Everything now runs under
`~/kshitiz/satsandesh/` there — `conduit`, `tuwunel`,
`spike-bot-conduit`, and `spike-bot-tuwunel` as four containers on a
shared Docker network, reachable by any teammate with access to the
machine, not just one laptop.

