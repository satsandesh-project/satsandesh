#!/bin/sh
# Opt-in HTTPS for Caddy using its INTERNAL CA (see infra/caddy/README.md).
#
# It derives the config from the one Caddyfile everybody already uses, instead of keeping a
# second copy of the routes that would drift or editing the file staging serves today: every
# route and every comment in it reaches the HTTPS site byte for byte. The generated config is:
#   1. a global `auto_https disable_redirects`, so the redirect below is the ONLY redirect and
#      points at the port browsers really reach HTTPS on (Caddy's own redirect would use the
#      default 443, which this stack does not publish);
#   2. a `:80` site that REDIRECTS to https://<TLS_HOST>[:<TLS_PUBLIC_PORT>]<path+query>. It is
#      302, not 301/308, on purpose: browsers cache permanent redirects, and this certificate
#      comes from an internal CA that a tester may not have installed yet;
#   3. the Caddyfile's own site, its line `:80 {` turned into `<TLS_HOST> {` + `tls internal` +
#      response headers worth having at a boundary.
# There is deliberately NO Strict-Transport-Security header: HSTS pins the site to HTTPS in the
# browser for a long time, and with a certificate from an internal CA that would lock out every
# tester who has not (yet) trusted the root. Add it only once the certificate is publicly trusted.
# If the Caddyfile has no line that is exactly `:80 {` the script STOPS rather than guess.
set -eu
: "${TLS_HOST:?TLS_HOST is required: the address or name browsers will use (e.g. the server IP)}"
PORT="${TLS_PUBLIC_PORT:-8443}"
SRC="${CADDYFILE_SRC:-/etc/caddy/Caddyfile}"
OUT=/tmp/Caddyfile.tls

if ! grep -qx ':80 {' "$SRC"; then
  echo "tls-internal-entrypoint: $SRC has no line that is exactly ':80 {' to turn into the TLS site; refusing to guess" >&2
  exit 3
fi

if [ "$PORT" = "443" ]; then TARGET="https://$TLS_HOST"; else TARGET="https://$TLS_HOST:$PORT"; fi

{
  printf '{\n\tauto_https disable_redirects\n}\n\n'
  printf ':80 {\n\tredir %s{uri} 302\n}\n\n' "$TARGET"
  awk -v host="$TLS_HOST" '
    !done && $0 == ":80 {" {
      print host " {"
      print "\ttls internal"
      print "\theader {"
      print "\t\tX-Content-Type-Options nosniff"
      print "\t\tReferrer-Policy strict-origin-when-cross-origin"
      print "\t\tX-Frame-Options SAMEORIGIN"
      print "\t\t-Server"
      print "\t}"
      done = 1
      next
    }
    { print }
  ' "$SRC"
} > "$OUT"

exec caddy run --config "$OUT" --adapter caddyfile
