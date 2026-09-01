-- Runs automatically on first Postgres container boot (mounted into
-- /docker-entrypoint-initdb.d). Only runs against an empty data directory —
-- if you need to re-run it, `docker compose down -v` first to drop the
-- named volume.

CREATE TABLE schema_check (
    id SERIAL PRIMARY KEY,
    note TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO schema_check (note) VALUES ('db init ran');
