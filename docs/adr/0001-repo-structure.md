# ADR 0001: Initial repo structure

**Status:** Accepted
**Date:** 2026-07-24
**Author(s):** Student 2

## Context

The team needs a shared repo layout before any real code lands, so each
member has a clear home for their component and no folder-naming
disagreements happen mid-sprint.

## Options considered

### Option A: Monorepo, one folder per major component
- Pros: Mirrors the system architecture directly (gateway, backbone,
  ai-services, clients); easy for a small 4-person team to navigate;
  single CI pipeline and single set of dependency-management conventions.
- Cons: Coarser build boundaries than a multi-repo setup; less relevant
  at this scale.

### Option B: Separate repo per component
- Pros: Independent versioning and CI per service.
- Cons: Overhead not justified for a 4-student, 8-month pilot project;
  harder to keep architecture-touching changes reviewable as a whole.

## Decision

Option A — a single monorepo with top-level folders matching the system
architecture (`gateway/`, `backbone/`, `ai-services/`, `clients/`,
`infra/`, `docs/`).

## Consequences

Simple to onboard, one CI pipeline to maintain, and PRs that touch
multiple layers (e.g. gateway + backbone) stay reviewable in one place.
Revisit if/when any component needs independent deployment cadence.
