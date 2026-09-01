# Built from THIS directory as context (services/gateway/), not the repo
# root -- unlike gateway/spike-matrix-a and friends, this service has no
# dependency on anything outside its own tree (contracts/chat/ is
# currently unused by it, per docs/OWNERSHIP.md's Week 4 note).
FROM python:3.11-slim

WORKDIR /app

# requirements.txt is the fully-pinned lock file (direct + transitive) --
# see its own header comment. Docker builds install from this, not
# pyproject.toml, for a reproducible image, matching what
# docs/DECISIONS.md's "Dependency pinning" note says this file is for.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Apply migrations, then start the app. `alembic upgrade head` is
# idempotent (no-ops if already at head), safe to run on every container
# start rather than needing a separate one-shot migration step someone
# has to remember. DATABASE_URL/JWT_SECRET/VAPID_* are required with no
# default (app/config.py) -- a container started without them fails
# loudly here rather than starting into a broken state.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
