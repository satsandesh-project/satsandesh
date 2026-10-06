#!/bin/sh
# Opt-in HTTPS for Caddy using its INTERNAL CA (see infra/caddy/README.md).
#
# It derives the HTTPS config from the one Caddyfile everybody already uses, instead of keeping
# a second copy of the routes that would drift: the site line `:80 {` becomes
# `<TLS_HOST> {` plus `tls internal`, and nothing else changes. If the Caddyfile no longer
# starts its site block with exactly `:80 {` the script STOPS rather than guess.
set -eu
: "${TLS_HOST:?TLS_HOST is required: the address or name browsers will use (e.g. the server IP)}"
SRC="${CADDYFILE_SRC:-/etc/caddy/Caddyfile}"
OUT=/tmp/Caddyfile.tls

awk -v host="$TLS_HOST" '
  !done && $0 == ":80 {" { print host " {"; print "\ttls internal"; done = 1; next }
  { print }
  END { if (!done) exit 3 }
' "$SRC" > "$OUT" || {
  echo "tls-internal-entrypoint: $SRC has no line that is exactly ':80 {' to turn into the TLS site; refusing to guess" >&2
  exit 3
}

exec caddy run --config "$OUT" --adapter caddyfile
