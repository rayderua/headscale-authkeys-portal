"""Builds the multi-line `tailscale up` command shown to users."""
from __future__ import annotations


def build_command(login_server: str, authkey: str, extra_args: list[str]) -> str:
    """One argument per line, joined with a trailing backslash."""
    parts = [f"--login-server {login_server}", f"--authkey {authkey}", *extra_args]
    return "tailscale up \\\n" + " \\\n".join(f"  {p}" for p in parts)
