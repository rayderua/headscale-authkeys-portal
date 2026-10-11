from __future__ import annotations

import logging
import secrets
from pathlib import Path

from authlib.integrations.starlette_client import OAuth
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .command import build_command
from .config import Settings, load_settings
from .headscale import HeadscaleClient, HeadscaleError, HeadscaleUnavailable
from .i18n import STRINGS
from .signup import build_signup_url
from .service import Identity, PortalError, PortalService, is_admin

BASE = Path(__file__).parent
log = logging.getLogger("portal")

_STATUS = {"user_not_found": 200, "user_ambiguous": 200, "forbidden": 403,
           "limit_reached": 409, "bad_request": 400, "rate_limited": 429,
           "node_not_found": 404}


def create_app(settings: Settings | None = None, hs: HeadscaleClient | None = None) -> FastAPI:
    s = settings or load_settings()
    hs = hs or HeadscaleClient(s.headscale_api_url, s.headscale_api_key,
                               s.headscale_timeout_seconds)
    svc = PortalService(s, hs)
    strings = STRINGS

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=s.session.secret,
                       max_age=s.session.max_age_hours * 3600, same_site="lax",
                       https_only=s.session.cookie_secure, session_cookie="portal_session")
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    templates = Jinja2Templates(directory=BASE / "templates")

    oauth = OAuth()
    oauth.register(
        name="oidc",
        server_metadata_url=s.oidc.issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_id=s.oidc.client_id, client_secret=s.oidc.client_secret,
        client_kwargs={"scope": " ".join(s.oidc.scopes)},
    )

    def t(key: str, **kw) -> str:
        return strings.get(key, key).format(**{"url": s.headscale_public_url,
                                              "ttl": s.limits.ttl_minutes, **kw})

    def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        ctx.update(t=t, user=request.session.get("user"), csrf=_csrf(request), lang="en",
                   settings=s, flash=request.session.pop("flash", None),
                   page="nodes" if request.url.path.startswith("/nodes") else "keys")
        resp = templates.TemplateResponse(request, name, ctx, status_code=status)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def error_page(request: Request, key: str, status: int) -> HTMLResponse:
        return render(request, "error.html", status, message=t(key))

    def _csrf(request: Request) -> str:
        tok = request.session.get("csrf")
        if not tok:
            tok = request.session["csrf"] = secrets.token_urlsafe(24)
        return tok

    def _csrf_ok(request: Request, token: str) -> bool:
        return bool(token) and secrets.compare_digest(token, request.session.get("csrf", ""))

    def identity(request: Request) -> Identity | None:
        u = request.session.get("user")
        return Identity(**u) if u else None

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.exception_handler(HeadscaleUnavailable)
    async def _unavailable(request: Request, exc: HeadscaleUnavailable):
        log.error("headscale unavailable: %s", exc)
        return error_page(request, "err_unavailable", 503)

    @app.exception_handler(HeadscaleError)
    async def _hs_error(request: Request, exc: HeadscaleError):
        log.error("headscale error: %s", exc)
        return error_page(request, "err_generic", 502)

    def signup_url() -> str | None:
        return build_signup_url(s.headscale_public_url) if s.signup_link else None

    @app.exception_handler(PortalError)
    async def _portal_error(request: Request, exc: PortalError):
        if exc.code == "user_not_found":
            return render(request, "signup.html", 200, signup_url=signup_url())
        return error_page(request, "err_" + exc.code, _STATUS.get(exc.code, 400))

    @app.get("/healthz")
    async def healthz():
        return PlainTextResponse("ok")

    # ---- auth ---------------------------------------------------------------
    @app.get("/")
    async def index(request: Request):
        if identity(request):
            return RedirectResponse("/keys", status_code=303)
        return render(request, "login.html")

    @app.get("/login")
    async def login(request: Request):
        return await oauth.oidc.authorize_redirect(
            request, s.public_url.rstrip("/") + "/auth/callback")

    @app.get("/auth/callback")
    async def callback(request: Request):
        try:
            token = await oauth.oidc.authorize_access_token(request)
            claims = dict(token.get("userinfo") or {})
            if s.oidc.groups_claim not in claims:
                claims.update(await oauth.oidc.userinfo(token=token))
        except Exception as exc:  # any OIDC failure -> generic message
            log.warning("oidc callback failed: %r", exc)
            return error_page(request, "err_login", 400)
        username = claims.get(s.oidc.username_claim) or ""
        if not username:
            return error_page(request, "err_login", 400)
        groups = claims.get(s.oidc.groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        admin = is_admin(s, username, list(groups))
        request.session.clear()
        request.session["user"] = {"username": username, "email": claims.get("email") or "",
                                   "name": claims.get("name") or username, "is_admin": admin}
        log.info("login user=%s admin=%s", username, admin)
        return RedirectResponse("/keys", status_code=303)

    @app.post("/logout")
    async def logout(request: Request, csrf: str = Form("")):
        if _csrf_ok(request, csrf):
            request.session.clear()
        return RedirectResponse("/", status_code=303)

    # ---- keys ---------------------------------------------------------------
    async def _me(who: Identity) -> tuple[dict | None, str | None]:
        """Resolve the caller's headscale user; admins may continue without one."""
        try:
            return await svc.resolve_me(who), None
        except PortalError as exc:
            if who.is_admin:
                return None, exc.code
            raise

    @app.get("/keys")
    async def keys(request: Request):
        who = identity(request)
        if not who:
            return RedirectResponse("/", status_code=303)
        me, me_problem = await _me(who)
        sets = await svc.list_sets(who, me)
        owners = await svc.list_owners() if who.is_admin else []
        mine = [x for x in sets if me and x.owner_id == int(me["id"])]
        return render(request, "keys.html", sets=sets, me=me, me_problem=me_problem,
                      owners=owners, used=len(mine),
                      signup_url=signup_url() if me_problem == "user_not_found" else None, limits=s.limits,
                      full=bool(me) and len(mine) >= s.limits.max_sets)

    @app.post("/keys")
    async def create(request: Request, devices: int = Form(1), owner: str = Form(""),
                     csrf: str = Form("")):
        who = identity(request)
        if not who:
            return RedirectResponse("/", status_code=303)
        if not _csrf_ok(request, csrf):
            return error_page(request, "err_csrf", 400)
        me, _ = await _me(who)
        owner_id = int(owner) if owner.isdigit() else None
        owner_user, keys_, expires = await svc.create_set(who, me, owner_id, devices)
        commands = [build_command(s.headscale_public_url, k, s.tailscale.up_args) for k in keys_]
        return render(request, "created.html", owner_user=owner_user, keys=list(zip(keys_, commands)),
                      expires=expires, for_other=me is None or int(owner_user["id"]) != int(me["id"]))

    @app.post("/keys/revoke")
    async def revoke(request: Request, set_id: str = Form(""), csrf: str = Form("")):
        who = identity(request)
        if not who:
            return RedirectResponse("/", status_code=303)
        if not _csrf_ok(request, csrf):
            return error_page(request, "err_csrf", 400)
        me, _ = await _me(who)
        await svc.revoke_set(who, me, set_id)
        return RedirectResponse("/keys", status_code=303)

    # ---- nodes ----------------------------------------------------------------
    @app.get("/nodes")
    async def nodes(request: Request):
        who = identity(request)
        if not who:
            return RedirectResponse("/", status_code=303)
        me, me_problem = await _me(who)
        return render(request, "nodes.html", nodes=await svc.list_nodes(who, me),
                      me_problem=me_problem,
                      signup_url=signup_url() if me_problem == "user_not_found" else None)

    @app.get("/nodes/{node_id}/delete")
    async def delete_node_page(request: Request, node_id: int):
        who = identity(request)
        if not who:
            return RedirectResponse("/", status_code=303)
        me, _ = await _me(who)
        return render(request, "node_delete.html", node=await svc.get_node(who, me, node_id))

    @app.post("/nodes/{node_id}/delete")
    async def delete_node(request: Request, node_id: int, csrf: str = Form("")):
        who = identity(request)
        if not who:
            return RedirectResponse("/", status_code=303)
        if not _csrf_ok(request, csrf):
            return error_page(request, "err_csrf", 400)
        me, _ = await _me(who)
        node = await svc.delete_node(who, me, node_id)
        request.session["flash"] = t("node_deleted", name=node.name)
        return RedirectResponse("/nodes", status_code=303)

    return app
