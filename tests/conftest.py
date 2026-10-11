import itertools
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import Settings
from app.headscale import HeadscaleClient

ISSUER = "https://auth.example.com/application/o/headscale"


def make_settings(**over) -> Settings:
    data = dict(
        public_url="https://portal.example.com",
        headscale_public_url="https://hs.example.com",
        headscale_api_url="http://headscale:8080",
        headscale_api_key="k",
        oidc=dict(issuer=ISSUER, client_id="c", client_secret="s"),
        admin=dict(group="admins", users=["root"]),
        session=dict(secret="x" * 32, cookie_secure=False),
    )
    data.update(over)
    return Settings.model_validate(data)


def user(uid, name, email=None, provider="oidc", sub="abc"):
    return {"id": str(uid), "name": name, "email": email or f"{name}@example.com",
            "provider": provider,
            "providerId": f"{ISSUER}/{sub}" if provider == "oidc" else ""}


class FakeHeadscale:
    """In-memory headscale API behind an httpx MockTransport."""

    def __init__(self, users):
        self.users, self.keys, self.down = users, [], False
        self._ids = itertools.count(1)
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, req: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        assert req.headers["authorization"] == "Bearer k"
        p = req.url.path
        if req.method == "GET" and p == "/api/v1/user":
            return httpx.Response(200, json={"users": self.users})
        if req.method == "GET" and p == "/api/v1/preauthkey":
            return httpx.Response(200, json={"preAuthKeys": self.keys})
        body = json.loads(req.content or b"{}")
        if req.method == "POST" and p == "/api/v1/preauthkey":
            u = next(x for x in self.users if x["id"] == body["user"])
            now = datetime.now(timezone.utc)
            kid = next(self._ids)
            k = {"id": str(kid), "key": f"hskey-auth-{kid}", "user": u, "reusable": body["reusable"],
                 "ephemeral": body["ephemeral"], "used": False, "expiration": body["expiration"],
                 "createdAt": (now.strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"), "aclTags": body["aclTags"]}
            self.keys.append(k)
            return httpx.Response(200, json={"preAuthKey": k})
        if req.method == "POST" and p == "/api/v1/preauthkey/expire":
            for k in self.keys:
                if k["id"] == body["id"]:
                    k["expiration"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime(
                        "%Y-%m-%dT%H:%M:%S.%fZ")
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def fake():
    return FakeHeadscale([user(1, "alice"), user(2, "bob"), user(3, "root"),
                          user(9, "manual", provider="")])


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def hs(fake):
    return HeadscaleClient("http://headscale:8080", "k", transport=fake.transport)
