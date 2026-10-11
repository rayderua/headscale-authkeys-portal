"""Portal configuration.

Non-secret settings come from a YAML file (PORTAL_CONFIG, default
/etc/portal/config.yaml). Secrets are read from the environment only:

  PORTAL_HEADSCALE_API_KEY, PORTAL_OIDC_CLIENT_SECRET, PORTAL_SESSION_SECRET
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class OidcConfig(BaseModel):
    issuer: str
    client_id: str
    client_secret: str = ""
    scopes: list[str] = ["openid", "profile", "email", "groups"]
    groups_claim: str = "groups"
    username_claim: str = "preferred_username"
    # Issuer that headscale used when it created the OIDC users. Defaults to
    # the portal issuer (same provider). Only the issuer is compared, never
    # the "sub" part of the user's provider_id.
    headscale_issuer: str = ""


class AdminConfig(BaseModel):
    # A user is an admin when the group is present in the groups claim ...
    group: str = ""
    # ... or when the username is listed here (exact match).
    users: list[str] = []


class LimitsConfig(BaseModel):
    max_sets: int = Field(3, ge=1)
    max_devices: int = Field(5, ge=1)
    default_devices: int = Field(1, ge=1)
    ttl_minutes: int = Field(15, ge=1)
    creates_per_minute: int = Field(10, ge=1)


class SessionConfig(BaseModel):
    secret: str = ""
    max_age_hours: int = Field(8, ge=1)
    cookie_secure: bool = True


class Settings(BaseModel):
    # Public URL of this portal (used for the OIDC redirect URI).
    public_url: str
    # Public URL of headscale, shown to users and used in the commands.
    headscale_public_url: str
    # Private URL of headscale, used for API calls only.
    headscale_api_url: str
    headscale_api_key: str = ""
    headscale_timeout_seconds: float = 10.0
    oidc: OidcConfig
    admin: AdminConfig = AdminConfig()
    limits: LimitsConfig = LimitsConfig()
    session: SessionConfig = SessionConfig()

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if self.limits.default_devices > self.limits.max_devices:
            raise ValueError("limits.default_devices must be <= limits.max_devices")
        for name in ("headscale_api_key", "session.secret", "oidc.client_secret"):
            obj: object = self
            for part in name.split("."):
                obj = getattr(obj, part)
            if not obj:
                raise ValueError(f"missing secret: {name}")
        return self

    @property
    def headscale_issuer(self) -> str:
        return (self.oidc.headscale_issuer or self.oidc.issuer).rstrip("/")


def load_settings(path: str | None = None) -> Settings:
    cfg_path = Path(path or os.environ.get("PORTAL_CONFIG", "/etc/portal/config.yaml"))
    data = yaml.safe_load(cfg_path.read_text()) or {}
    env = os.environ
    if env.get("PORTAL_HEADSCALE_API_KEY"):
        data["headscale_api_key"] = env["PORTAL_HEADSCALE_API_KEY"].strip()
    if env.get("PORTAL_OIDC_CLIENT_SECRET"):
        data.setdefault("oidc", {})["client_secret"] = env["PORTAL_OIDC_CLIENT_SECRET"].strip()
    if env.get("PORTAL_SESSION_SECRET"):
        data.setdefault("session", {})["secret"] = env["PORTAL_SESSION_SECRET"].strip()
    return Settings.model_validate(data)
