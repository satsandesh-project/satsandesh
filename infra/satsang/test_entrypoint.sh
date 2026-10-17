#!/usr/bin/env bash
# Real test of infra/satsang/entrypoint.sh and nginx.conf.template: builds the REAL image and runs the REAL
# entrypoint (nginx -t is the real nginx with the real RTMP module). Binds no host ports, touches no stack.
# Run on any host with docker:   infra/satsang/test_entrypoint.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG=satsang-entrypoint-test:$$
status=0
trap 'docker rmi -f "$IMG" >/dev/null 2>&1' EXIT
docker build -q -t "$IMG" "$HERE" >/dev/null || { echo "FAIL  the image did not build"; exit 1; }

ok() { echo "PASS  $1"; }
bad() { echo "FAIL  $1 -- $2"; status=1; }
# render <env...> : runs the entrypoint with `nginx` replaced by cat, so we see the rendered config
render() { docker run --rm --entrypoint /bin/sh "$@" "$IMG" -c \
  'sed -i "s#^exec nginx.*#cat /tmp/satsang.conf#" /entrypoint.sh && /bin/sh /entrypoint.sh'; }
SECRET=abcdefghijklmnop1234

out=$(docker run --rm "$IMG" 2>&1); rc=$?
[ $rc -ne 0 ] && ok "no SATSANG_HOOK_SECRET: refuses to start (exit $rc)" || bad "no secret" "it started"
out=$(docker run --rm -e SATSANG_HOOK_SECRET='short' "$IMG" 2>&1); rc=$?
[ $rc = 2 ] && ok "a secret under 16 characters: exit 2" || bad "short secret" "exit $rc: $out"
out=$(docker run --rm -e SATSANG_HOOK_SECRET='abcdefghijklmnop&evil=1' "$IMG" 2>&1); rc=$?
[ $rc = 2 ] && ok "a secret needing URL escaping: exit 2" || bad "unescaped secret" "exit $rc: $out"

conf=$(render -e SATSANG_HOOK_SECRET=$SECRET); rc=$?
[ $rc = 0 ] && ok "a good secret renders a config" || bad "render" "exit $rc"
# nginx -t on the rendered file is the last line of the real entrypoint before exec; run it for real
docker run --rm -e SATSANG_HOOK_SECRET=$SECRET --entrypoint /bin/sh "$IMG" -c \
  'sed -i "s#^exec nginx.*#true#" /entrypoint.sh && /bin/sh /entrypoint.sh && nginx -t -c /tmp/satsang.conf' >/dev/null 2>&1 \
  && ok "the rendered config passes the real nginx -t (with the RTMP module loaded)" || bad "nginx -t" "failed"

echo "$conf" | grep -q '@[A-Z_]*@' && bad "placeholders" "an @...@ is left in the rendered config" || ok "no @placeholder@ is left unfilled"
n=$(echo "$conf" | grep -c "secret=$SECRET")
[ "$n" = 2 ] && ok "the secret appears exactly twice, both in the loopback proxy" || bad "secret placement" "$n occurrences"
echo "$conf" | grep -E '^\s*on_publish(_done)? ' | grep -q "$SECRET" \
  && bad "rtmp callbacks" "the secret sits in an on_publish line" || ok "the rtmp on_publish lines carry no secret"
echo "$conf" | grep -q '^error_log /dev/stderr warn;' && ok "error_log is warn (info would log the stream key)" || bad "error_log" "not warn"
echo "$conf" | grep -q 'hls_fragment 4s;' && echo "$conf" | grep -q 'hls_playlist_length 32s;' \
  && ok "defaults: 4 s fragments, 32 s playlist" || bad "defaults" "not 4s/32s"
c2=$(render -e SATSANG_HOOK_SECRET=$SECRET -e SATSANG_HLS_FRAGMENT=2s -e SATSANG_HLS_PLAYLIST=20s)
echo "$c2" | grep -q 'hls_fragment 2s;' && echo "$c2" | grep -q 'hls_playlist_length 20s;' \
  && ok "the fragment and playlist length can be overridden" || bad "override" "not applied"
echo "$conf" | grep -q 'drop_idle_publisher 30s;' && ok "a publisher silent for 30 s is dropped by default" \
  || bad "drop_idle_publisher" "no 'drop_idle_publisher 30s;' in the rendered config"
c3=$(render -e SATSANG_HOOK_SECRET=$SECRET -e SATSANG_DROP_IDLE_PUBLISHER=90s)
echo "$c3" | grep -q 'drop_idle_publisher 90s;' && ok "the idle limit can be overridden" || bad "drop override" "not applied"
echo "$conf" | grep -q 'deny play all;' && ok "playing RTMP directly is denied" || bad "deny play" "missing"

echo; [ $status = 0 ] && echo "ALL PASSED" || echo "SOME FAILED"
exit $status
