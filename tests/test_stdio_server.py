"""Tests for the stdio server credential resolution."""
from __future__ import annotations

import pytest

from wafpass_mcp.settings_store import UserConfig
from wafpass_mcp.stdio_server import _resolve_stdio_credentials


def test_env_token_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WAFPASS_ACCESS_TOKEN", "env-token")
    monkeypatch.setenv("WAFPASS_REFRESH_TOKEN", "env-refresh")

    token, refresh = _resolve_stdio_credentials()
    assert token == "env-token"
    assert refresh == "env-refresh"


def test_config_file_used_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WAFPASS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WAFPASS_REFRESH_TOKEN", raising=False)

    fake_config = UserConfig(
        api_base_url="http://config:8000",
        access_token="config-token",
        refresh_token="config-refresh",
        log_level="DEBUG",
    )
    monkeypatch.setattr(
        "wafpass_mcp.stdio_server.load_user_config", lambda: fake_config
    )

    token, refresh = _resolve_stdio_credentials()
    assert token == "config-token"
    assert refresh == "config-refresh"

    from wafpass_mcp.config import settings

    assert settings.wafpass_api_base_url == "http://config:8000"
    assert settings.log_level == "DEBUG"


def test_missing_env_and_config_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WAFPASS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WAFPASS_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("wafpass_mcp.stdio_server.load_user_config", lambda: None)

    token, refresh = _resolve_stdio_credentials()
    assert token == ""
    assert refresh == ""
