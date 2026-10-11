import base64
import json
import re

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.main import create_app
from tests.conftest import make_settings


def cookie(settings, **session):
    raw = base64.b64encode(json.dumps(session).encode())
    return TimestampSigner(settings.session.secret).sign(raw).decode()


def client(settings, hs, **session):
    c = TestClient(create_app(settings, hs), follow_redirects=False)
    if session:
        c.cookies.set("portal_session", cookie(settings, **session))
    return c


ALICE = dict(user=dict(username="alice", email="alice@example.com", name="Alice", is_admin=False),
             csrf="tok")
ROOT = dict(user=dict(username="root", email="root@example.com", name="Root", is_admin=True),
            csrf="tok")


def test_anonymous(settings, hs):
    c = client(settings, hs)
    assert c.get("/healthz").text == "ok"
    assert "/login" in c.get("/").text
    r = c.get("/keys")
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_create_flow_shows_copy_fields(settings, hs):
    c = client(settings, hs, **ALICE)
    assert "0:00" not in c.get("/keys").text
    r = c.post("/keys", data={"devices": "2", "csrf": "tok"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    keys = re.findall(r"hskey-auth-\d+", r.text)
    assert len(set(keys)) == 2
    assert r.text.count('data-copy="https://hs.example.com"') == 1
    assert "tailscale up --login-server https://hs.example.com --authkey hskey-auth-1" in r.text
    listing = c.get("/keys").text
    assert "hskey-auth" not in listing  # keys never shown again


def test_csrf_enforced(settings, hs):
    c = client(settings, hs, **ALICE)
    assert c.post("/keys", data={"devices": "1", "csrf": "bad"}).status_code == 400
    assert c.post("/keys", data={"devices": "1"}).status_code == 400


def test_user_not_found_message(settings, hs, fake):
    c = client(settings, hs, user=dict(username="ghost", email="g@example.com", name="g",
                                       is_admin=False), csrf="tok")
    r = c.get("/keys")
    assert "tailscale up --login-server https://hs.example.com" in r.text


def test_headscale_down_is_simple(settings, hs, fake):
    fake.down = True
    r = client(settings, hs, **ALICE).get("/keys")
    assert r.status_code == 503 and "Headscale is unavailable" in r.text


def test_non_admin_cannot_target_other(settings, hs):
    c = client(settings, hs, **ALICE)
    r = c.post("/keys", data={"devices": "1", "owner": "2", "csrf": "tok"})
    assert r.status_code == 403


def test_admin_flow(settings, hs):
    a = client(settings, hs, **ALICE)
    a.post("/keys", data={"devices": "1", "csrf": "tok"})
    r = client(settings, hs, **ROOT).get("/keys")
    assert "alice" in r.text and 'name="owner"' in r.text
    assert 'name="owner"' not in a.get("/keys").text
    r = client(settings, hs, **ROOT).post("/keys", data={"devices": "1", "owner": "2", "csrf": "tok"})
    assert r.status_code == 200 and "bob" in r.text


def test_revoke_via_web(settings, hs):
    c = client(settings, hs, **ALICE)
    c.post("/keys", data={"devices": "1", "csrf": "tok"})
    set_id = re.search(r'name="set_id" value="([^"]+)"', c.get("/keys").text).group(1)
    assert c.post("/keys/revoke", data={"set_id": set_id, "csrf": "tok"}).status_code == 303
    assert 'name="set_id"' not in c.get("/keys").text
