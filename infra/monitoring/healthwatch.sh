#!/usr/bin/env bash
# Lightweight healthcheck watchdog for the shared staging deployment.
#
# Born from a real incident (2026-09-02/03, see docs/prompt-journal.md):
# liveapp-gateway-1 went unhealthy and sat that way for hours, unnoticed,
# because nothing was watching Docker's own healthcheck status between
# whenever someone happened to look. Docker already tracks this correctly
# (`docker inspect --format '{{.State.Health.Status}}'`) -- this script's
# only job is to actually look, on a schedule, and make noise + attempt
# recovery when something's wrong, instead of relying on a person to
# notice.
#
# Deliberately simple: no external monitoring service, no new
# infrastructure to run. Meant to be driven by cron (see README.md in
# this directory for the crontab line) on the same account that already
# runs the deployment -- no root needed, matches how the rest of this
# project's deploy tooling works on this host.
#
# What it does, once per invocation:
#   1. Check Docker's own health status for each container in
#      $CONTAINERS (space-separated, default below).
#   2. Any container that isn't "healthy" -- append a timestamped ALERT
#      line to $LOG_FILE, restart it, then log whether the restart
#      actually restored a healthy state after a short wait.
#   3. One summary heartbeat line per run, always -- "N/M healthy" --
#      regardless of whether anything needed an ALERT. This is what
#      actually lets `tail -f`/a gap in timestamps prove cron is still
#      running at all, as opposed to grepping for ALERT lines that only
#      ever appear when something's already wrong (caught in review on
#      #28: the first version logged nothing on a clean run, which is
#      indistinguishable from cron not running).
#
# Optional: set ALERT_WEBHOOK_URL (a Slack/Discord incoming-webhook URL)
# to also POST a one-line message there when an ALERT fires. Unset by
# default -- the log file alone is the alert until the team wires one up;
# this script works identically either way, so adding a webhook later is
# a config change, not a script change.
set -uo pipefail

# liveapp-matrix-circle-service-1 and liveapp-tuwunel-1 removed
# 2026-09-05 along with ADR 0002's superseded Matrix decision -- see
# docker-compose.yml and docs/adr/0002-chat-backbone.md's "Update"
# section. Left in CONTAINERS, they'd permanently ALERT as "not found"
# once the live deployment picks up that removal, for containers that
# were deliberately retired, not actually down.
CONTAINERS="${CONTAINERS:-liveapp-gateway-1 liveapp-elder-app-1 liveapp-ai-services-1 liveapp-postgres-1}"
LOG_FILE="${LOG_FILE:-$HOME/healthwatch.log}"
RESTART_RECHECK_DELAY="${RESTART_RECHECK_DELAY:-15}"

log() {
  printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >> "$LOG_FILE"
}

alert() {
  local msg="ALERT: $1"
  log "$msg"
  if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
    # Slack- and Discord-compatible webhook body shape (both accept a
    # bare {"text": ...} / {"content": ...} JSON payload on their
    # incoming-webhook URLs); best-effort, failure here must not stop
    # the restart attempt below.
    curl -fsS -X POST -H 'Content-Type: application/json' \
      -d "{\"text\": \"$msg\", \"content\": \"$msg\"}" \
      "$ALERT_WEBHOOK_URL" >/dev/null 2>&1 || log "webhook POST failed (non-fatal)"
  fi
}

healthy_count=0
total_count=0

for container in $CONTAINERS; do
  total_count=$((total_count + 1))

  if ! docker inspect "$container" >/dev/null 2>&1; then
    alert "$container: not found (stopped, removed, or renamed?)"
    continue
  fi

  status=$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' 2>/dev/null)

  if [ "$status" = "healthy" ] || [ "$status" = "no-healthcheck" ]; then
    healthy_count=$((healthy_count + 1))
    continue
  fi

  streak=$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.FailingStreak}}{{else}}0{{end}}' 2>/dev/null)
  alert "$container: status=$status FailingStreak=$streak -- restarting"

  docker restart "$container" >/dev/null 2>&1
  sleep "$RESTART_RECHECK_DELAY"

  new_status=$(docker inspect "$container" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' 2>/dev/null)
  if [ "$new_status" = "healthy" ] || [ "$new_status" = "starting" ]; then
    healthy_count=$((healthy_count + 1))
    log "$container: restart recovered it (status=$new_status)"
  else
    alert "$container: restart did NOT recover it (status=$new_status) -- needs a human"
  fi
done

log "heartbeat: $healthy_count/$total_count healthy"
