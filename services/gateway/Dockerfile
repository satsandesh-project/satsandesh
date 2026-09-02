# Built from the REPO ROOT as context (see docker-compose.yml), not this
# directory. Corrects an earlier version of this file that assumed
# contracts/chat/ was unused here -- empirically wrong, caught by
# actually running the built image, not by re-reading the code more
# carefully: app/circles.py and app/messages.py both import from
# contracts.chat.*, which lives outside services/gateway/'s own tree.
# Locally, tests/conftest.py works around this by manually inserting the
# repo root onto sys.path -- that trick is pytest-only and does nothing
# for `uvicorn app.main:app` run directly, which is exactly what a
# container does. So both services/gateway/ and contracts/ need to be in
# the image, structured so contracts.* resolves without conftest.py's
# help.
FROM python:3.11-slim

WORKDIR /app

# requirements.txt is the fully-pinned lock file (direct + transitive) --
# see its own header comment. Docker builds install from this, not
# pyproject.toml, for a reproducible image, matching what
# docs/DECISIONS.md's "Dependency pinning" note says this file is for.
COPY services/gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app/, alembic/, alembic.ini etc. land directly in /app -- so `app.main`
# resolves the same way it does when running `uvicorn app.main:app` from
# inside services/gateway/ locally.
COPY services/gateway/ .
# contracts/ as a sibling of app/ inside /app, so `from contracts.chat...`
# resolves via the CWD-on-sys.path behaviour uvicorn/Python already give
# /app -- no separate PYTHONPATH env var needed, verified by actually
# running the built image, not assumed.
COPY contracts/ ./contracts/

EXPOSE 8000

# Apply migrations, then start the app. `alembic upgrade head` is
# idempotent (no-ops if already at head), safe to run on every container
# start rather than needing a separate one-shot migration step someone
# has to remember. DATABASE_URL/JWT_SECRET/VAPID_* are required with no
# default (app/config.py) -- a container started without them fails
# loudly here rather than starting into a broken state.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
