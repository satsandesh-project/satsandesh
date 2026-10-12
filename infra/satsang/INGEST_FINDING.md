# Satsang broadcast: how audio gets INTO the server (2026-10-12)

M2 Feature 1 (Weeks 9–10) says "a satsang streams from a phone". This is the measurement phase: which
ingest path is actually possible on the shared server, decided before any code. No code was written,
no compose file or Caddyfile was touched.

## 1. Measurements

Every row says how it was tested and from where. "Untested" means untested, not "probably fine".

| Question | Tested how, from where | Result |
|---|---|---|
| Is TCP 1935 free on the host? | `ss -tuln` on 10.110.11.31 | **Free.** Nothing listening |
| Can **rootless** Docker publish 1935? | `docker run -p 1935:1935` on the host; `docker ps`, `ss`, container log | **Yes.** `0.0.0.0:1935->1935/tcp`, host socket present |
| Does a listener on 1935 answer from inside the box? | `/dev/tcp` to 127.0.0.1 and to 10.110.11.31 | **Yes**, both. Says nothing about inbound |
| Is 1935 reachable **inbound from another machine**? | TCP connect from this laptop, campus **Wi-Fi**, 10.110.235.193 (Intel Wi-Fi 6E, native 802.11) | **Open**, banner read back in 0.06 s |
| Is a host-bound port reachable too (control, to separate "Docker can't publish" from "network blocks")? | Same, port 1936, plain python bind | **Open.** So neither rootless Docker nor the network is the obstacle |
| Is **UDP** carried to that host between campus subnets? | Rootless container publishing 17882/udp; 4 datagrams from this laptop | **4/4 echoed**, 3.5–45 ms |
| Is 8443 (Week 8 HTTPS) reachable from another machine? | TCP + real TLS handshake from this laptop | **Open.** TLS 1.3, `GET /health` → `HTTP/1.1 200 OK` |
| Does that certificate satisfy a normal client? | Same handshake with default CA verification | **No**: `CERTIFICATE_VERIFY_FAILED`. This is what an elder's phone sees today |
| Is anything reachable **from off campus**? | HTTPS GET to the institution's outbound address `14.139.86.54:8443`, from outside the campus network | **`ECONNREFUSED`.** Nothing is port-forwarded |
| Do we have root, to open a port or read the firewall? | `sudo -n`, `ufw status`, `iptables -S` | **No root.** Firewall not inspectable |
| What owns the low ports? | `ss -tulnp`, `systemctl is-active apache2`, `curl localhost:80` | **`:80` is the system Apache** (active, answers 200). Not ours, cannot be taken |
| What else is taken? | `ss -tulnp`, `docker ps` | 8095 + 8443 `liveapp` (M1's staging), 18300 + 18443 `maindemo`, 8080 my old demo, 55432/55434 Postgres, 15990, 22, 139/445 (Samba), 3389 |
| `ip_unprivileged_port_start` | `/proc/sys/...` on the host | **1024.** So any port ≥ 1024 is ours to publish; nothing below it ever is |

**The two facts that decide everything below.** Arbitrary high ports, TCP **and** UDP, are reachable on
campus Wi-Fi without asking anyone. Nothing at all is reachable from the public internet, and we have no
root to change that.

## 2. The decision: RTMP on 1935 (option a), not WHIP, for Weeks 9–10

**Building:** `nginx-rtmp` (or MediaMTX in RTMP mode) in `docker-compose.satsang.yml`, publishing TCP
1935, HLS segments served through the existing Caddy on 8443.

**Why not WHIP over HTTPS, which looked like the tidier answer.** WHIP's *signalling* is an HTTPS POST
and would indeed ride through the port already open. But its *media* is WebRTC, which needs its own ICE
path — a published UDP port (or ICE-over-TCP). So WHIP does not avoid publishing a port; it just moves
which one. That part is fine: UDP is proven to work. The real obstacle is different: **publishing from a
browser needs the microphone, and `getUserMedia` needs a secure context.** Staging's origin fails normal
certificate verification (measured above), so whether a phone browser will hand over the microphone
there is an open question — and it is the same question the parked domain/HTTPS decision is blocking.
Option (a) does not depend on that question at all, because the publisher is an app, not a web page.

**What option (a) costs.**
- **A browser cannot speak RTMP** — there is no browser API for it. The speaker must use an app. That is
  the honest price of this path.
- RTMP is cleartext on 1935. On the campus network, for a discourse that is about to be broadcast to a
  circle anyway, I judge that acceptable; the stream key is the thing worth protecting, and it travels in
  the same cleartext handshake. Worth revisiting if ingest ever crosses the internet.
- HLS adds latency between speaker and listener (segment length, typically seconds, not milliseconds).
  Not measured yet; it must be in Week 10, because W11's Q&A queue is built on top of it and a question
  asked several seconds behind the live edge is a design constraint, not a bug.

**What it rules out, and why that is not a loss yet:** no browser-based "go live" page in Weeks 9–10. I
would add WHIP later as a second ingest route once there is a trusted certificate, by which time it is an
addition to a working feature rather than a bet.

## 3. What the speaker will actually use

- **Publish:** the **Larix Broadcaster** app (free, Android and iOS), pointed at
  `rtmp://10.110.11.31:1935/live/<stream key>`, on campus Wi-Fi. The gateway checks the key in an
  `on_publish` hook, so a stream with no valid key is refused. **Not yet verified by me** — I have not
  installed it or completed one RTMP handshake; what is proven is that a TCP listener on 1935 is
  reachable from a Wi-Fi client. Fallback, certain to work: **OBS Studio** on a laptop, same URL.
- **Listen:** the elder app's join-to-listen page, `hls.js` against
  `https://10.110.11.31:8443/hls/<session>.m3u8`, on a phone on campus Wi-Fi, with the internal CA
  trusted or its warning accepted.

## 4. What needs a human

1. **A phone, on campus Wi-Fi.** Every measurement above is from a laptop. The laptop is on campus Wi-Fi,
   which makes it a good proxy, but a phone may land on a different SSID or VLAN with client isolation.
   Someone should open `https://10.110.11.31:8443/health` on a phone and tell me what happens.
2. **Does a phone browser on staging grant the microphone?** This decides whether WHIP is ever possible
   without the domain. There is evidence it may already work: the six Telugu voice notes held on staging
   on 11 Oct were recorded through the browser on that same origin. **The unknown is whether that device
   had the internal CA installed or merely clicked past the warning** — the two behave differently for
   microphone access. @kpspyolo024 can answer this in one line, and it is worth asking this week.
3. **Nothing is reachable from off campus.** If a satsang must be watchable on mobile data, or if the
   Week 10 demo is not on campus, the institution has to port-forward — the same request as the domain
   and HTTPS one. `ECONNREFUSED` from outside proves nothing is forwarded today. **Week 10's demo must be
   planned as an on-campus demo** unless that request is made and granted.
4. **Install the publishing app** on the speaker's phone before the Week 10 demo, and do one end-to-end
   rehearsal that is not demo day.

## 5. What Week 10 depends on

- **An additive `handle` block in `infra/caddy/Caddyfile`** for the HLS path, inside the existing `:80`
  site and above the catch-all. Checked: `tls-internal-entrypoint.sh` does `grep -qx ':80 {'` and exits 3
  without it, so that line survives untouched; the file has it exactly once, at line 1. A **Caddy restart
  is needed** for it to take effect, which is M1's stack and his go — the same lesson as `/moderation*`,
  which is still a 404 on staging for exactly this reason.
- **A reminder with its own clock.** `app/jobs.py` has `register_handler`, but the queue has **no
  future-due support** (no `due_at`, `run_after` or `available_at`). So the reminder stores its own
  `due_at` on the `sessions` rows and needs a periodic sweep. `run_retention_sweep_loop`, started in
  `app/main.py`'s `lifespan`, is the shape to copy.
- **Two additive lines in `app/main.py`** (an `include_router` and one `create_task` for that sweep).
  `main.py` is outside the files this feature owns, and I am flagging it rather than assuming: it is the
  same one-line additive pattern every other router has used, but it is a shared file.
- The HLS latency number from §2, measured, before W11's Q&A queue is designed on top of it.

## Not done in this phase

No code, no contract, no migration, no compose file, no Caddyfile edit. No RTMP handshake, no HLS
playback, no WHIP server installed, no phone involved, and no sustained media flow (the UDP result is 4
datagrams, not a jitter or loss measurement). Every temporary listener and container I started was
removed; `docker ps -a` shows no `satsang-*` container and nothing listening on 1935, 1936 or 17882.
