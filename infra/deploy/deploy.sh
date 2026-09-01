#!/usr/bin/env bash
# Staging deploy script for a bare Ubuntu host reached by raw IP (no
# domain yet -- see docs/deployment.md for why HTTPS is deliberately
# deferred). Run ON the remote host, as a user with sudo, from the repo
# root after cloning:
#
#   git clone <repo-url> satsandesh && cd satsandesh
#   scp your-local/.env <host>:satsandesh/.env   # do this BEFORE running
#   ./infra/deploy/deploy.sh
#
# Idempotent: safe to re-run after a `git pull` to pick up code changes
# (it does not touch data volumes).
set -euo pipefail

if [ ! -f .env ]; then
  echo "Missing .env -- copy your production .env to the repo root first" \
       "(see .env.example for the required keys). Refusing to start with" \
       "no secrets configured." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker Engine + Compose plugin..."
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Docker installed. Log out and back in (or run 'newgrp docker')" \
       "for the group change to take effect, then re-run this script." >&2
  exit 0
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose plugin not found -- get.docker.com's installer" \
       "above should have included it. Check the Docker installation." >&2
  exit 1
fi

echo "Configuring firewall (ufw): SSH + HTTP only..."
if command -v ufw >/dev/null 2>&1; then
  sudo ufw allow OpenSSH
  sudo ufw allow 80/tcp
  # Deliberately NOT opening 5432 (Postgres), 8008 (Tuwunel), or 8101
  # (matrix-circle-service) -- those host port publishes in
  # docker-compose.yml exist for local dev convenience (manual poking
  # during a spike) and have no reason to be reachable from the public
  # internet on a staging box. Caddy on :80 is the only intended entry
  # point; everything else talks over the compose-internal network.
  sudo ufw --force enable
  sudo ufw status verbose
else
  echo "ufw not found -- configure your firewall manually: allow 22 and" \
       "80 only, deny everything else inbound by default." >&2
fi

echo "Pulling images / building services..."
docker compose build

echo "Starting the base stack (gateway, elder-app, ai-services, caddy," \
     "postgres) plus the matrix profile (tuwunel, matrix-circle-service)" \
     "-- ADR 0002 decided Matrix as the real backbone, not the base" \
     "stack's placeholder default..."
docker compose --profile matrix up -d

echo "Waiting for healthchecks..."
for i in $(seq 1 30); do
  unhealthy=$(docker compose ps --format '{{.Name}} {{.Status}}' \
    | grep -v "healthy" | grep -v "Up.*ago$" || true)
  if [ -z "$unhealthy" ]; then
    echo "All services healthy."
    break
  fi
  sleep 5
done

docker compose ps
echo
echo "Deploy complete. Verify from another machine: curl -i http://<this-host-ip>/health"
echo "Record the real IP in docs/server-notes.md (gitignored) -- do not commit it."
