import pytest

from app.headscale import parse_ts
from app.service import (AmbiguousUser, BadRequest, Forbidden, Identity, LimitReached,
                         PortalService, RateLimited, UserNotFound, is_admin, match_users)
from tests.conftest import ISSUER, make_settings, user

ALICE = Identity("alice", "alice@example.com", "Alice", False)
ROOT = Identity("root", "root@example.com", "Root", True)


def test_parse_ts_nanoseconds():
    assert parse_ts("2026-10-11T12:00:00.123456789Z").microsecond == 123456
    assert parse_ts("2026-10-11T12:00:00Z").second == 0


def test_is_admin_group_or_user():
    s = make_settings()
    assert is_admin(s, "x", ["admins"])
    assert is_admin(s, "root", [])
    assert not is_admin(s, "x", ["users"])
    assert not is_admin(make_settings(admin=dict(group="", users=[])), "x", [""])


def test_match_ignores_sub_and_non_oidc():
    users = [user(1, "alice", sub="aaa"), user(2, "alice", provider=""),
             user(3, "alice", sub="zzz")]
    users[2]["providerId"] = "https://other.example/app/zzz"
    got = match_users(users, ISSUER, "alice", "ALICE@example.com")
    assert [u["id"] for u in got] == ["1"]
    assert match_users(users, ISSUER, "alice", "other@example.com") == []


async def test_resolve_errors(settings, hs, fake):
    svc = PortalService(settings, hs)
    assert (await svc.resolve_me(ALICE))["id"] == "1"
    with pytest.raises(UserNotFound):
        await svc.resolve_me(Identity("nobody", "n@example.com", "n", False))
    fake.users.append(user(10, "alice", sub="second"))
    with pytest.raises(AmbiguousUser):
        await svc.resolve_me(ALICE)


async def test_create_list_limit_revoke(settings, hs):
    svc = PortalService(settings, hs)
    me = await svc.resolve_me(ALICE)
    for _ in range(3):
        owner, keys, _exp = await svc.create_set(ALICE, me, None, 2)
        assert owner["name"] == "alice" and len(keys) == 2
    sets = await svc.list_sets(ALICE, me)
    assert len(sets) == 3 and all(s.total == 2 for s in sets)
    with pytest.raises(LimitReached):
        await svc.create_set(ALICE, me, None, 1)
    await svc.revoke_set(ALICE, me, sets[0].id)
    assert len(await svc.list_sets(ALICE, me)) == 2
    await svc.create_set(ALICE, me, None, 1)


async def test_scoping_and_admin(settings, hs):
    svc = PortalService(settings, hs)
    alice, bob = await svc.resolve_me(ALICE), {"id": "2"}
    await svc.create_set(ALICE, alice, None, 1)
    bob_id = Identity("bob", "bob@example.com", "Bob", False)
    bob_me = await svc.resolve_me(bob_id)
    assert await svc.list_sets(bob_id, bob_me) == []
    with pytest.raises(Forbidden):
        await svc.create_set(bob_id, bob_me, 1, 1)
    alice_set = (await svc.list_sets(ALICE, alice))[0]
    with pytest.raises(Forbidden):
        await svc.revoke_set(bob_id, bob_me, alice_set.id)
    # admin sees all, creates for others, revokes foreign sets
    root_me = await svc.resolve_me(ROOT)
    assert len(await svc.list_sets(ROOT, root_me)) == 1
    await svc.create_set(ROOT, root_me, 2, 1)
    assert len(await svc.list_sets(ROOT, root_me)) == 2
    await svc.revoke_set(ROOT, root_me, alice_set.id)
    assert len(await svc.list_sets(ROOT, root_me)) == 1
    # admin without own headscale user still works
    await svc.create_set(ROOT, None, 1, 1)
    # non-oidc users are not valid owners
    with pytest.raises(UserNotFound):
        await svc.create_set(ROOT, root_me, 9, 1)


async def test_foreign_keys_ignored(settings, hs, fake):
    svc = PortalService(settings, hs)
    me = await svc.resolve_me(ALICE)
    await svc.create_set(ALICE, me, None, 1)
    # manual reusable / long-lived keys must never show up or count
    base = dict(fake.keys[0])
    fake.keys.append({**base, "id": "50", "reusable": True})
    fake.keys.append({**base, "id": "51", "aclTags": ["tag:x"]})
    fake.keys.append({**base, "id": "52", "expiration": "2099-01-01T00:00:00Z"})
    assert len(await svc.list_sets(ALICE, me)) == 1


async def test_bad_count_and_rate_limit(hs):
    s = make_settings(limits=dict(creates_per_minute=2, max_sets=10))
    svc = PortalService(s, hs)
    me = await svc.resolve_me(ALICE)
    for bad in (0, 6):
        with pytest.raises(BadRequest):
            await svc.create_set(ALICE, me, None, bad)
    await svc.create_set(ALICE, me, None, 1)
    await svc.create_set(ALICE, me, None, 1)
    with pytest.raises(RateLimited):
        await svc.create_set(ALICE, me, None, 1)


def test_config_example_loads(monkeypatch):
    from pathlib import Path
    from app.config import load_settings
    monkeypatch.setenv("PORTAL_HEADSCALE_API_KEY", "k")
    monkeypatch.setenv("PORTAL_OIDC_CLIENT_SECRET", "s")
    monkeypatch.setenv("PORTAL_SESSION_SECRET", "x" * 32)
    s = load_settings(str(Path(__file__).parent.parent / "config_example.yaml"))
    assert s.admin.group == "admins" and s.limits.max_sets == 3


def test_build_command_multiline():
    from app.command import build_command
    cmd = build_command("https://hs.example.com", "KEY", ["--accept-dns=true", "--accept-routes"])
    assert cmd == ("tailscale up \\\n"
                   "  --login-server https://hs.example.com \\\n"
                   "  --authkey KEY \\\n"
                   "  --accept-dns=true \\\n"
                   "  --accept-routes")
    assert build_command("https://h", "K", []).endswith("--authkey K")


@pytest.mark.parametrize("bad", ["--accept-dns true", "--x; rm -rf /", "accept-dns", "--a$(b)",
                                 "--login-server=https://x", "--authkey=abc", "--auth-key"])
def test_up_args_validation(bad):
    with pytest.raises(Exception):
        make_settings(tailscale=dict(up_args=[bad]))


def test_up_args_accepts_common_flags():
    s = make_settings(tailscale=dict(up_args=["--accept-dns=true", "--advertise-tags=tag:a,tag:b",
                                              "--accept-routes", "-v"]))
    assert len(s.tailscale.up_args) == 4
