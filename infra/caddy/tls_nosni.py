"""Client half of test_tls_internal.sh's no-SNI check: runs INSIDE the test network.

usage: tls_nosni.py <connect-address> <expected-name> <root.crt>

A browser (or any client) that connects to an IP address sends NO server name (SNI): IP literals
are never put in the TLS ClientHello. Caddy then has to pick a certificate on its own, and by
default it uses the address the connection arrived on. Behind Docker's port publishing that is
the container's own address, not the address the browser typed, so without `default_sni` the
handshake fails with `tlsv1 alert internal error`. This connects the way a browser does and
checks the certificate is still served, chains to our root, and is valid for the expected name.
"""

import ipaddress
import socket
import ssl
import sys

addr, expected, root = sys.argv[1], sys.argv[2], sys.argv[3]
ctx = ssl.create_default_context(cafile=root)
ctx.check_hostname = False  # no name to check against: we compare the SAN ourselves below
ctx.verify_mode = ssl.CERT_REQUIRED  # but the chain to our root IS verified

try:
    with socket.create_connection((addr, 443), timeout=10) as raw:
        with ctx.wrap_socket(raw) as tls:  # no server_hostname -> no SNI
            cert = tls.getpeercert()
    sans = [v for _, v in cert.get("subjectAltName", ())]
    try:
        ipaddress.ip_address(expected)
        ok = expected in sans
    except ValueError:
        ok = expected in sans
    print(f"{'PASS' if ok else 'FAIL'}  a client that sends no SNI still gets the certificate  SANs={sans}")
    sys.exit(0 if ok else 1)
except Exception as e:  # noqa: BLE001
    print(f"FAIL  a client that sends no SNI still gets the certificate  {e!r}")
    sys.exit(1)
