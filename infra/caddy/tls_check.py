"""Client half of test_tls_internal.sh: runs INSIDE the test network.

usage: tls_check.py <host-or-ip> <root.crt>

Proves four things about the HTTPS endpoint, and prints one line each:
  1. a client that does NOT trust our root refuses the certificate (so the cert really is
     from the internal CA, not from some public CA, and not accepted blindly)
  2. a client that trusts the exported root completes the handshake
  3. the certificate is valid for the address we connected to (a SAN match, checked by the
     TLS library, not by eye)
  4. the same routes answer as on plain HTTP (/me/settings and /moderation -> gateway, everything
     else -> the elder-app catch-all), proving the transform did not change routing
Exits 0 only if every check passed.
"""

import http.client
import os
import socket
import ssl
import sys

host, root = sys.argv[1], sys.argv[2]
failures = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        failures.append(name)


# 1. default trust store must refuse
try:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=10) as raw:
        ctx.wrap_socket(raw, server_hostname=host)
    check("untrusting client refuses the internal-CA certificate", False, "it connected")
except ssl.SSLCertVerificationError as e:
    check("untrusting client refuses the internal-CA certificate", True, f"({e.verify_message})")
except Exception as e:  # noqa: BLE001
    check("untrusting client refuses the internal-CA certificate", False, f"unexpected: {e!r}")

# 2 + 3. trusting the exported root: handshake, and hostname/IP verification stays ON
ctx = ssl.create_default_context(cafile=root)
ctx.check_hostname = True
try:
    with socket.create_connection((host, 443), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
            check("trusting client completes the handshake", True, f"({tls.version()})")
            sans = [v for _, v in cert.get("subjectAltName", ())]
            check("certificate is valid for the address connected to", host in sans, f"SANs={sans}")
except Exception as e:  # noqa: BLE001
    check("trusting client completes the handshake", False, repr(e))
    check("certificate is valid for the address connected to", False, "no handshake")


# 4. routing unchanged
def get(path):
    conn = http.client.HTTPSConnection(host, 443, context=ctx, timeout=10)
    conn.request("GET", path)
    return conn.getresponse().read().decode()


try:
    got = {
        p: get(p)
        for p in (
            "/me/settings",
            "/messages",
            "/moderation/queue",
            "/moderation/messages/x/events",
            "/",
        )
    }
    check(
        "/me/settings reaches the gateway",
        got["/me/settings"].startswith("GATEWAY"),
        got["/me/settings"],
    )
    check("/messages reaches the gateway", got["/messages"].startswith("GATEWAY"), got["/messages"])
    check(
        "/moderation/queue reaches the gateway (which refuses unsigned callers itself)",
        got["/moderation/queue"].startswith("GATEWAY"),
        got["/moderation/queue"],
    )
    check(
        "/moderation/messages/<id>/events reaches the gateway",
        got["/moderation/messages/x/events"].startswith("GATEWAY"),
        got["/moderation/messages/x/events"],
    )
    check("everything else reaches the elder-app", got["/"].startswith("ELDER-APP"), got["/"])
except Exception as e:  # noqa: BLE001
    check("routing checks ran", False, repr(e))

# 5. plain HTTP must REDIRECT to HTTPS (never serve the app in cleartext), to the port browsers
#    actually reach HTTPS on (the published one, not the default 443), keeping path and query.
#    Temporary (302) on purpose: a permanent redirect is cached by browsers, and this certificate
#    comes from an internal CA that testers may not have installed yet.
port = os.environ.get("EXPECT_TLS_PORT", "8443")
suffix = "" if port == "443" else f":{port}"
try:
    plain = http.client.HTTPConnection(host, 80, timeout=10)
    plain.request("GET", "/me/settings?x=1")
    resp = plain.getresponse()
    resp.read()
    want = f"https://{host}{suffix}/me/settings?x=1"
    check(
        "plain HTTP redirects to HTTPS (302), keeping path and query",
        resp.status == 302 and resp.getheader("Location") == want,
        f"status={resp.status} location={resp.getheader('Location')} want={want}",
    )
except Exception as e:  # noqa: BLE001
    check("plain HTTP redirects to HTTPS (302), keeping path and query", False, repr(e))

# 6. headers worth having at a boundary; and NO HSTS (an internal-CA certificate must not be
#    pinned in testers' browsers: HSTS would lock them out of the site until it expires)
try:
    conn = http.client.HTTPSConnection(host, 443, context=ctx, timeout=10)
    conn.request("GET", "/me/settings")
    r = conn.getresponse()
    r.read()
    h = {k.lower(): v for k, v in r.getheaders()}
    check(
        "X-Content-Type-Options: nosniff",
        h.get("x-content-type-options") == "nosniff",
        str(h.get("x-content-type-options")),
    )
    check(
        "Referrer-Policy: strict-origin-when-cross-origin",
        h.get("referrer-policy") == "strict-origin-when-cross-origin",
        str(h.get("referrer-policy")),
    )
    check(
        "X-Frame-Options: SAMEORIGIN",
        h.get("x-frame-options") == "SAMEORIGIN",
        str(h.get("x-frame-options")),
    )
    check("the Server header is removed", "server" not in h, str(h.get("server")))
    # Caddy would advertise HTTP/3 on :443 (Alt-Svc), a UDP port this stack does not publish:
    # browsers would try it, time out, and fall back. HTTP/3 is switched off instead.
    check("no Alt-Svc advertising HTTP/3", "alt-svc" not in h, str(h.get("alt-svc")))
    check(
        "NO Strict-Transport-Security",
        "strict-transport-security" not in h,
        str(h.get("strict-transport-security")),
    )
except Exception as e:  # noqa: BLE001
    check("header checks ran", False, repr(e))

sys.exit(1 if failures else 0)
