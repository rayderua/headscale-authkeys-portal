"""Thin async client for the headscale REST API (verified against v0.29.3 swagger).

Used endpoints:
  GET  /api/v1/user                  -> {"users": [...]}
  GET  /api/v1/preauthkey            -> {"preAuthKeys": [...]}  (all users)
  POST /api/v1/preauthkey            -> {"preAuthKey": {...}}
  POST /api/v1/preauthkey/expire     body {"id": "<uint64>"}
uint64 values are JSON strings.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx2 as httpx


class HeadscaleError(Exception):
    """Headscale answered, but with an error."""


class HeadscaleUnavailable(HeadscaleError):
    """Headscale could not be reached or answered 502/503/504."""


_FRACTION = re.compile(r"\.(\d+)")


def parse_ts(value: str) -> datetime:
    """Parse an RFC3339 timestamp (possibly with nanoseconds) to aware UTC."""
    value = value.strip().replace("Z", "+00:00")
    value = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], value, count=1)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class HeadscaleClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kw) -> dict:
        try:
            resp = await self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise HeadscaleUnavailable(f"{method} {path}: {exc!r}") from exc
        if resp.status_code in (502, 503, 504):
            raise HeadscaleUnavailable(f"{method} {path}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise HeadscaleError(f"{method} {path}: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise HeadscaleError(f"{method} {path}: invalid JSON") from exc

    async def list_users(self) -> list[dict]:
        return (await self._request("GET", "/api/v1/user")).get("users") or []

    async def list_preauth_keys(self) -> list[dict]:
        return (await self._request("GET", "/api/v1/preauthkey")).get("preAuthKeys") or []

    async def create_preauth_key(self, user_id: int, expiration: datetime) -> dict:
        body = {
            "user": str(user_id),
            "reusable": False,
            "ephemeral": False,
            "expiration": format_ts(expiration),
            "aclTags": [],
        }
        data = await self._request("POST", "/api/v1/preauthkey", json=body)
        return data.get("preAuthKey") or {}

    async def expire_preauth_key(self, key_id: int) -> None:
        await self._request("POST", "/api/v1/preauthkey/expire", json={"id": str(key_id)})