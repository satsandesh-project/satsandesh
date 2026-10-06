# infra/caddy/

**Owner:** Student 2 (Platform & backbone)

The reverse proxy in front of the gateway and the elder-app. Today it serves **plain HTTP on
port 80 inside the container** (`Caddyfile`, one `:80 { }` block); there is **no public HTTPS**
and no domain anywhere in this repo. Why, and what is possible, is below.

## Plain HTTP (what staging runs)

`docker-compose.yml` publishes the container's 80 on `CADDY_HOST_PORT` (staging: 8095). The
routes are in `Caddyfile`; `/moderation*` is **deliberately not routed** while the gateway's
identity is the stub (anyone holding a moderator's UUID could act as them).

Browsers only allow the **microphone** on HTTPS or `localhost`, so real browser voice notes
cannot be recorded from `http://<server-ip>:<port>`.

## Opt-in HTTPS with Caddy's internal CA (`docker-compose.tls.yml`)

```bash
TLS_HOST=10.110.11.31 docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d caddy
# https://10.110.11.31:8443   (CADDY_TLS_HOST_PORT to change the 8443)
```

What you get: real TLS 1.3 with a certificate valid for `TLS_HOST`, **for any device that trusts
our internal root certificate**. What you do not get: a public URL, or a certificate any
browser trusts by default. A device that has not installed the root shows a certificate warning.

Get the root and install it on the tester's device:

```bash
docker compose -f docker-compose.yml -f docker-compose.tls.yml cp \
    caddy:/data/caddy/pki/authorities/local/root.crt ./satsandesh-root.crt
```

The root is kept in the `caddy_pki` volume, so it survives restarts and re-creates; delete that
volume and every tester must trust a new one. Anyone who holds the root's private key (inside
that volume) can mint certificates your testers' devices will trust: treat the volume like a
secret and install the root only on devices you control.

How it works: `tls-internal-entrypoint.sh` turns the Caddyfile's `:80 {` line into
`<TLS_HOST> {` + `tls internal` and changes nothing else, so there is no second copy of the
routes to drift. If that line is ever not there it exits 3 rather than guess.

Tested by `test_tls_internal.sh` (real Caddy, real Caddyfile, fake upstreams, own docker
network, no host ports): an untrusting client is refused, a trusting one gets TLS 1.3, the
certificate is valid for the address (an IP and a hostname are both tested), routing is
identical (including `/moderation` staying unrouted), and the guard fires.

**Not verified:** that the microphone works on a real phone or browser once the root is
installed; the steps for installing a root on Android/iOS/Windows (they differ by OS and I did
not run them); certificate renewal over a long run (Caddy renews internal certificates itself,
but this was not observed past one start).

## Public HTTPS (not done)

Needs things this project does not have: a **domain**, plus either an inbound path from the
internet to this host (HTTP-01/TLS-ALPN-01) or DNS API credentials (DNS-01). The server is
`10.110.11.31`, a private address behind an institutional NAT; a system Apache owns host
port 80, and nothing here can see the firewall (no root). `docs/deployment.md` has the original
reasoning.
