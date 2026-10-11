"""Link that makes headscale create the user through its OIDC flow.

headscale 0.29.3 (hscontrol/oidc.go) creates/updates the user in the OIDC
callback before it looks up the pending auth request, and /auth/{auth_id} only
validates the id format ("hskey-authreq-" + 24 chars, 38 in total). A random id
therefore creates the user, after which headscale answers "login session
expired" (HTTP 410) because no node is waiting. This is not a documented
feature, so it can be switched off with `signup_link: false`.
"""
from __future__ import annotations

import secrets


def build_signup_url(headscale_public_url: str) -> str:
    return f"{headscale_public_url.rstrip('/')}/auth/hskey-authreq-{secrets.token_hex(12)}"
