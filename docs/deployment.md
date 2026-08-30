# Deployment

How to run the full stack (base services + the Matrix backbone profile,
per ADR 0002) on a remote Ubuntu host, and what's deliberately deferred.

## Status

**Not yet executed against a real remote host.** This document, and
`infra/deploy/deploy.sh`, were written and the steps individually verified
locally (Docker install command, ufw commands, `docker compose` commands
all run correctly against this project's own compose file), but there is
currently no confirmed remote server to run them against -- see
`docs/prompt-journal.md`'s Week 4 entry for why. Treat the steps below as
a real, executable plan, not a placeholder -- but the "two tabs from two
devices exchange a message through a public IP" acceptance test has not
been run for real yet. Update this section (and the journal) once it has.

## What you need before starting

- An Ubuntu host (22.04+) reachable over SSH, with a public IP. A
  DigitalOcean droplet or an Oracle Cloud free-tier VM both work --
  nothing here is provider-specific.
- No domain name is assumed. Everything below serves plain HTTP on the
  server's raw IP.
- SSH access as a user that can `sudo` (for installing Docker and
  configuring the firewall).
- A `.env` file with real production secrets, prepared on your own
  machine first (see "Producing a production `.env`" below). **Never
  commit this file** -- it travels to the server over `scp`, not git.

## Why plain HTTP, not HTTPS, for now

Caddy's automatic HTTPS (Let's Encrypt / ZeroSSL) requires a real domain
name it can prove ownership of -- it cannot issue a certificate for a bare
IP address. Since there's no domain yet, `infra/caddy/Caddyfile` is
configured to serve plain HTTP on `:80` only.

**This is a deliberate, tracked gap, not an oversight.** It needs to be
closed before Week 6-7: browsers block `navigator.mediaDevices.getUserMedia`
(microphone access) on any origin that isn't HTTPS or `localhost`, and
voice input is on the roadmap for that point. The fix at that time is:
point a real domain's A record at the server's IP, add that domain to the
Caddyfile in place of `:80`, and Caddy will provision and renew the
certificate automatically -- no other code changes expected.

## Producing a production `.env`

Copy `.env.example` to `.env` and fill in real values, same shape as local
dev but with values that aren't the checked-in dev defaults:

```bash
cp .env.example .env
```

- `POSTGRES_PASSWORD`: a real random password, not `changeme_dev_password`.
- `SESSION_SECRET`: `openssl rand -hex 32` -- signs gateway session
  tokens (`gateway/auth.py`); anyone who has this can forge a token for
  any user id.
- `ALLOWED_ORIGINS`: must include the origin the browser will actually
  use to reach the client. For a bare-IP staging deploy this is
  `http://<server-ip>` (no port -- Caddy serves `:80`, and browsers omit
  the default port from the `Origin` header). Example:
  `ALLOWED_ORIGINS=http://203.0.113.10`
