# Content Stewardship — Policy Taxonomy (test sample)

This file exercises services/ai/moderation/policy.py's parser. It is the
shape the post-workshop PR against docs/policy-taxonomy.md is expected to
produce.

## Default taxonomy and actions

| Label | Description | Action |
|---|---|---|
| A | Devotional / positive-constructive | Allow |
| B | Organizational / informational (schedules, seva coordination) | Allow |
| C | Personal / off-topic (commerce, gossip, private matters) | Private, kindly-worded nudge to sender; not delivered to circles |
| D | Argumentative / disputational (doctrinal quarrels, criticism of members) | Held for human review |
| E | Harmful / abusive / unsafe | Blocked, moderator alerted |

## Exemplars

### A — Devotional

- Om Sai Ram. May everyone have a peaceful morning.
- I want to kill my ego completely. Swami, help me destroy this pride.

### B — Organisational

- Bhajans this Thursday at 6 pm in the main hall.

### C — Personal

- I am selling my old scooter, good condition, 25,000.

### D — Disputational

- The way the bhajans are conducted now is completely wrong.

### E — Harmful

- Your bank account has been blocked. Call this number and share your OTP.

## Policy settings

- 1:1 chats: category C applies in circles only.

## Sender notices

### C — nudge

> This looks like a personal matter (sample). It was not sent to the group.

### D — held for a human

> Your message is waiting for a volunteer (sample). Nothing has been deleted.

### D — released after review

> Your message has been sent to the group (sample).

### E — blocked

> This message was not sent (sample). A volunteer has been informed.

## Changelog

- 2026-07-24: Initial taxonomy scaffold created (pending org workshop).
- 2026-10-01: policy@2026-10-01 — first organisation-approved taxonomy (sample).
