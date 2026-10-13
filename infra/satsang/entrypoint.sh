#!/bin/sh
# Renders the nginx config from the template and runs nginx in the foreground.
set -eu
: "${SATSANG_HOOK_SECRET:?set SATSANG_HOOK_SECRET (the same value the gateway has)}"
# The secret goes into a URL in the config: refuse anything that would need escaping there.
case "$SATSANG_HOOK_SECRET" in
  *[!A-Za-z0-9._~-]*) echo "SATSANG_HOOK_SECRET may only contain A-Z a-z 0-9 . _ ~ -" >&2; exit 2 ;;
esac
[ "${#SATSANG_HOOK_SECRET}" -ge 16 ] || { echo "SATSANG_HOOK_SECRET is shorter than 16 characters" >&2; exit 2; }
sed -e "s|@HOOK_SECRET@|$SATSANG_HOOK_SECRET|g" \
    -e "s|@HLS_FRAGMENT@|${SATSANG_HLS_FRAGMENT:-4s}|" \
    -e "s|@HLS_PLAYLIST@|${SATSANG_HLS_PLAYLIST:-32s}|" \
    /etc/nginx/satsang.conf.template > /tmp/satsang.conf
chmod 600 /tmp/satsang.conf
nginx -t -c /tmp/satsang.conf >/dev/null
exec nginx -c /tmp/satsang.conf -g "daemon off;"
