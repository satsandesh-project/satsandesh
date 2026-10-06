# infra/caddy/

**Owner:** Student 2 (Platform & backbone)

The reverse proxy in front of the gateway and the elder-app. By default it serves **plain HTTP**
(`Caddyfile`, one `:80 { }` block). There is an **opt-in HTTPS mode** (below) that gives real TLS
to testers who install our certificate authority. **There is no public HTTPS URL**, and this
folder does not pretend otherwise.

## Option chosen, and why the others are unavailable

Four ways to get HTTPS were considered (2026-10-06):

| Option | Status |
|---|---|
| (a) public domain + Let's Encrypt | **Unavailable:** no domain exists, and the server (`10.110.11.31`, a private address behind an institutional NAT) has no known inbound path from the internet; a system Apache owns host port 80 and nobody on the team has root to move it. |
| (b) domain + DNS-01 challenge | **Unavailable:** needs a domain and DNS API credentials; our Caddy image also has no DNS plugin. |
| **(c) Caddy's internal CA** | **Implemented here.** Needs nobody's permission. |
| (d) a tunnel provider | Reachable from the server, not built: the provider decrypts all traffic (elders' voice notes), which is a data-protection decision, not an engineering one. |

(c) gives **real HTTPS only to devices that trust our root certificate**. It is for testers, not
the public.

## Plain HTTP (what staging runs today)

`docker-compose.yml` publishes the container's 80 on `CADDY_HOST_PORT` (staging: 8095). Routes
are in `Caddyfile`; **`/moderation*` is deliberately not routed** while the gateway's identity is
a stub (see `services/gateway/OPEN_QUESTIONS.md` #23). Browsers allow the **microphone** only on
HTTPS or `localhost`, so real browser voice notes cannot be recorded from `http://<ip>:<port>`.

## Opt-in HTTPS (`docker-compose.tls.yml`)

```bash
TLS_HOST=10.110.11.31 docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d caddy
# https://10.110.11.31:8443   (CADDY_TLS_HOST_PORT changes the 8443)
```

What the mode does (`tls-internal-entrypoint.sh` derives all of it from the existing Caddyfile, so
every route and comment reaches the HTTPS site unchanged and there is no second copy to drift):

- the HTTPS site serves the same routes with the same precedence; `/moderation*` stays unrouted;
- plain HTTP on the container's `:80` **redirects** (302) to `https://<TLS_HOST>:<port>/...`,
  keeping path and query. 302 on purpose: browsers cache permanent redirects, and a tester may not
  have installed the root yet;
- response headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  strict-origin-when-cross-origin`, `X-Frame-Options: SAMEORIGIN`, no `Server` header;
- **no HSTS.** HSTS pins a site to HTTPS in the browser for a long time; with a certificate from
  an internal CA it would lock out every tester who has not (yet) trusted the root. Add it only
  once the certificate is publicly trusted;
- `default_sni`: a client that connects to an IP address (every browser pointed at
  `https://10.x.x.x:8443`) sends no server name, and behind docker port publishing Caddy would
  look up the container's own address and fail with `tlsv1 alert internal error`. **This was a
  real bug, found only by testing from a laptop** (the in-network test had identical addresses);
- HTTP/3 is switched off (it was advertised via `Alt-Svc` on a UDP port this stack does not publish).

### What a tester has to install

Get the root from the running container and give it to the tester **through a channel you trust**
(not over the HTTP site), together with its fingerprint to compare:

```bash
docker compose -f docker-compose.yml -f docker-compose.tls.yml cp \
    caddy:/data/caddy/pki/authorities/local/root.crt ./satsandesh-root.crt
openssl x509 -in satsandesh-root.crt -noout -subject -fingerprint -sha256
```

Without it a client refuses the connection: seen from a Windows laptop, `curl` reports
`SEC_E_UNTRUSTED_ROOT`; a browser shows a certificate warning. With it installed in the OS or
browser trust store the padlock is clean. How to install a root differs by OS and browser
(Windows: *Trusted Root Certification Authorities*; Android: *Install a certificate, CA
certificate*; iOS: install the profile **and** enable it under *Certificate Trust Settings*);
**those steps were not run here**. Anyone holding the root's private key (inside the `caddy_pki`
volume) can mint certificates that tester devices will trust: treat the volume as a secret and
install the root only on devices you control.

## Who renews the certificate, and what happens if nobody is watching

Observed from the running endpoint (2026-10-06): the **leaf** certificate is valid **12 hours**
(06:01 to 18:01 the same day) and carries `IP Address:10.110.11.31`; the **intermediate** is valid
**7 days**; the **root** is valid **10 years** (to 2036).

- **Who renews:** the Caddy process, by itself, with no external service (the CA is local). Caddy
  is documented to renew the short-lived leaf automatically and to rotate the intermediate; **I
  observed one issuance, not a renewal.**
- **If Caddy keeps running:** nothing to do.
- **If Caddy is stopped for more than ~12 hours:** the leaf expires and the site fails TLS until
  Caddy starts again; on start it issues a new one (documented behaviour, not observed).
- **The real operational risk is the `caddy_pki` volume.** The root lives there so it survives
  restarts and re-creates. Delete the volume (`down -v`, a new host) and a **new root** is made:
  **every tester must trust the new one.** Back it up if testers are relying on it.
- **A wrong system clock** on the server or a tester's device makes valid certificates look
  expired.

## What is still missing for a genuinely public URL

1. A **domain** and a certificate a browser trusts without installing anything: (a) or (b).
2. For (a): an inbound path from the internet to this host (port 443, plus 80 unless
   TLS-ALPN is used), which only whoever runs the network can open; for (b): DNS API credentials
   and a Caddy build with the provider's plugin.
3. **The elder app must be told it is on HTTPS.** Its API origin (`PUBLIC_ORIGIN` /
   `REFLEX_API_URL`, `GATEWAY_PUBLIC_URL`) and the gateway's `CORS_ORIGINS` are `http://...:8095`
   today; a page served over HTTPS that calls an `http://` API is blocked as mixed content. **This
   was not tested**: the proof stack ran the gateway and Caddy, not the elder app.
4. HSTS and a permanent redirect, only after (1).
5. `AUTH_MODE=jwt` for any deployment real people reach (`OPEN_QUESTIONS.md` #23, #32).

## Tests

`test_tls_internal.sh`: the real Caddy, the real Caddyfile, the real entrypoint, three fake
upstreams, its own docker network, no host ports. Cases: an IP, a hostname, and a certificate name
that is **not** the container's own address (as with docker port publishing). Checks: an
untrusting client is refused; a trusting one gets TLS 1.3; the certificate is valid for the
address; routing is identical (`/moderation` still unrouted); HTTP redirects to the published
port with path and query; the security headers are present and **HSTS and `Alt-Svc` are absent**;
a client that sends **no SNI** still gets the certificate; and a Caddyfile with no `:80 {` line is
refused (exit 3). Each rule has a sabotage check (a mutation that breaks it and must fail the
test).
