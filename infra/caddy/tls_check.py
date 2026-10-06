"""Client half of test_tls_internal.sh: runs INSIDE the test network.

usage: tls_check.py <host-or-ip> <root.crt>

Proves four things about the HTTPS endpoint, and prints one line each:
  1. a client that does NOT trust our root refuses the certificate (so the cert really is
     from the internal CA, not from some public CA, and not accepted blindly)
  2. a client that trusts the exported root completes the handshake
  3. the certificate is valid for the address we connected to (a SAN match, checked by the
     TLS library, not by eye)
  4. the same routes answer as on plain HTTP (/me/settings -> gateway, /moderation -> the
     elder-app catch-all, i.e. still NOT exposed), proving the transform did not change routing
Exits 0 only if every check passed.
"""

import http.client
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
    got = {p: get(p) for p in ("/me/settings", "/messages", "/moderation/queue", "/")}
    check("/me/settings reaches the gateway", got["/me/settings"].startswith("GATEWAY"), got["/me/settings"])
    check("/messages reaches the gateway", got["/messages"].startswith("GATEWAY"), got["/messages"])
    check("/moderation is still NOT routed to the gateway", got["/moderation/queue"].startswith("ELDER-APP"), got["/moderation/queue"])
    check("everything else reaches the elder-app", got["/"].startswith("ELDER-APP"), got["/"])
except Exception as e:  # noqa: BLE001
    check("routing checks ran", False, repr(e))

sys.exit(1 if failures else 0)
