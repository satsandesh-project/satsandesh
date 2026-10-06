#!/bin/sh
# Opt-in HTTPS for Caddy using its INTERNAL CA (see infra/caddy/README.md).
#
# It derives the config from the one Caddyfile everybody already uses, instead of keeping a
# second copy of the routes that would drift. The generated config is:
#   1. a global `auto_https disable_redirects`, because otherwise Caddy turns port 80 into a
#      redirect to https://<host>/ on the DEFAULT port 443 -- a port this stack does not
#      publish -- and everyone already using the plain-HTTP URL would land on a dead address;
#   2. the Caddyfile exactly as it is (the `:80 { }` site, so plain HTTP keeps working);
#   3. a copy of it whose site line `:80 {` is `<TLS_HOST> {` + `tls internal`.
# If the Caddyfile has no line that is exactly `:80 {` the script STOPS rather than guess.
set -eu
: "${TLS_HOST:?TLS_HOST is required: the address or name browsers will use (e.g. the server IP)}"
SRC="${CADDYFILE_SRC:-/etc/caddy/Caddyfile}"
OUT=/tmp/Caddyfile.tls

if ! grep -qx ':80 {' "$SRC"; then
  echo "tls-internal-entrypoint: $SRC has no line that is exactly ':80 {' to turn into the TLS site; refusing to guess" >&2
  exit 3
fi

{
  printf '{\n\tauto_https disable_redirects\n}\n\n'
  cat "$SRC"
  printf '\n'
  awk -v host="$TLS_HOST" '
    !done && $0 == ":80 {" { print host " {"; print "\ttls internal"; done = 1; next }
    { print }
  ' "$SRC"
} > "$OUT"

exec caddy run --config "$OUT" --adapter caddyfile
