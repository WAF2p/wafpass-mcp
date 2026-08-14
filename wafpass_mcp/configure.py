"""Interactive configuration helper for the stdio MCP server.

Prompts the user for the WAFpass API URL, username, and password, logs in,
and stores the resulting tokens in the user's config directory. After this
one-time setup, ``wafpass-mcp-stdio`` can run without environment variables.
"""
from __future__ import annotations

import getpass
import sys

import httpx

from wafpass_mcp.settings_store import (
    UserConfig,
    config_file,
    load_user_config,
    save_user_config,
)


def _prompt(prompt_text: str, default: str = "") -> str:
    prompt_text = (
        f"{prompt_text} [{default}]: " if default else f"{prompt_text}: "
    )
    value = input(prompt_text).strip()
    return value if value else default


def configure() -> None:
    """Interactively collect credentials and persist tokens."""
    print("WAF++ MCP configuration")
    print()

    existing = load_user_config()
    default_url = existing.api_base_url if existing else "http://localhost:8000"

    api_base_url = _prompt("WAFpass API base URL", default_url).rstrip("/")
    if not api_base_url:
        print("Error: API base URL is required.", file=sys.stderr)
        raise SystemExit(1)

    username = _prompt("Username")
    if not username:
        print("Error: username is required.", file=sys.stderr)
        raise SystemExit(1)

    password = getpass.getpass("Password: ")
    if not password:
        print("Error: password is required.", file=sys.stderr)
        raise SystemExit(1)

    login_url = f"{api_base_url}/api/v1/auth/login"
    try:
        resp = httpx.post(
            login_url,
            json={"username": username, "password": password},
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = "unknown error"
        try:
            detail = exc.response.json().get("detail", detail)
        except Exception:
            detail = exc.response.text or detail
        print(f"Login failed ({exc.response.status_code}): {detail}", file=sys.stderr)
        raise SystemExit(1) from exc
    except httpx.RequestError as exc:
        print(f"Could not reach {login_url}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if not access_token:
        print(
            "Login response did not contain an access token.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Optionally enrich the stored username from /auth/me.
    try:
        me_resp = httpx.get(
            f"{api_base_url}/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if me_resp.is_success:
            username = me_resp.json().get("username") or username
    except Exception:
        pass

    config = UserConfig(
        api_base_url=api_base_url,
        access_token=access_token,
        refresh_token=refresh_token or "",
        username=username,
    )
    save_user_config(config)

    print()
    print(f"Saved configuration to {config_file()}")
    print("Run `wafpass-mcp-stdio` to start the MCP server.")


def _entrypoint() -> None:
    configure()
