-- Delivery scheduling + dead-lettering, added after an audit found three
-- delivery bugs in the Week 2/3 dispatcher (all reproduced against real
-- Postgres before being fixed -- see tests/test_delivery_faults.py).
--
-- The core problem: an outbox row that could not be delivered stayed
-- status='pending' forever and was re-claimed on every single 200ms poll
-- cycle. Two consequences, both real:
--
--   1. Head-of-line blocking. The claim query is
--      "ORDER BY id ... LIMIT 50", so 50+ rows owed to one user who never
--      reconnects fill every batch permanently -- messages for users who
--      ARE online are never reached. Measured: 60 rows for an offline
--      user starved an online user's message across 5 full cycles.
--   2. A permanent busy loop: claim + UPDATE + COMMIT, 5x/second, forever,
--      for every stuck row.
--
-- next_attempt_at fixes both by letting a row say "not yet". The
-- dispatcher claims only rows that are actually due, so undeliverable
-- rows stop crowding out deliverable ones and stop hammering Postgres.

ALTER TABLE spike_outbox
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- Counts consecutive *send failures* (socket present, send raised), which
-- is a real fault worth giving up on eventually. Deliberately NOT the same
-- counter as `attempts`: attempts also increments when a recipient is
-- merely offline, and being offline must never dead-letter a message --
-- elders are offline for hours, and durable offline queueing is the whole
-- point of the outbox.
ALTER TABLE spike_outbox
    ADD COLUMN IF NOT EXISTS send_failures INT NOT NULL DEFAULT 0;

-- Supersedes idx_spike_outbox_status_id: the claim query now filters on
-- (status, next_attempt_at) and orders by id, so the old two-column index
-- can no longer serve it. Dropped rather than left behind, since an index
-- no query uses is pure write overhead.
CREATE INDEX IF NOT EXISTS idx_spike_outbox_claimable
    ON spike_outbox (status, next_attempt_at, id);
DROP INDEX IF EXISTS idx_spike_outbox_status_id;
