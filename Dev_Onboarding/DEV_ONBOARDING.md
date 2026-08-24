# Developer Onboarding — SatSandesh

This guide takes a new contributor from a fresh clone to a working local
development environment, and explains how the project is structured and how
contributions are made.

## 1. Project Overview

SatSandesh is a private, invitation-only messaging platform for a spiritual
community. It combines familiar chat functionality with four differentiating
capabilities: values-aligned content stewardship, a multilingual voice
translation bridge, first-class satsang and bhajan sessions, and an
elder-first user experience.

The guiding engineering principle is to assemble mature open-source
components rather than build core infrastructure from scratch. Message
delivery, speech recognition, translation, speech synthesis, and live audio
are all provided by established open-source projects; development effort is
concentrated on the parts that make the product distinctive.

New contributors should read the full project proposal before their first
contribution — it covers the problem statement, objectives, and design
rationale in more depth than this document does.

## 2. Prerequisites

Install the following before setting up the project:

| Tool | Purpose |
|---|---|
| **Python 3.11** | The stack is Python end-to-end — application backend, gateway, and AI services all run on Python |
| **Docker and Docker Compose** | The full stack (database, gateway, AI services, live audio, reverse proxy) is defined and run as containers |
| **Git** | Version control |
| **pre-commit** | Runs linting and formatting checks automatically before each commit |
| **Claude Code** | The team's AI-assisted development tool — see `CLAUDE.md` in the repository root for conventions on how it is used in this project |

A code editor with Python and Docker support is recommended. A PostgreSQL
client (e.g. `psql`, TablePlus, or DBeaver) is useful for inspecting the
database directly during development.

## 3. Getting Started

Clone the repository and install the git hooks and dependencies:

```bash
git clone <repo-url>
cd satsandesh-main

pre-commit install
pip install -e .
```

Confirm the environment is working correctly before making any changes:

```bash
ruff check .
ruff format --check .
pytest
```

All three commands should complete without errors on a clean checkout.

## 4. Environment Configuration

The project uses a `.env` file for local configuration and secrets. Copy the
provided template and fill in local values:

```bash
cp .env.example .env
```

`.env` is excluded from version control and must never be committed.
`.env.example` should always contain placeholder values only, and should be
kept up to date as new configuration is introduced.

Typical configuration values, in line with the system architecture described
in the project proposal, include:

```
DATABASE_URL=postgresql://...
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=8000
WEB_PUSH_VAPID_PUBLIC_KEY=...
WEB_PUSH_VAPID_PRIVATE_KEY=...
```

Local development should always use dummy or non-sensitive values. Real
credentials and any pilot or user data must never appear in a local `.env`
file or be committed to the repository.

## 5. Running the Stack Locally

The application is designed to run as a single Docker Compose deployment,
consistent with the architecture described in the project proposal. To start
the full local environment:

```bash
docker compose up
```

This brings up the core services described in the system architecture:

- **Database** — PostgreSQL, storing users, messages, moderation events, and
  session state
- **Gateway** — the FastAPI service handling authentication, routing, and
  WebSocket connections
- **AI services** — the speech recognition, translation, synthesis, and
  moderation pipeline, run as a separate service from the client
- **Live audio** — the services supporting satsang broadcast and bhajan
  sessions
- **Reverse proxy** — handling HTTPS termination

To stop the environment:

```bash
docker compose down
```

Add the `-v` flag to also remove local data volumes for a clean reset. Use
this with care, as it deletes all local database contents.

## 6. Project Structure

```
satsandesh-main/
├── CLAUDE.md              # AI-assisted development conventions
├── README.md              # Project overview
├── docs/
│   ├── CONVENTIONS.md     # Branching, PR, and review conventions
│   └── DEV_ONBOARDING.md  # This document
├── tests/                 # Automated test suite
├── pyproject.toml         # Tooling and dependency configuration
├── .pre-commit-config.yaml
├── .github/workflows/     # Continuous integration
├── docker-compose.yml     # Local and deployment orchestration
├── .env.example           # Environment variable template
├── gateway/                # FastAPI gateway: auth, routing, WebSocket fan-out
├── backbone/                # Chat message storage, delivery, and sync
├── ai-services/               # Speech recognition, translation, synthesis, moderation
├── live/                       # Satsang broadcast and bhajan room services
├── client/                     # Elder application, onboarding flow, and admin console
└── contracts/                  # Interfaces shared across services
```

This structure reflects the system architecture described in the project
proposal: a client layer, a gateway, and three backend service groups
(chat backbone, AI services, and live audio), backed by a shared database.
Directory names may evolve as the codebase develops; consult the current
repository layout if this document appears out of date.

## 7. Development Workflow

Full conventions are documented in `docs/CONVENTIONS.md`. In summary:

1. Always branch from an up-to-date `main`.
2. Use descriptive branch names in the form `<type>/<short-description>`
   (`feat/`, `fix/`, `chore/`, `docs/`, `refactor/`).
3. Keep commits small and focused on a single concern.
4. Open a pull request for every change, regardless of size.
5. Ensure CI passes and the change is reviewed before merging.
6. Every pull request should be reviewed and understood by at least one
   other contributor before merging — no change should merge unread.
7. Stay within the relevant service or module; changes to shared interfaces
   or configuration should be coordinated with the rest of the team before
   being made.
8. Delete branches after merging.

## 8. Testing and Verification

Before opening a pull request, run the same checks that continuous
integration will run:

```bash
ruff check .
ruff format --check .
pytest
```

Tests should be written alongside — and ideally before — the implementation
they cover. New functionality should not be merged without corresponding
test coverage.

## 9. Security Practices

All contributions should follow these baseline practices, consistent with
the ethics and privacy commitments described in the project proposal:

- Every route or endpoint enforces authorization; nothing is trusted by
  default.
- No secrets, credentials, or tokens appear in source code, logs, or commit
  history.
- All database queries are parameterized; no query is built by string
  concatenation.
- File and media uploads enforce explicit size and type limits.
- New dependencies are checked for known vulnerabilities before being added.

## 10. Troubleshooting

| Issue | Suggested Resolution |
|---|---|
| Git hooks not running on commit | Re-run `pre-commit install`; it must be run once per clone |
| `ruff format --check` fails unexpectedly | Run `ruff format .` to apply formatting automatically, then review the diff before committing |
| Docker Compose fails to start | Confirm `.env` is present and populated, and that no other process is using the required ports |
| Tests pass locally but fail in continuous integration | Confirm the local Python version matches the version pinned in `pyproject.toml`, and that no required environment variable is missing from `.env.example` |

## 11. Further Reading

- The full project proposal — problem statement, objectives, system
  architecture, and technology stack
- `docs/CONVENTIONS.md` — detailed contribution conventions
- `CLAUDE.md` — conventions for AI-assisted development on this project

This document should be kept up to date as the project evolves. If any
section no longer reflects the current state of the repository, it should
be corrected as part of the change that causes the discrepancy.
