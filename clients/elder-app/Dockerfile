# Built from the repo root (see docker-compose.yml) is NOT required here,
# unlike gateway/spike-matrix-a -- this client has no dependency on
# backbone/interfaces.py or anything else outside its own directory. It
# only ever talks to the gateway over plain HTTP/WebSocket from the
# browser, never imports gateway code.
FROM python:3.11-slim

# Reflex needs Node.js for its frontend toolchain and unzip to install
# its own bundled bun runtime on first run.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-fetches bun and frontend deps at build time rather than on first
# container start -- keeps that (slow, network-dependent) step out of the
# request path for whoever brings this up.
#
# SSL_NO_VERIFY / ~/.curlrc "insecure": this dev machine's network fails
# TLS verification on every plain download in this step -- reflex's own
# fetch of bun's install script from raw.githubusercontent.com, AND that
# script's own subsequent `curl` call to github.com/oven-sh/bun's release
# zip (`unable to get local issuer certificate` both times). SSL_NO_VERIFY
# only covers reflex's own downloader, not the second curl invocation
# inside the installed script, hence both. This is the same class of
# TLS-interception symptom this session already hit with `git` itself
# (worked around there via a different mechanism, schannel) -- almost
# certainly a MITM-scanning proxy/antivirus on THIS machine's network, not
# a property of the app or its dependencies. Scoped to this one build
# step, not left as a runtime default. Worth removing entirely and
# retesting on a network that isn't intercepting TLS (e.g. the real
# staging server) -- likely unnecessary there.
RUN echo "insecure" > /root/.curlrc \
    && SSL_NO_VERIFY=1 reflex init \
    && rm -f /root/.curlrc

# Only 3000 -- --env prod (below) is Reflex's single-port mode: frontend
# and backend run as ONE process on ONE port, not the two separate ports
# (3000 frontend / 8000 backend) dev mode uses. Confirmed by reading
# reflex/reflex.py's _run()/_run_prod() (this project's pinned 0.9.9):
# for env=PROD, config._set_persistent(frontend_port=port,
# backend_port=port) sets BOTH to the same single port (defaults to 3000,
# reflex_base.constants.config.DefaultPorts.FRONTEND_PORT, since neither
# --frontend-port nor --backend-port nor rxconfig.py set one explicitly),
# and run_backend_prod(..., mount_frontend_compiled_app=True) mounts the
# compiled frontend directly into that one backend process. Verified
# directly, not just read: /ping returned 200 "pong" on :3000, while
# :8000 was connection-refused the entire time -- port 8000 is simply
# never bound in prod mode, by design, regardless of container/network
# config. An earlier version of this file ran --env dev instead as a
# workaround before this was root-caused (see the Week 4 prompt-journal
# entry) -- dev mode's separate 3000/8000 ports are what our healthcheck
# and infra/caddy/Caddyfile were originally written for, which is exactly
# why prod mode looked broken; both now point at the single real port.
EXPOSE 3000

CMD ["reflex", "run", "--env", "prod", "--backend-host", "0.0.0.0"]
