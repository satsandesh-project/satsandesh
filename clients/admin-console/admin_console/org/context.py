"""The organisation pages' data source: chosen by the environment on first use
(not at import, so importing a page never fails on a half-set environment), and
replaceable by tests -- the same arrangement as admin_console.set_source for the
moderation queue."""

from __future__ import annotations

from admin_console.org.source import OrgSource, build_org_source_from_env

_source: OrgSource | None = None


def get_source() -> OrgSource:
    global _source
    if _source is None:
        _source = build_org_source_from_env()
    return _source


def set_source(source: OrgSource | None) -> None:
    global _source
    _source = source
