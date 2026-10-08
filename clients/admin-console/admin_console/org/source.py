"""
Where the organisation pages (Members, Circles, Announce) get their data.

Two backends behind one protocol, chosen by the same environment variables as
the moderation queue (admin_console/source.py's `build_source_from_env`):

  FixtureOrgSource  -- in memory, a few seeded people and circles. The default:
                       needs nothing running, so the pages can be built and
                       demonstrated alone. It follows the same rules as the
                       real routes (admin-only is the gateway's job, but: the
                       site keeps one admin, an announcement goes only into an
                       announcement circle, a retry posts once).

  GatewayOrgSource  -- the real `/admin/*` routes (contracts/chat/admin_org.py)
                       over HTTP. Works against the gateway and, with
                       `X-Mock-Role: admin`, against the chat mock, which is how
                       the tests drive it (an injected client carries its own
                       base URL and timeout).

Every payload crossing this boundary is validated against the contract in both
backends, so a drifted answer fails with a readable message instead of nonsense
on screen. Every failure -- not signed in, not a site admin, a conflict, gateway
down -- is a `SourceError` whose text is written to be shown to the admin as-is;
without that, a click would raise inside a Reflex event handler and nothing would
visibly happen.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

import httpx
from pydantic import ValidationError

from admin_console.source import (
    GatewayQueueSource,
    SourceError,
    _detail,
    build_source_from_env,
)
from contracts.chat.admin_org import (
    AdminActionKind,
    AdminActionOut,
    AdminActionsOut,
    AdminAnnouncementIn,
    AdminAnnouncementOut,
    AdminCircleCreate,
    AdminCircleList,
    AdminCircleMembers,
    AdminCircleOut,
    AdminMemberOut,
    AdminTokenOut,
    AdminUserCreate,
    AdminUserCreated,
    AdminUserList,
    AdminUserOut,
    SiteRole,
)
from contracts.chat.circles import CircleKind, MembershipRole

# A server that keeps handing back a cursor is an error, not an infinite loop.
MAX_PAGES = 50
USER_PAGE_SIZE = 200


class OrgSource(Protocol):
    label: str

    def list_users(self) -> list[AdminUserOut]: ...
    def create_user(self, body: AdminUserCreate) -> AdminUserCreated: ...
    def set_user_role(self, user_id: str, role: SiteRole) -> AdminUserOut: ...
    def reissue_token(self, user_id: str) -> AdminTokenOut: ...

    def list_circles(self) -> list[AdminCircleOut]: ...
    def create_circle(self, body: AdminCircleCreate) -> AdminCircleOut: ...
    def rename_circle(self, circle_id: str, name: str) -> AdminCircleOut: ...
    def list_members(self, circle_id: str) -> list[AdminMemberOut]: ...
    def add_member(self, circle_id: str, user_id: str, role: MembershipRole) -> AdminMemberOut: ...
    def set_member_role(
        self, circle_id: str, user_id: str, role: MembershipRole
    ) -> AdminMemberOut: ...
    def remove_member(self, circle_id: str, user_id: str) -> None: ...

    def publish_announcement(self, body: AdminAnnouncementIn) -> AdminAnnouncementOut: ...
    def list_actions(self, limit: int = 20) -> list[AdminActionOut]: ...


# --- the real thing --------------------------------------------------------------------------
class GatewayOrgSource:
    """The real routes, over HTTP. `headers` carries anything extra the target
    needs: the chat mock wants `X-Mock-Role: admin`; the real gateway checks the
    DATABASE role of the user behind the bearer token and needs nothing extra.

    `client` lets a test hand in an in-process client (a FastAPI TestClient is an
    httpx.Client), so the tests exercise the real request/response path with no
    socket and no server."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 10.0,
        headers: dict[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.label = f"Gateway — {base_url}"
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", **(headers or {})}
        self._timeout = timeout_s
        self._client = client

    # -- transport ---------------------------------------------------------

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._client is not None:
            client, url, extra = self._client, path, {}
        else:
            client, url, extra = httpx, f"{self._base}{path}", {"timeout": self._timeout}
        try:
            response = client.request(method, url, headers=self._headers, **extra, **kwargs)
        except httpx.TimeoutException as exc:
            raise SourceError(
                f"The gateway did not answer within {self._timeout:g} s. "
                "Nothing was changed; try again."
            ) from exc
        except httpx.TransportError as exc:
            raise SourceError(
                "Could not reach the gateway. Check the console's gateway address "
                f"and that you are on the network. ({type(exc).__name__})"
            ) from exc

        status = response.status_code
        if status == 401:
            raise SourceError(
                "The gateway did not accept this console's token (401). It may be wrong or expired."
            )
        if status == 403:
            raise SourceError(
                "This account is not an admin on that gateway (403), so it cannot "
                "manage members, circles or announcements."
            )
        if status >= 500:
            raise SourceError(
                f"The gateway had a problem ({status}): {_detail_text(response)}. "
                "Nothing may have been changed; check before trying again."
            )
        if status >= 400:
            # The gateway's own words are the useful ones ("The site must keep at
            # least one admin", "Circle not found: ...").
            raise SourceError(_detail_text(response))
        return response

    @staticmethod
    def _parse(model, response: httpx.Response):
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise SourceError(
                "The gateway answered in a shape this console does not understand. "
                "Its version may not match the console's."
            ) from exc

    def _get(self, model, path: str, **params):
        return self._parse(model, self._send("GET", path, params=params or None))

    def _write(self, model, method: str, path: str, body=None):
        json = body.model_dump(mode="json") if body is not None else None
        return self._parse(model, self._send(method, path, json=json))

    # -- users -------------------------------------------------------------

    def list_users(self) -> list[AdminUserOut]:
        users: list[AdminUserOut] = []
        cursor, seen = None, set()
        for _ in range(MAX_PAGES):
            params = {"limit": USER_PAGE_SIZE, **({"cursor": cursor} if cursor else {})}
            page = self._get(AdminUserList, "/admin/users", **params)
            users.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                return users
            if cursor in seen:
                raise SourceError("The gateway keeps returning the same page of members.")
            seen.add(cursor)
        raise SourceError("There are more members than this console will list.")

    def create_user(self, body: AdminUserCreate) -> AdminUserCreated:
        return self._write(AdminUserCreated, "POST", "/admin/users", body)

    def set_user_role(self, user_id: str, role: SiteRole) -> AdminUserOut:
        response = self._send("PATCH", f"/admin/users/{user_id}/role", json={"role": role.value})
        return self._parse(AdminUserOut, response)

    def reissue_token(self, user_id: str) -> AdminTokenOut:
        return self._write(AdminTokenOut, "POST", f"/admin/users/{user_id}/token")

    # -- circles -----------------------------------------------------------

    def list_circles(self) -> list[AdminCircleOut]:
        return self._get(AdminCircleList, "/admin/circles").items

    def create_circle(self, body: AdminCircleCreate) -> AdminCircleOut:
        return self._write(AdminCircleOut, "POST", "/admin/circles", body)

    def rename_circle(self, circle_id: str, name: str) -> AdminCircleOut:
        response = self._send("PATCH", f"/admin/circles/{circle_id}", json={"name": name})
        return self._parse(AdminCircleOut, response)

    def list_members(self, circle_id: str) -> list[AdminMemberOut]:
        return self._get(AdminCircleMembers, f"/admin/circles/{circle_id}/members").members

    def add_member(self, circle_id: str, user_id: str, role: MembershipRole) -> AdminMemberOut:
        response = self._send(
            "POST",
            f"/admin/circles/{circle_id}/members",
            json={"user_id": user_id, "role": role.value},
        )
        return self._parse(AdminMemberOut, response)

    def set_member_role(self, circle_id: str, user_id: str, role: MembershipRole) -> AdminMemberOut:
        response = self._send(
            "PATCH", f"/admin/circles/{circle_id}/members/{user_id}", json={"role": role.value}
        )
        return self._parse(AdminMemberOut, response)

    def remove_member(self, circle_id: str, user_id: str) -> None:
        self._send("DELETE", f"/admin/circles/{circle_id}/members/{user_id}")

    # -- announcements and the log ------------------------------------------

    def publish_announcement(self, body: AdminAnnouncementIn) -> AdminAnnouncementOut:
        return self._write(AdminAnnouncementOut, "POST", "/admin/announcements", body)

    def list_actions(self, limit: int = 20) -> list[AdminActionOut]:
        return self._get(AdminActionsOut, "/admin/actions", limit=limit).items


def _detail_text(response: httpx.Response) -> str:
    """The gateway's `detail` as a sentence. A 422 from FastAPI's own validation
    carries a list of problems, not a string; say what was wrong in one line."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):
        parts = [
            f"{'.'.join(str(p) for p in item.get('loc', [])[1:]) or 'request'}: {item.get('msg')}"
            for item in detail
            if isinstance(item, dict)
        ]
        return "That was not accepted — " + "; ".join(parts) if parts else _detail(response)
    return str(detail) if detail else _detail(response)


# --- the default: nothing needs to be running ------------------------------------------------
class FixtureOrgSource:
    """In memory, seeded with a few people and circles, and nothing persists
    across a restart -- for building and showing the UI, not a stand-in
    datastore. The rules that make a page behave (one admin stays, announcements
    only into announcement circles, a retry posts once) match the real routes."""

    label = "Sample data (not connected to a gateway)"
    _ADMIN = "fixture-admin"

    def __init__(self) -> None:
        self._users: dict[str, AdminUserOut] = {}
        self._circles: dict[str, AdminCircleOut] = {}
        self._members: dict[str, dict[str, AdminMemberOut]] = {}
        self._actions: list[AdminActionOut] = []
        self._published: dict[tuple[uuid.UUID, str], str] = {}
        self._seed()

    def _seed(self) -> None:
        asha = self._new_user("Asha Rao", "en", SiteRole.ADMIN)
        kamala = self._new_user("Kamala Devi", "hi", SiteRole.ELDER)
        ravi = self._new_user("Ravi Teja", "te", SiteRole.ELDER)
        satsang = self._new_circle("Evening Satsang", CircleKind.GROUP)
        notices = self._new_circle("Notices", CircleKind.ANNOUNCEMENT)
        for circle, user in [
            (satsang, kamala),
            (satsang, ravi),
            (notices, kamala),
            (notices, ravi),
            (notices, asha),
        ]:
            self._add(circle.id, user.id, MembershipRole.MEMBER)

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _log(self, action: AdminActionKind, target_type: str, target_id, **details) -> None:
        self._actions.append(
            AdminActionOut(
                id=str(uuid.uuid4()),
                admin_id=self._ADMIN,
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details,
                created_at=self._now(),
            )
        )

    def _new_user(self, name: str, language: str, role: SiteRole) -> AdminUserOut:
        user = AdminUserOut(
            id=str(uuid.uuid4()),
            name=name,
            preferred_language=language,
            role=role,
            created_at=self._now(),
        )
        self._users[user.id] = user
        return user

    def _new_circle(self, name: str, kind: CircleKind) -> AdminCircleOut:
        circle = AdminCircleOut(
            id=str(uuid.uuid4()),
            name=name,
            kind=kind,
            created_by=self._ADMIN,
            created_at=self._now(),
            member_count=0,
        )
        self._circles[circle.id] = circle
        self._members[circle.id] = {}
        return circle

    def _add(self, circle_id: str, user_id: str, role: MembershipRole) -> AdminMemberOut:
        member = AdminMemberOut(
            user_id=user_id,
            name=self._users[user_id].name,
            role=role,
            joined_at=self._now(),
        )
        self._members[circle_id][user_id] = member
        return member

    def _circle(self, circle_id: str) -> AdminCircleOut:
        circle = self._circles.get(circle_id)
        if circle is None:
            raise SourceError("That circle does not exist (any more).")
        return circle.model_copy(update={"member_count": len(self._members[circle_id])})

    def _user(self, user_id: str) -> AdminUserOut:
        user = self._users.get(user_id)
        if user is None:
            raise SourceError("That person does not exist (any more).")
        return user

    # -- users -------------------------------------------------------------

    def list_users(self) -> list[AdminUserOut]:
        return list(self._users.values())

    def create_user(self, body: AdminUserCreate) -> AdminUserCreated:
        for circle_id in body.circle_ids:
            self._circle(circle_id)  # one unknown circle refuses the whole request
        user = self._new_user(body.name, body.preferred_language, body.role)
        joined = list(dict.fromkeys(body.circle_ids))
        for circle_id in joined:
            self._add(circle_id, user.id, MembershipRole.MEMBER)
        self._log(
            AdminActionKind.USER_CREATE,
            "user",
            user.id,
            name=user.name,
            role=user.role.value,
            circle_ids=joined,
        )
        return AdminUserCreated(
            user=user, access_token=f"sample-token-{user.id}", circle_ids_joined=joined
        )

    def set_user_role(self, user_id: str, role: SiteRole) -> AdminUserOut:
        user = self._user(user_id)
        if user.role is SiteRole.ADMIN and role is not SiteRole.ADMIN:
            if sum(1 for u in self._users.values() if u.role is SiteRole.ADMIN) <= 1:
                raise SourceError("The site must keep at least one admin")
        updated = user.model_copy(update={"role": role})
        self._users[user_id] = updated
        self._log(
            AdminActionKind.USER_SET_ROLE,
            "user",
            user_id,
            from_role=user.role.value,
            to_role=role.value,
        )
        return updated

    def reissue_token(self, user_id: str) -> AdminTokenOut:
        self._user(user_id)
        self._log(AdminActionKind.USER_REISSUE_TOKEN, "user", user_id)
        return AdminTokenOut(user_id=user_id, access_token=f"sample-token-{user_id}")

    # -- circles -----------------------------------------------------------

    def list_circles(self) -> list[AdminCircleOut]:
        return [self._circle(c) for c in self._circles]

    def create_circle(self, body: AdminCircleCreate) -> AdminCircleOut:
        circle = self._new_circle(body.name, body.kind)
        self._log(
            AdminActionKind.CIRCLE_CREATE, "circle", circle.id, name=body.name, kind=body.kind.value
        )
        return circle

    def rename_circle(self, circle_id: str, name: str) -> AdminCircleOut:
        circle = self._circle(circle_id)
        self._circles[circle_id] = circle.model_copy(update={"name": name})
        self._log(
            AdminActionKind.CIRCLE_RENAME, "circle", circle_id, from_name=circle.name, to_name=name
        )
        return self._circle(circle_id)

    def list_members(self, circle_id: str) -> list[AdminMemberOut]:
        self._circle(circle_id)
        return list(self._members[circle_id].values())

    def add_member(self, circle_id: str, user_id: str, role: MembershipRole) -> AdminMemberOut:
        self._circle(circle_id)
        self._user(user_id)
        existing = self._members[circle_id].get(user_id)
        if existing is not None:
            return existing
        member = self._add(circle_id, user_id, role)
        self._log(AdminActionKind.MEMBER_ADD, "circle", circle_id, user_id=user_id, role=role.value)
        return member

    def set_member_role(self, circle_id: str, user_id: str, role: MembershipRole) -> AdminMemberOut:
        self._circle(circle_id)
        existing = self._members[circle_id].get(user_id)
        if existing is None:
            raise SourceError("That person is not in this circle.")
        updated = existing.model_copy(update={"role": role})
        self._members[circle_id][user_id] = updated
        self._log(
            AdminActionKind.MEMBER_SET_ROLE,
            "circle",
            circle_id,
            user_id=user_id,
            from_role=existing.role.value,
            to_role=role.value,
        )
        return updated

    def remove_member(self, circle_id: str, user_id: str) -> None:
        self._circle(circle_id)
        if self._members[circle_id].pop(user_id, None) is None:
            raise SourceError("That person is not in this circle.")
        self._log(AdminActionKind.MEMBER_REMOVE, "circle", circle_id, user_id=user_id)

    # -- announcements and the log ------------------------------------------

    def publish_announcement(self, body: AdminAnnouncementIn) -> AdminAnnouncementOut:
        if body.all_circles:
            targets = [c for c in self._circles.values() if c.kind is CircleKind.ANNOUNCEMENT]
            if not targets:
                raise SourceError("There is no announcement circle yet")
        else:
            circle = self._circle(body.circle_id)
            if circle.kind is not CircleKind.ANNOUNCEMENT:
                raise SourceError("That circle is not an announcement circle")
            targets = [circle]
        message_ids, fresh = [], False
        for circle in targets:
            key = (body.client_msg_id, circle.id)
            if key not in self._published:
                self._published[key] = str(uuid.uuid4())
                fresh = True
            message_ids.append(self._published[key])
        if fresh:
            self._log(
                AdminActionKind.ANNOUNCEMENT_PUBLISH,
                "all_circles" if body.all_circles else "circle",
                None if body.all_circles else targets[0].id,
                circle_ids=[c.id for c in targets],
                length=len(body.text),
            )
        return AdminAnnouncementOut(circle_ids=[c.id for c in targets], message_ids=message_ids)

    def list_actions(self, limit: int = 20) -> list[AdminActionOut]:
        return list(reversed(self._actions))[:limit]


def build_org_source_from_env(env: dict[str, str] | None = None) -> OrgSource:
    """The same environment as the moderation queue (CONSOLE_SOURCE,
    CONSOLE_GATEWAY_URL, CONSOLE_GATEWAY_TOKEN, CONSOLE_GATEWAY_HEADERS), so one
    set of variables points the whole console at one gateway -- and the same
    validation, failing at startup naming the variable. The token is never
    echoed."""
    chosen = build_source_from_env(env)
    if isinstance(chosen, GatewayQueueSource):
        token = chosen._headers["Authorization"].removeprefix("Bearer ")
        extra = {k: v for k, v in chosen._headers.items() if k != "Authorization"}
        return GatewayOrgSource(chosen._base, token, timeout_s=chosen._timeout, headers=extra)
    return FixtureOrgSource()
