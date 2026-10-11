"""Business logic: user matching, key sets, limits, admin scoping.

A "key set" is a group of single-use pre-auth keys created together. Headscale
has no comment/label field on pre-auth keys, so the portal stays stateless and
identifies a set as (owner id, expiration timestamp in microseconds): all keys
of one set share an identical expiration. Only keys that look like portal keys
(not reusable, not ephemeral, no tags, lifetime <= TTL) are ever touched.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import Settings
from .headscale import HeadscaleClient, HeadscaleNotFound, format_ts, parse_ts

audit = logging.getLogger("portal.audit")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_LIFETIME_SLACK = timedelta(seconds=5)


class PortalError(Exception):
    """Base class; `code` is a message key for the UI."""
    code = "error"


class UserNotFound(PortalError):
    code = "user_not_found"


class AmbiguousUser(PortalError):
    code = "user_ambiguous"


class Forbidden(PortalError):
    code = "forbidden"


class LimitReached(PortalError):
    code = "limit_reached"


class BadRequest(PortalError):
    code = "bad_request"


class NodeNotFound(PortalError):
    code = "node_not_found"


class RateLimited(PortalError):
    code = "rate_limited"


@dataclass(frozen=True)
class Identity:
    username: str
    email: str
    name: str
    is_admin: bool


@dataclass
class KeyInfo:
    id: int
    used: bool
    expires_at: datetime
    created_at: datetime


@dataclass
class KeySet:
    owner_id: int
    owner_name: str
    expires_at: datetime
    keys: list[KeyInfo] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.owner_id}:{(self.expires_at - _EPOCH) // timedelta(microseconds=1)}"

    @property
    def unused(self) -> int:
        return sum(1 for k in self.keys if not k.used)

    @property
    def total(self) -> int:
        return len(self.keys)


def is_admin(settings: Settings, username: str, groups: list[str]) -> bool:
    adm = settings.admin
    if adm.group and adm.group in groups:
        return True
    return bool(username) and username in adm.users


def match_users(users: list[dict], issuer: str, username: str, email: str) -> list[dict]:
    """OIDC users of the given issuer whose name (and email, when both sides
    have one) match. The "sub" part of provider_id is deliberately ignored."""
    prefix = issuer.rstrip("/") + "/"
    out = []
    for u in users:
        if u.get("provider") != "oidc":
            continue
        if not (u.get("providerId") or "").startswith(prefix):
            continue
        if u.get("name") != username:
            continue
        u_email = (u.get("email") or "").casefold()
        if u_email and email and u_email != email.casefold():
            continue
        out.append(u)
    return out


@dataclass
class NodeInfo:
    id: int
    name: str
    owner_id: int
    owner_name: str
    ips: list[str]
    online: bool
    last_seen: datetime | None
    created_at: datetime | None
    expiry: datetime | None


def _ts(value: str | None) -> datetime | None:
    # headscale sends the zero time for "never"; treat years before 2000 as unset
    if not value:
        return None
    dt = parse_ts(value)
    return dt if dt.year >= 2000 else None


def to_node_info(raw: dict) -> NodeInfo | None:
    """User-owned nodes only: tagged nodes and nodes without a user are not
    managed by the portal."""
    user = raw.get("user") or {}
    if raw.get("tags") or not user.get("id") or not raw.get("id"):
        return None
    return NodeInfo(
        id=int(raw["id"]), name=raw.get("givenName") or raw.get("name") or "",
        owner_id=int(user["id"]), owner_name=user.get("name", ""),
        ips=list(raw.get("ipAddresses") or []), online=bool(raw.get("online")),
        last_seen=_ts(raw.get("lastSeen")), created_at=_ts(raw.get("createdAt")),
        expiry=_ts(raw.get("expiry")),
    )


class PortalService:
    def __init__(self, settings: Settings, hs: HeadscaleClient) -> None:
        self.s = settings
        self.hs = hs
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._rate: dict[str, deque[float]] = defaultdict(deque)

    # ---- users -----------------------------------------------------------
    def _oidc_users(self, users: list[dict]) -> list[dict]:
        prefix = self.s.headscale_issuer + "/"
        return [u for u in users if u.get("provider") == "oidc"
                and (u.get("providerId") or "").startswith(prefix)]

    async def resolve_me(self, who: Identity) -> dict:
        found = match_users(await self.hs.list_users(), self.s.headscale_issuer,
                            who.username, who.email)
        if not found:
            raise UserNotFound()
        if len(found) > 1:
            audit.warning("ambiguous headscale user user=%s ids=%s", who.username,
                          [u["id"] for u in found])
            raise AmbiguousUser()
        return found[0]

    async def list_owners(self) -> list[dict]:
        users = self._oidc_users(await self.hs.list_users())
        return sorted(users, key=lambda u: u.get("name", ""))

    # ---- key sets ----------------------------------------------------------
    def _group(self, keys: list[dict]) -> list[KeySet]:
        now = datetime.now(timezone.utc)
        ttl = timedelta(minutes=self.s.limits.ttl_minutes) + _LIFETIME_SLACK
        sets: dict[str, KeySet] = {}
        for k in keys:
            if k.get("reusable") or k.get("ephemeral") or k.get("aclTags"):
                continue
            user = k.get("user") or {}
            if not user.get("id") or not k.get("expiration") or not k.get("createdAt"):
                continue
            exp, created = parse_ts(k["expiration"]), parse_ts(k["createdAt"])
            if exp <= now or exp - created > ttl:
                continue
            ks = KeySet(owner_id=int(user["id"]), owner_name=user.get("name", ""), expires_at=exp)
            ks = sets.setdefault(ks.id, ks)
            ks.keys.append(KeyInfo(int(k["id"]), bool(k.get("used")), exp, created))
        # A set whose keys are all used no longer holds a slot.
        return sorted((s for s in sets.values() if s.unused),
                      key=lambda s: (s.owner_name, s.expires_at))

    async def list_sets(self, who: Identity, me: dict | None) -> list[KeySet]:
        sets = self._group(await self.hs.list_preauth_keys())
        if who.is_admin:
            return sets
        if me is None:
            return []
        return [s for s in sets if s.owner_id == int(me["id"])]

    def _check_rate(self, username: str) -> None:
        now = time.monotonic()
        q = self._rate[username]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.s.limits.creates_per_minute:
            raise RateLimited()
        q.append(now)

    async def create_set(self, who: Identity, me: dict | None, owner_id: int | None,
                         count: int) -> tuple[dict, list[str], datetime]:
        lim = self.s.limits
        if not 1 <= count <= lim.max_devices:
            raise BadRequest()
        target_id = owner_id if owner_id is not None else (int(me["id"]) if me else None)
        if target_id is None:
            raise UserNotFound()
        if me is None or target_id != int(me["id"]):
            if not who.is_admin:
                raise Forbidden()
        owners = {int(u["id"]): u for u in self._oidc_users(await self.hs.list_users())}
        owner = owners.get(target_id)
        if owner is None:
            raise UserNotFound()
        self._check_rate(who.username)

        async with self._locks[target_id]:
            current = [s for s in self._group(await self.hs.list_preauth_keys())
                       if s.owner_id == target_id]
            if len(current) >= lim.max_sets:
                raise LimitReached()
            expiration = datetime.now(timezone.utc).replace(microsecond=0) + \
                timedelta(minutes=lim.ttl_minutes)
            expiration = expiration + timedelta(microseconds=int(time.time_ns() // 1000) % 1_000_000)
            created: list[dict] = []
            try:
                for _ in range(count):
                    created.append(await self.hs.create_preauth_key(target_id, expiration))
            except Exception:
                for k in created:  # roll back a partially created set
                    try:
                        await self.hs.expire_preauth_key(int(k["id"]))
                    except Exception:  # best effort
                        pass
                raise
        audit.info("create actor=%s owner=%s count=%d ids=%s", who.username, owner["name"],
                   count, [k.get("id") for k in created])
        return owner, [k["key"] for k in created], expiration

    async def revoke_set(self, who: Identity, me: dict | None, set_id: str) -> None:
        sets = {s.id: s for s in self._group(await self.hs.list_preauth_keys())}
        target = sets.get(set_id)
        if target is None:
            raise BadRequest()
        if not who.is_admin and (me is None or target.owner_id != int(me["id"])):
            raise Forbidden()
        for k in target.keys:
            if not k.used:
                await self.hs.expire_preauth_key(k.id)
        audit.info("revoke actor=%s owner=%s set=%s", who.username, target.owner_name, set_id)

    # ---- nodes -----------------------------------------------------------
    @staticmethod
    def _visible(who: Identity, me: dict | None, node: NodeInfo | None) -> bool:
        if node is None:
            return False
        return who.is_admin or (me is not None and node.owner_id == int(me["id"]))

    async def list_nodes(self, who: Identity, me: dict | None) -> list[NodeInfo]:
        nodes = (to_node_info(n) for n in await self.hs.list_nodes())
        out = [n for n in nodes if self._visible(who, me, n)]
        return sorted(out, key=lambda n: (n.owner_name, n.name))

    async def get_node(self, who: Identity, me: dict | None, node_id: int) -> NodeInfo:
        """Fresh lookup by id with the ownership check. Foreign nodes look the
        same as missing ones, so ids cannot be probed."""
        node = to_node_info(await self.hs.get_node(node_id) or {})
        if not self._visible(who, me, node):
            raise NodeNotFound()
        return node

    async def delete_node(self, who: Identity, me: dict | None, node_id: int) -> NodeInfo:
        node = await self.get_node(who, me, node_id)
        try:
            await self.hs.delete_node(node.id)
        except HeadscaleNotFound:  # deleted by someone else in the meantime
            raise NodeNotFound() from None
        audit.info("delete-node actor=%s owner=%s node=%s id=%d", who.username,
                   node.owner_name, node.name, node.id)
        return node
