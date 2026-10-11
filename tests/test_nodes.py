import re

import pytest

from app.headscale import HeadscaleClient
from app.service import Identity, NodeNotFound, PortalService
from tests.test_service import ALICE, ROOT
from tests.test_web import ALICE as ALICE_SESSION, ROOT as ROOT_SESSION, client


async def test_user_sees_only_own_untagged_nodes(settings, hs):
    svc = PortalService(settings, hs)
    me = await svc.resolve_me(ALICE)
    names = [n.name for n in await svc.list_nodes(ALICE, me)]
    assert names == ["alice-laptop", "alice-phone"]  # no bob-pc, tagged or orphan nodes


async def test_admin_sees_all_user_nodes_but_not_tagged(settings, hs):
    svc = PortalService(settings, hs)
    me = await svc.resolve_me(ROOT)
    names = [n.name for n in await svc.list_nodes(ROOT, me)]
    assert names == ["alice-laptop", "alice-phone", "bob-pc"]


async def test_node_info_parsing(settings, hs):
    svc = PortalService(settings, hs)
    me = await svc.resolve_me(ALICE)
    n = (await svc.list_nodes(ALICE, me))[0]
    assert n.online and n.ips == ["100.64.0.10"] and n.last_seen.microsecond == 123456
    assert n.expiry is None  # zero time means "never"


async def test_delete_own_node(settings, hs, fake):
    svc = PortalService(settings, hs)
    me = await svc.resolve_me(ALICE)
    node = await svc.delete_node(ALICE, me, 11)
    assert node.name == "alice-phone" and fake.deleted == ["11"]


@pytest.mark.parametrize("nid", [12, 13, 14, 999])
async def test_cannot_delete_foreign_tagged_or_missing(settings, hs, fake, nid):
    svc = PortalService(settings, hs)
    me = await svc.resolve_me(ALICE)
    with pytest.raises(NodeNotFound):
        await svc.delete_node(ALICE, me, nid)
    assert fake.deleted == []


async def test_admin_deletes_foreign_but_not_tagged(settings, hs, fake):
    svc = PortalService(settings, hs)
    me = await svc.resolve_me(ROOT)
    await svc.delete_node(ROOT, me, 12)
    assert fake.deleted == ["12"]
    with pytest.raises(NodeNotFound):
        await svc.delete_node(ROOT, me, 13)


def test_nodes_page(settings, hs):
    r = client(settings, hs, **ALICE_SESSION).get("/nodes")
    assert "alice-laptop" in r.text and "alice-phone" in r.text and "bob-pc" not in r.text
    assert "100.64.0.10" in r.text and "online" in r.text and "/nodes/11/delete" in r.text
    assert "My nodes" in r.text


def test_admin_nodes_page(settings, hs):
    r = client(settings, hs, **ROOT_SESSION).get("/nodes")
    assert "bob-pc" in r.text and "All nodes" in r.text and "ci-runner" not in r.text


def test_delete_requires_confirmation_and_csrf(settings, hs, fake):
    c = client(settings, hs, **ALICE_SESSION)
    page = c.get("/nodes/11/delete")
    assert page.status_code == 200 and "alice-phone" in page.text and fake.deleted == []
    assert c.post("/nodes/11/delete", data={"csrf": "bad"}).status_code == 400
    assert fake.deleted == []
    r = c.post("/nodes/11/delete", data={"csrf": "tok"})
    assert r.status_code == 303 and r.headers["location"] == "/nodes"
    assert fake.deleted == ["11"]
    assert "Node alice-phone deleted." in c.get("/nodes").text
    assert "deleted." not in c.get("/nodes").text  # flash is shown once


def test_foreign_node_pages_are_404(settings, hs, fake):
    c = client(settings, hs, **ALICE_SESSION)
    assert c.get("/nodes/12/delete").status_code == 404
    assert c.post("/nodes/12/delete", data={"csrf": "tok"}).status_code == 404
    assert c.get("/nodes/13/delete").status_code == 404
    assert fake.deleted == []


def test_nodes_requires_login(settings, hs):
    c = client(settings, hs)
    for path in ("/nodes", "/nodes/10/delete"):
        assert c.get(path).status_code == 303
    assert c.post("/nodes/10/delete", data={"csrf": "x"}).status_code == 303


def test_nodes_page_user_missing_shows_signup(settings, hs):
    c = client(settings, hs, user=dict(username="ghost", email="g@example.com", name="g",
                                       is_admin=False), csrf="tok")
    r = c.get("/nodes")
    assert "/auth/hskey-authreq-" in r.text


async def test_delete_race_is_not_found(settings, hs, fake, monkeypatch):
    svc = PortalService(settings, hs)
    me = await svc.resolve_me(ALICE)
    real_get = hs.get_node

    async def get_then_vanish(nid):
        node = await real_get(nid)
        fake.nodes[:] = [n for n in fake.nodes if n["id"] != str(nid)]
        return node

    monkeypatch.setattr(hs, "get_node", get_then_vanish)
    with pytest.raises(NodeNotFound):
        await svc.delete_node(ALICE, me, 11)
