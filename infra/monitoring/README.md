# infra/monitoring/

**Owner:** Student 2 (Platform & backbone)

`healthwatch.sh` -- a lightweight watchdog for the shared staging
deployment (`liveapp-*`). Polls each container's Docker healthcheck
status, logs and (best-effort) alerts on anything unhealthy, and
attempts one restart before escalating. Built after a real incident
where `liveapp-gateway-1` sat unhealthy for hours with nobody noticing --
see `docs/prompt-journal.md`'s entries around 2026-09-02/03 for the full
story, including why this is a real host-level rootless-Docker-without-
cgroups issue, not an application bug (`docker info` on that host warns
"Running in rootless-mode without cgroups"; fixing that properly needs
root, which the deployment account does not have).

## Setup (run once, on the host, as the account that runs the deployment)

```bash
chmod +x infra/monitoring/healthwatch.sh

# Every 5 minutes. Adjust CONTAINERS/LOG_FILE inline if this host's
# project name or container set differs from the defaults in the script.
( crontab -l 2>/dev/null; \
  echo "*/5 * * * * $HOME/satsandesh/infra/monitoring/healthwatch.sh" \
) | crontab -
```

No root required -- `crontab` is per-user, and `docker inspect`/`docker
restart` work the same as any other `docker` command under rootless
Docker.

## Checking it

```bash
tail -f ~/healthwatch.log        # heartbeat + any ALERT lines
grep ALERT ~/healthwatch.log     # just the incidents
crontab -l                       # confirm the schedule is actually installed
```

## Optional: a real alert channel

Unset by default -- the log file alone is the alert until this is wired
up. Set `ALERT_WEBHOOK_URL` (a Slack or Discord incoming-webhook URL) in
the crontab line's environment (or a wrapper script) to also get a
one-line POST there when something goes unhealthy. No script change
needed either way.

## What this does not fix

This catches a recurrence fast; it does not fix the underlying cause.
The real fix -- enabling proper cgroup v2 delegation for the rootless
Docker user -- needs root on this host, which nobody currently has. If
that access becomes available, see the PR #26 discussion thread for
what the actual fix likely involves (`dockerd-rootless-setuptool.sh
install` plus cgroup v2 delegation, roughly).
