-- Spike B schema. Deliberately separate from db/init/001_init.sql: this
-- code is exploratory and prefixed spike_* so it can be dropped wholesale
-- without touching the Week 1 verified stack. Applied idempotently by
-- db.ensure_schema() on app startup (CREATE TABLE/INDEX IF NOT EXISTS) --
-- there's no migration runner here, on purpose: this is a spike, not
-- production, and adding one would be answering a question nobody asked
-- yet ("how do we manage schema migrations long-term").

CREATE TABLE IF NOT EXISTS spike_messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    sender_id       TEXT NOT NULL,
    body            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spike_outbox (
    id            BIGSERIAL PRIMARY KEY,
    message_id    BIGINT NOT NULL REFERENCES spike_messages(id),
    recipient_id  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at  TIMESTAMPTZ
);

-- The dispatcher's entire claim query is
-- "WHERE status = 'pending' ORDER BY id FOR UPDATE SKIP LOCKED" --
-- this index is what keeps that query cheap as spike_outbox grows.
CREATE INDEX IF NOT EXISTS idx_spike_outbox_status_id
    ON spike_outbox (status, id);