- `PUBLIC_ORIGIN`: the same value, `http://<server-ip>` -- this is what
  the containerized elder-app and the gateway both use to build
  browser-facing URLs (see `docker-compose.yml`'s `elder-app` service).

Copy this file to the server with `scp` (not git):

```bash
scp .env youruser@<server-ip>:~/satsandesh/.env
```

## Deploy steps

1. **Clone the repo onto the server:**

   ```bash
   git clone <repo-url> satsandesh
   cd satsandesh
   ```

2. **Copy `.env` into place** (see above) if you haven't already --
   `infra/deploy/deploy.sh` refuses to start without one.

3. **Run the deploy script:**

   ```bash
   chmod +x infra/deploy/deploy.sh
   ./infra/deploy/deploy.sh
   ```

   First run installs Docker Engine + the Compose plugin
   (`get.docker.com`'s official installer) if not already present, adds
   your user to the `docker` group, and exits asking you to re-login for
   the group change to apply -- run it again after that. On the run that
   actually proceeds, it:

   - Configures `ufw` to allow only SSH and port 80, denies everything
     else inbound, and enables it. **Deliberately does not open** 5432
     (Postgres), 8008 (Tuwunel), or 8101 (matrix-circle-service) --
     `docker-compose.yml` publishes those to the host for local dev
     convenience (manual poking during the Matrix spike), and there's no
     reason for any of them to be reachable from the public internet on a
     staging box. Caddy on `:80` is the only intended entry point.
   - Runs `docker compose build`.
   - Starts the stack with `docker compose --profile matrix up -d` --
     the `matrix` profile, not a plain `docker compose up`, because ADR
     0002 decided Matrix/Tuwunel as the actual backbone; a plain
     `docker compose up` would leave the gateway pointed at a
     `matrix-circle-service` that was never started.
   - Polls `docker compose ps` until every service reports healthy (or
     30 x 5s tries out).

4. **Confirm healthchecks pass on the remote host itself:**

   ```bash
   docker compose ps
   ```

   Every service should show `(healthy)`. If `elder-app` doesn't, check
   `docker compose logs elder-app` -- see "Known gaps" below re: the
   Docker build's frontend-dependency install, which needed a real fix
   during local development and should be watched for on first remote
   build too.

5. **Verify from a different machine** (not the one you deployed from --
   confirms it's actually reachable from the public internet, not just
   from the server's own loopback):

   ```bash
   curl -i http://<server-ip>/health
   ```

   Then open `http://<server-ip>` in a browser, in two separate tabs (or
   better, from two separate devices), join with two different display
   names, and exchange a message.

6. **Verify reconnect on the remote host** -- with the page open in a
   browser, kill the gateway container and watch the client recover
   without a page reload:

   ```bash
   docker kill satsandesh-gateway-1   # or: docker compose kill gateway
   # watch the browser tab's status line go from "connected" through
   # reconnect attempts back to "connected" on its own
   docker compose up -d gateway       # or: docker compose start gateway
   ```

## Record the server's identity -- never in git

Put the real IP address (and anything else identifying about this
specific server) in `docs/server-notes.md`. That filename is in
`.gitignore` specifically so this never gets committed by accident --
create it locally, it will not show up in `git status` as untracked-and-
ignorable, it'll just be ignored outright.

## Shared hosts: check ports before starting

If this is a shared server (a college machine, a box other teammates
also deploy to), **check ports 80 and 5432 aren't already taken before
running the stack**:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:80   # non-000 means something's there
ss -tlnp 2>/dev/null | grep -E ":80 |:5432 "
```

Both `docker-compose.yml`'s `postgres` and `caddy` services take their
host-side port from an env var (`POSTGRES_HOST_PORT`, `CADDY_HOST_PORT`,
both defaulting to the standard port) specifically so a shared host can
remap around whatever's already running -- nothing in the stack reaches
either service via the host port, every service talks to them by name
over the compose-internal network. If you remap `CADDY_HOST_PORT`, update
`ALLOWED_ORIGINS` and `PUBLIC_ORIGIN` in `.env` to include that same port
explicitly (e.g. `http://<ip>:8080`) -- browsers only omit the port from
the `Origin` header when it's the protocol's real default (80 for http),
not whatever this happens to be set to.

Hit exactly this on the actual shared college server used for this
project: a system Apache already on 80, and a teammate's own Postgres
container already on 5432. See the Week 4 journal entry.

## Known gaps / deferred on purpose

- **HTTPS**: deferred until a domain exists, required before Week 6-7 for
  microphone access. See "Why plain HTTP" above.
- **Single point of failure everywhere**: one gateway container, in-memory
  connection registry (`gateway/ws.py`) -- a gateway restart drops all
  live sockets and clients land in a fresh circle on reconnect (see
  `gateway/ws.py`'s module-level `_default_circle_id` and the Week 4
  journal entry). Acceptable for a staging demo; not a real HA setup.
- **No image registry / CI build**: `deploy.sh` builds images on the
  target host itself via `docker compose build`, not from a pre-built
  image pushed to a registry. Fine at this scale; would need revisiting
  before this became a real multi-server deployment.
- **Elder client's own Docker build**: needed a real, non-obvious fix
  during local development to get `reflex init`'s frontend-dependency
  bootstrap working reliably (see the Week 4 prompt-journal entry for the
  specifics) -- worth re-checking the actual build log the first time
  this runs on a fresh remote host, since network conditions there will
  differ from the machine this was developed on.
