"""Registers this service's appservice with Tuwunel on startup.

Every step here was run manually against a real Tuwunel container first,
one HTTP call at a time, with the actual response read before writing
this -- not assumed from the spec or from Tuwunel's docs page. See
docs/prompt-journal.md's dated entry for the raw verification transcript.

Sequence (all confirmed working, none guessed):
  1. Register a first user via the standard C-S API. Tuwunel's own docs
     say the first registered user becomes a server admin and is
     auto-joined to the admin room -- confirmed: joined_rooms returned
     exactly one room for a freshly registered first user.
  2. Registration needs completing a User-Interactive Auth `m.login.dummy`
     stage -- confirmed by reading the actual 401 response's `flows`.
  3. Send `!admin appservices register` with our registration YAML as a
     markdown code block in the SAME message -- confirmed by reading the
     admin bot's own reply in the room afterward
     ("Appservice registered with ID: <our id>"), not just trusting a 200
     from the send call (which only proves the message was accepted into
     the room, not that the command was understood).

Idempotent by re-running, but not quite the way Tuwunel's own docs
describe: re-registering the same admin username hits M_USER_IN_USE,
handled by logging in instead. For the appservice itself, Tuwunel's docs
(appservices.html) say re-registering an existing id "replaces the
previous entry" -- that's false for this version, confirmed by actually
re-running this twice: the second attempt gets "Failed to register
appservice: Duplicate id: <id>", not a silent replace. Treated as an
equally-valid success signal (see _send_and_confirm_registration) rather
than trusted from the docs -- safe to run this on every container start
either way, just not for the reason the docs state.
"""

import asyncio
import logging
import os
import uuid

import httpx

logger = logging.getLogger("bootstrap")

BOOTSTRAP_TIMEOUT = 15.0
CONFIRMATION_POLL_INTERVAL = 0.5


class BootstrapError(RuntimeError):
    pass


async def _register_or_login_admin(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post(
        "/_matrix/client/v3/register", json={"username": username, "password": password}
    )
    if resp.status_code == 401:
        session = resp.json()["session"]
        resp = await client.post(
            "/_matrix/client/v3/register",
            json={
                "username": username,
                "password": password,
                "auth": {"type": "m.login.dummy", "session": session},
            },
        )
    if resp.status_code == 200:
        return resp.json()["access_token"]

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if resp.status_code == 400 and body.get("errcode") == "M_USER_IN_USE":
        resp = await client.post(
            "/_matrix/client/v3/login",
            json={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": username},
                "password": password,
            },
        )
        if resp.status_code == 200:
            return resp.json()["access_token"]

    raise BootstrapError(f"could not register/login admin user: {resp.status_code} {resp.text}")


async def _find_admin_room(client: httpx.AsyncClient, admin_token: str) -> str:
    resp = await client.get(
        "/_matrix/client/v3/joined_rooms", headers={"Authorization": f"Bearer {admin_token}"}
    )
    if resp.status_code != 200:
        raise BootstrapError(f"could not list joined rooms: {resp.status_code} {resp.text}")
    rooms = resp.json()["joined_rooms"]
    if not rooms:
        # KNOWN LIMITATION, found running this against a non-fresh Tuwunel
        # volume (one that already had a real first user from earlier
        # testing): "first registered user becomes admin" is true only for
        # the actual first-ever user on that homeserver instance, not for
        # "the first time THIS username was registered". If some other
        # user got there first -- a previous test run, a previous compose
        # up against the same volume -- BOOTSTRAP_ADMIN_USERNAME registers
        # successfully as an ordinary user with zero special rooms, and
        # bootstrap has no way to recover from here: there's no HTTP API
        # to discover or join the admin room after the fact, only the
        # room the first user was automatically placed in. Fixing this
        # properly would mean tracking whether bootstrap already ran (e.g.
        # a marker file on the tuwunel_data volume) rather than re-deriving
        # admin status from scratch every start -- real scope, not solved
        # here. Failing loudly is deliberate: silently limping along
        # without an admin room would fail confusingly later instead.
        raise BootstrapError(
            "the bootstrap admin user has no joined rooms, so no admin "
            "room could be found. This means it was not the first user "
            "ever registered on this Tuwunel instance -- likely because "
            "the tuwunel_data volume already had a prior admin user from "
            "an earlier run. Fix: `docker compose --profile matrix down -v "
            "tuwunel matrix-circle-service` for a clean volume, then bring "
            "the matrix profile up again."
        )
    # First-user bootstrap auto-joins exactly one room in every run this
    # was verified against; if that ever isn't true, failing loudly here
    # beats silently picking the wrong room.
    return rooms[0]


async def _send_and_confirm_registration(
    client: httpx.AsyncClient,
    admin_token: str,
    admin_room_id: str,
    registration_yaml: str,
    appservice_id: str,
) -> None:
    headers = {"Authorization": f"Bearer {admin_token}"}
    body = f"!admin appservices register\n```yaml\n{registration_yaml}\n```"
    txn = uuid.uuid4().hex
    resp = await client.put(
        f"/_matrix/client/v3/rooms/{admin_room_id}/send/m.room.message/{txn}",
        headers=headers,
        json={"msgtype": "m.text", "body": body},
    )
    if resp.status_code != 200:
        raise BootstrapError(f"could not send registration command: {resp.status_code} {resp.text}")

    # A 200 here only proves the message landed in the room, not that the
    # admin bot understood it -- confirmed this distinction matters by
    # testing it: read the room back and look for the bot's own reply.
    #
    # Two acceptable replies, not one -- found the hard way. Tuwunel's own
    # docs (appservices.html) say "Registering with an existing ID
    # replaces the previous entry." That's false for this version: a
    # second registration of the same id gets "Failed to register
    # appservice: Duplicate id: <id>", not a silent replace. Confirmed by
    # reading the actual admin room contents after a real second run
    # failed a naive "only look for the success message" check.
    # "Duplicate id: <ours>" means the appservice is already registered
    # under our id, which for bootstrap's purposes IS success -- treated
    # as such rather than retried forever.
    deadline = asyncio.get_running_loop().time() + BOOTSTRAP_TIMEOUT
    success = f"Appservice registered with ID: {appservice_id}"
    already_registered = f"Duplicate id: {appservice_id}"
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(
            f"/_matrix/client/v3/rooms/{admin_room_id}/messages",
            headers=headers,
            params={"dir": "b", "limit": 5},
        )
        if resp.status_code == 200:
            for event in resp.json().get("chunk", []):
                body = event.get("content", {}).get("body", "")
                if event.get("type") != "m.room.message":
                    continue
                if success in body:
                    logger.info("appservice registration confirmed: %s", success)
                    return
                if already_registered in body:
                    logger.info("appservice already registered under our id: %s", appservice_id)
                    return
        await asyncio.sleep(CONFIRMATION_POLL_INTERVAL)

    raise BootstrapError(
        f"no confirmation of appservice registration seen within {BOOTSTRAP_TIMEOUT}s "
        f"(expected {success!r} or {already_registered!r} in the admin room)"
    )


async def bootstrap_appservice(
    homeserver_url: str,
    registration_yaml: str,
    appservice_id: str,
    admin_username: str,
    admin_password: str,
) -> None:
    async with httpx.AsyncClient(base_url=homeserver_url.rstrip("/"), timeout=BOOTSTRAP_TIMEOUT) as client:
        admin_token = await _register_or_login_admin(client, admin_username, admin_password)
        admin_room_id = await _find_admin_room(client, admin_token)
        await _send_and_confirm_registration(
            client, admin_token, admin_room_id, registration_yaml, appservice_id
        )
