# infra/backups/

**Owner:** Student 2 (Platform & backbone)

Backup scripts: nightly encrypted PostgreSQL dumps (pg_dump) plus restic
for media/state backup. Original voice recordings auto-purge after a
configurable retention (default 30 days) as part of data minimization —
see the project proposal, Ethics & Privacy section.

Do not commit real dumps or media here — see .gitignore.

Status: not yet started.
