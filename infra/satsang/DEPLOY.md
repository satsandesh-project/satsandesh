# Deploying the satsang feature to staging: the plan, with a rollback for every step

**STATUS: A PLAN. NOTHING HERE HAS BEEN RUN ON STAGING.** Staging (`liveapp`, `~/kshitiz/live-app`) is M1's stack:
every step that changes it needs his written go. Written 2026-10-16 against `main` at `a15040d`.
"Measured" below means: I ran it, read-only, on staging on 2026-10-16, or on a throwaway database or container on
the same server; "unmeasured" means I did not.

## What staging is today (read-only checks; re-run them before you start, section "Step 0")

| Fact | Value | Why it matters |
|---|---|---|
| Checkout | `197fef8` on `main`, clean, 13 Oct | **58 commits** behind `main` (not 121: that was 8 Oct) |
| Database | `alembic_version = a7c2e91f4b36` (the rooms migration), 17 tables, **no `sessions` table** | The new migrations are one chain: `bc32cd718b80` (sessions), `8e3234dc9fc8` (widens the audit log's CHECKs for satsang actions, #183), `d3a9f1c27b58` (reminder renderings, #171), and `c8f2a6d41e93` (reminder deliveries, #177). **Update 2026-10-17:** the measurements below were made on the two-migration chain before #183 and #177; **the round trip has not been re-run on the longer chain**. (It is not `b9d4f1a27c3e`: that was expected, and is wrong today.) |
| Containers | postgres 16-alpine, gateway, elder-app, caddy 2-alpine, speech, mt, moderation, render, ai-services; all healthy except caddy (no healthcheck) | The satsang layer adds one container, `satsang`. Port 1935 is free |
| Started with | project **`liveapp`**, files `docker-compose.yml`, `.tls.yml`, `.ai.yml` in `~/kshitiz/live-app` | Every compose command below needs `-p liveapp`: the directory is `live-app`, so without it compose starts a second, parallel project |
| Gateway env | `AUTH_MODE=legacy`, `PIPELINE_ENABLED=true`, `AI_RENDER_URL=http://render:8005` | The reminder's render service is reachable from the gateway on staging |
| **Running Caddy** | started 11 Oct. Its in-memory config (Caddy admin API) has routes only for `/ai/* /audio-labels/* /circles* /health /health/ready /me /me/settings /media* /messages* /onboarding* /ws` | **It has no `/rooms*`, no `/moderation*`, no `/livekit/*`, and none of mine.** `GET /sessions` and `GET /hls/x.m3u8` through staging today return the elder app's 404 page (404, `text/html`, 5110 bytes) |
| **Caddyfile mount** | the container sees a **4517-byte** Caddyfile (sha256 `73f1aa07…`); the file on disk is **5964 bytes** (`af430b7d…`) | The bind mount points at the old file (git replaced it after the container was created). **`docker compose restart caddy` would reload the OLD routes. It must be `up -d --force-recreate caddy`** |
| A trap in probing | `curl -k https://127.0.0.1:8443/...` from the server returns an empty 200 for every path | Use `https://10.110.11.31:8443`: the certificate and routing are for that address |

## Step 0: read-only commands (run as `satsandesh` on 10.110.11.31; they change nothing)

```bash
cd ~/kshitiz/live-app
git rev-parse --short HEAD; git status --porcelain | wc -l                 # 197fef8 and 0: the checkout, and no local edits
docker exec liveapp-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -A -c "select version_num from alembic_version"'   # a7c2e91f4b36
docker ps --filter name=liveapp --format 'table {{.Names}}\t{{.Status}}'
grep -c '^:80 {' infra/caddy/Caddyfile; grep -c 'handle /hls' infra/caddy/Caddyfile     # 1 and 0 (the /hls handle is not there yet)
docker exec liveapp-caddy-1 sh -c 'wc -c < /etc/caddy/Caddyfile; sha256sum /etc/caddy/Caddyfile'; wc -c < infra/caddy/Caddyfile; sha256sum infra/caddy/Caddyfile
docker exec liveapp-caddy-1 wget -qO- http://127.0.0.1:2019/config/ | grep -o '"path":\[[^]]*\]' | sort -u     # the routes Caddy really holds
```
How to read the answers: a head other than `197fef8` or a version other than `a7c2e91f4b36` means staging moved
(someone deployed) and **this plan must be re-checked before step 3**; the two Caddyfile sizes differing means the stale mount
above is still there (it will be until the container is recreated); no `/hls` in Caddy's holdings means the feature is not live.

## The plan (every step: do, expect, and how to go back)

Exports for every command from step 4 on. **Never print them, never commit them.** The two secrets must be exported
again for every later `docker compose` command that touches the gateway, or compose refuses (`:?`), which is intended:
```bash
cd ~/kshitiz/live-app
DC="docker compose -p liveapp -f docker-compose.yml -f docker-compose.tls.yml -f docker-compose.ai.yml -f docker-compose.satsang.yml"
DC_OLD="docker compose -p liveapp -f docker-compose.yml -f docker-compose.tls.yml -f docker-compose.ai.yml"     # what staging runs today
G=liveapp-gateway-1
envof() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n "s/^$2=//p"; }
export GATEWAY_VAPID_PRIVATE_KEY=$(envof $G VAPID_PRIVATE_KEY) GATEWAY_VAPID_PUBLIC_KEY=$(envof $G VAPID_PUBLIC_KEY) GATEWAY_VAPID_SUBJECT=$(envof $G VAPID_SUBJECT)
export TLS_HOST=10.110.11.31 SATSANG_PUBLIC_HOST=10.110.11.31
export SATSANG_HOOK_SECRET=$(head -c 40 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 24)   # from here on it is whatever the running gateway holds: envof $G SATSANG_HOOK_SECRET
```
(`TLS_HOST` and the push keys come from the shell, not from `.env`: a recreate without them either refuses or swaps the keys.)

| # | Do | Expect | Roll back |
|---|---|---|---|
| 1 | **Back up the database**: `docker exec liveapp-postgres-1 sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > ~/staging-before-satsang.dump; docker run --rm -v ~:/b postgres:16-alpine pg_restore -l /b/staging-before-satsang.dump \| head -3` | a non-empty file and a table of contents | nothing changes, nothing to undo |
| 2 | **Keep the old images and files**: `for s in gateway elder-app; do docker tag $(docker inspect -f '{{.Image}}' liveapp-$s-1) liveapp-$s:before-satsang; done; sha256sum infra/caddy/Caddyfile > ~/caddyfile.before.sha` | two `:before-satsang` tags | `docker rmi liveapp-gateway:before-satsang liveapp-elder-app:before-satsang` |
| 3 | **Update the checkout, fast-forward only**: `git fetch team && git merge --ff-only team/main` (M1's checkout: his go) then `git rev-parse --short HEAD; grep -c '^:80 {' infra/caddy/Caddyfile` | HEAD = main's; `1` | `git switch --detach 197fef8` (no reset). The running containers have not changed yet |
| 4 | **Check before building**: `$DC config --quiet; echo $?`; compare the push key still matches: `[ "$(echo -n "$GATEWAY_VAPID_PUBLIC_KEY" \| sha256sum)" = "$(echo -n "$(envof $G VAPID_PUBLIC_KEY)" \| sha256sum)" ] && echo same-key`; `ss -ltn \| grep -c ':1935 '`; `docker run --rm -v $PWD/infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile` | `0`, `same-key`, `0`, "Valid configuration" | nothing changed |
| 5 | **Build, do not start**: `$DC build gateway elder-app satsang` | three images built | nothing is running from them: `docker rmi` the new ones if you want the space. **Time: unmeasured** (the elder-app image runs a Reflex export) |
| 6 | **Migrate with the new image while the old gateway keeps serving**: `$DC run --rm --no-deps gateway alembic upgrade head` | `Running upgrade a7c2e91f4b36 -> bc32cd718b80` then `bc32cd718b80 -> 8e3234dc9fc8`, `8e3234dc9fc8 -> d3a9f1c27b58` (and `-> c8f2a6d41e93` once #177 is in); then `alembic current` = the head (`alembic heads` must list exactly one). As originally measured, the chain was `bc32cd718b80 -> d3a9f1c27b58`. The new tables are additive: the running old gateway does not notice them. **Do not restart the old gateway between steps 6 and 8** (see the box) | **`$DC run --rm --no-deps gateway alembic downgrade a7c2e91f4b36`** (new image; proven, below). A migration that fails part-way should leave nothing behind (Alembic runs them in one transaction on Postgres: **assumed, not tested here**); check with `alembic current` |
| 7 | **Start the media server**: `$DC up -d satsang` | `satsang` healthy; `ss -ltn \| grep ':1935 '` shows it | `$DC rm -sf satsang` |
| 8 | **Recreate the gateway on the new image**: `$DC up -d --no-deps gateway`; wait for `docker inspect -f '{{.State.Health.Status}}' $G` = `healthy` | healthy; `docker logs $G 2>&1 \| grep -i "satsang\|Traceback" \| head` shows no traceback. Open WebSocket clients drop and reconnect (expected, **unmeasured**) | the **rollback block** below |
| 9 | **Recreate the elder app**: `$DC up -d --no-deps elder-app` | healthy; `/satsang` and `/satsang-admin` answer 200 HTML | `docker tag liveapp-elder-app:before-satsang liveapp-elder-app:latest; git switch --detach 197fef8; $DC_OLD up -d --no-build --no-deps elder-app` |
| 10 | **Recreate Caddy (not restart)**: first note the CA: `docker exec liveapp-caddy-1 sha256sum /data/caddy/pki/authorities/local/root.crt`; then `$DC up -d --no-deps --force-recreate caddy`; the same `sha256sum` again | the **same root hash** (the `liveapp_caddy_pki` volume is kept, so phones' trust is unchanged); a few seconds without HTTPS | `git switch --detach 197fef8; $DC_OLD up -d --no-deps --force-recreate caddy` (back to today's routes) |
| 11 | **Smoke test** (next section) | five answers as listed | per step |

**The rollback block for steps 6 to 8: the ORDER matters, and it is proven.** After the new migrations have run,
staging's current gateway image **cannot start**: its command is `alembic upgrade head && uvicorn ...`, and Alembic stops with
`Can't locate revision identified by 'd3a9f1c27b58'` (measured then; the same for any newer revision) (**measured: exit 255**), so the container would restart in a loop. So, to go back:
```bash
$DC stop gateway                                                    # the new gateway
$DC run --rm --no-deps gateway alembic downgrade a7c2e91f4b36       # with the NEW image, which knows the revisions
docker exec -i liveapp-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<< "delete from jobs where job_type = 'satsang_reminder';"   # a downgrade leaves them; the old code would retry them as "no handler"
docker tag liveapp-gateway:before-satsang liveapp-gateway:latest; git switch --detach 197fef8
$DC_OLD up -d --no-build --no-deps gateway
```
**The downgrade to `a7c2e91f4b36` works only while no satsang action has been audited (added 2026-10-17, from M1's note on #183).** `8e3234dc9fc8` narrows the audit log's CHECK lists on the way down, and `admin_actions` is append-only, so once a satsang is created, cancelled, ended or has its key rotated through the admin page (`session.*` rows), the downgrade fails at that revision, loudly, and the rows cannot be deleted to make room. Then the order above cannot finish: the old image still cannot start on the newer revision. The rollback after that is a restore of the `pg_dump` taken in step 1, which loses everything written since. **Read from the migration's own comment and code, not run.** So do not skip the step 1 `pg_dump`, and treat the first satsang created on staging as the point after which a downgrade is no longer a rollback.
**A downgrade deletes every satsang and every reminder rendering created since the deploy** (the tables are dropped). Take a
`pg_dump` first if any matter. Nothing else is touched: the `users` rows and everything older stay.

## The migration path, reversed and shown (measured on a real Postgres 16)

`infra/satsang/migration_roundtrip.sh` on the shared server's own Postgres, in a throwaway database, from a throwaway checkout of
`team/feat/m2-w10-reminder-delivery` at `937c42b` (the only branch holding both migrations today); staging's version is the same
image, `postgres:16-alpine`. Its output, trimmed to the lines that matter:
```
## 0. staging's state: a fresh database upgraded to a7c2e91f4b36     sessions: f  renderings: f  trigger fn: 0
alembic heads (must be exactly one):  d3a9f1c27b58 (head)
## 1. alembic upgrade head
Running upgrade a7c2e91f4b36 -> bc32cd718b80, add sessions: the scheduled satsang broadcast
Running upgrade bc32cd718b80 -> d3a9f1c27b58, add satsang_reminder_renderings: a satsang reminder, per language
d3a9f1c27b58 (head)     sessions: t  renderings: t  trigger fn: 1
## 2. real rows in the new tables     rows now: sessions=1 renderings=1 queued reminder jobs=1
## 3. downgrade -1, then -1 again, WITH THE ROWS PRESENT
Running downgrade d3a9f1c27b58 -> bc32cd718b80 ...   bc32cd718b80   sessions: t  renderings: f  trigger fn: 1
Running downgrade bc32cd718b80 -> a7c2e91f4b36 ...   a7c2e91f4b36   sessions: f  renderings: f  trigger fn: 0
schema after the two downgrades vs staging's state: IDENTICAL
NOT removed by a downgrade: queued satsang_reminder jobs = 1 (the old code has no handler for them); users rows = 1
## 4. forward again:   schema after re-upgrade vs after the first upgrade: IDENTICAL
## 5. one command: alembic downgrade a7c2e91f4b36   ->  a7c2e91f4b36   schema ... vs staging's state: IDENTICAL
```
"IDENTICAL" is a diff of `pg_dump -s` with its two random `\restrict` lines removed (an earlier run of the same script without that
filter said DIFFERENT, and the only differences were those two lines). Not tested: a migration that fails half-way; a
downgrade with a real moderator's data in the tables beyond one row each; the time any step takes on staging's data.

## The smoke test: five requests after the deploy (from a laptop on campus; `-k` because the CA is internal)

(Rows 1, 2 and 4 are from `app/auth.py`, the route tests and `test_tls_internal.sh`, **not yet seen on staging**.) Before the deploy the first four show the **elder app's 404 page** (404, `text/html`, 5110 bytes, measured today), which is
how to tell them from the answers below. A status code alone is not enough: the catch-all is the elder app.

| # | Request | Expected | A different answer means |
|---|---|---|---|
| 1 | `curl -sk -i https://10.110.11.31:8443/sessions` | **401**, `application/json`, `{"detail":"Not authenticated"}` | **404 + `text/html`**: Caddy was not recreated or does not hold `/sessions*` (step 10). **502**: the gateway is down or restarting (step 8) |
| 2 | `curl -sk https://10.110.11.31:8443/sessions -H "Authorization: Bearer <your own user id>"` | **200**, `{"items": []}` (a list; empty until one is scheduled). Your id is a credential in `AUTH_MODE=legacy`: do not paste it anywhere | **500**: the table is missing, the migration did not run (step 6). **401**: the id is not a user on staging |
| 3 | `curl -sk -i https://10.110.11.31:8443/hls/does-not-exist.m3u8` | **404** with a short nginx body (153 bytes on the throwaway media server, **measured**), **not** the 5110-byte Reflex page | the 5110-byte page: Caddy lacks `/hls*`. **502**: the `satsang` container is not up or not healthy (step 7) |
| 4 | `curl -sk -i -X POST https://10.110.11.31:8443/sessions/ingest/publish` | **404, empty body**: the media-server hooks are refused at the edge | **anything else (401, 403, 422) is a security problem**: the hook, guarded only by a shared secret, is reachable from outside. Stop and recreate Caddy from the current Caddyfile |
| 5 | `nc -vz 10.110.11.31 1935` (or `Test-NetConnection 10.110.11.31 -Port 1935`) | connection **succeeded** | refused: the `satsang` container is not up or not publishing; times out: the phone's network is isolating clients (INGEST_FINDING.md saw it open from a laptop on campus Wi-Fi) |

Also look at, by hand: `https://10.110.11.31:8443/satsang` (the elder list; 200 HTML) and `/satsang-admin` (the admin page), and
`/health` (200 `{"status":"ok"}`, which this deploy must not change). Scheduling a satsang needs an administrator's signed token:
`docker exec liveapp-gateway-1 python -m app.tokens <an admin or moderator user id>` (staging may have none: create it with M1).
