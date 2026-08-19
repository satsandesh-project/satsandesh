-- Circles (groups) + memberships, Week 3.
--
-- NOTE ON DUPLICATION: Week 2's "data model + migrations" task (a
-- canonical users/circles/memberships schema, Student 3) has not landed
-- in this repo -- searched, not present on main or anywhere in the tree.
-- Rather than block, this defines the minimum tables circles need, under
-- the same spike_ prefix as 001 so it stays droppable alongside the rest
-- of the spike. If a canonical schema arrives later these two WILL need
-- reconciling; that's recorded in docs/work-breakdown.md's Week 3 section
-- rather than left to be discovered in a merge conflict.
--
-- There is deliberately no spike_users table. Nothing here needs one:
-- user ids are caller-asserted strings (there is no auth yet), and
-- inventing a users table would be inventing an identity model that
-- belongs to whoever owns the canonical schema, not to this spike.

CREATE TABLE IF NOT EXISTS spike_circles (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spike_circle_members (
    circle_id BIGINT NOT NULL REFERENCES spike_circles(id) ON DELETE CASCADE,
    user_id   TEXT NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Composite PK, not a surrogate id: it makes "user X is in circle Y"
    -- unique by construction, so add_member can be idempotent via
    -- ON CONFLICT DO NOTHING instead of a check-then-insert race.
    PRIMARY KEY (circle_id, user_id)
);

-- list_members(circle_id) and the fan-out in post_announcement both read
-- members by circle; the composite PK above already indexes that prefix,
-- so no extra index is needed for it.

-- A circle post reuses spike_messages exactly as-is: conversation_id
-- carries the circle id. That's the whole reason circles were cheap to
-- add -- "one message, many recipients" is what the Week 2 outbox
-- already did and already tested, so nothing about delivery is rebuilt
-- here. This index makes list_messages(circle_id) cheap.
CREATE INDEX IF NOT EXISTS idx_spike_messages_conversation_id
    ON spike_messages (conversation_id, id DESC);
