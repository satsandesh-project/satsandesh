"""Organisation admin pages: Members, Circles, Announcements.

Registered with one call from admin_console.py (`register_org_pages(app)`), so the
shared file carries one line for the whole feature."""

from __future__ import annotations

import reflex as rx


def register_org_pages(app: rx.App) -> None:
    from admin_console.org.announce import ANNOUNCE_ROUTE, announce_page
    from admin_console.org.circles import CIRCLES_ROUTE, circles_page
    from admin_console.org.members import MEMBERS_ROUTE, members_page

    app.add_page(members_page, route=MEMBERS_ROUTE, title="SatSandesh — Members")
    app.add_page(circles_page, route=CIRCLES_ROUTE, title="SatSandesh — Circles")
    app.add_page(announce_page, route=ANNOUNCE_ROUTE, title="SatSandesh — Announcements")
