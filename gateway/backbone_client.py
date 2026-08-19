"""A CircleBackbone that talks to a backbone service over HTTP.

This is the ONLY file in the gateway that knows how to reach a backbone.
Swapping custom-lite for Matrix (ADR 0002, still undecided) means writing
a sibling of this class and pointing BACKBONE_URL elsewhere -- with no
change to gateway/circles.py, which is the entire point of the interface.

It subclasses CircleBackbone rather than merely matching its shape, so
Python refuses to instantiate it if a contract method is ever added and
not implemented here -- enforced at startup in the real container, not
only under test.
"""

import os
from datetime import datetime
from typing import List, Optional

import httpx
from interfaces import BackboneUnavailable, CircleBackbone, CircleMessage

BACKBONE_URL = os.environ.get("BACKBONE_URL", "http://spike-backbone:8000")


class HttpCircleBackbone(CircleBackbone):
    def __init__(self, base_url: Optional[str] = None, timeout: float = 5.0):
        self._base_url = (base_url or BACKBONE_URL).rstrip("/")
        self._timeout = timeout

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Every backbone call funnels through here so that "backbone is
        unreachable" is translated once, into the contract's own error,
        instead of leaking httpx exceptions into route handlers."""
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise BackboneUnavailable(
                f"backbone returned {exc.response.status_code} for {method} {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise BackboneUnavailable(f"backbone unreachable at {url}: {exc}") from exc

    async def create_circle(self, name: str) -> str:
        data = await self._request("POST", "/circles", json={"name": name})
        return data["circle_id"]

    async def add_member(self, circle_id: str, user_id: str) -> None:
        await self._request(
            "POST", f"/circles/{circle_id}/members", json={"user_id": user_id}
        )

    async def remove_member(self, circle_id: str, user_id: str) -> None:
        await self._request("DELETE", f"/circles/{circle_id}/members/{user_id}")

    async def list_members(self, circle_id: str) -> List[str]:
        data = await self._request("GET", f"/circles/{circle_id}/members")
        return data["members"]

    async def post_announcement(self, circle_id: str, sender_id: str, body: str) -> str:
        data = await self._request(
            "POST",
            f"/circles/{circle_id}/announce",
            json={"sender_id": sender_id, "body": body},
        )
        return data["message_id"]

    async def list_messages(
        self, circle_id: str, limit: int = 50, before: Optional[str] = None
    ) -> List[CircleMessage]:
        params = {"limit": limit}
        if before is not None:
            params["before"] = before
        data = await self._request("GET", f"/circles/{circle_id}/messages", params=params)
        return [
            CircleMessage(
                id=m["id"],
                circle_id=m["circle_id"],
                sender_id=m["sender_id"],
                body=m["body"],
                created_at=datetime.fromisoformat(m["created_at"]),
            )
            for m in data["messages"]
        ]
