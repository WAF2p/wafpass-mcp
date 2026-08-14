"""Tests for the user settings store."""
from __future__ import annotations

from pathlib import Path

import pytest

from wafpass_mcp.settings_store import (
    UserConfig,
    config_file,
    load_user_config,
    save_user_config,
)


@pytest.fixture
def temp_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the settings file into a temporary directory."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(
        "wafpass_mcp.settings_store._CONFIG_FILE",
        path,
    )
    return path


def test_save_and_load_roundtrip(temp_config_file: Path) -> None:
    config = UserConfig(
        api_base_url="http://example:8000",
        access_token="at",
        refresh_token="rt",
        username="alice",
        log_level="DEBUG",
    )
    save_user_config(config)
    loaded = load_user_config()
    assert loaded is not None
    assert loaded.api_base_url == "http://example:8000"
    assert loaded.access_token == "at"
    assert loaded.refresh_token == "rt"
    assert loaded.username == "alice"
    assert loaded.log_level == "DEBUG"
    assert loaded.token_mode == "introspection"


def test_load_missing_config_returns_none(temp_config_file: Path) -> None:
    assert load_user_config() is None


def test_apply_to_env_sets_variables(
    temp_config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WAFPASS_API_BASE_URL", raising=False)
    monkeypatch.delenv("WAFPASS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WAFPASS_REFRESH_TOKEN", raising=False)

    config = UserConfig(
        api_base_url="http://setenv:8000",
        access_token="tok",
        refresh_token="reftok",
    )
    config.apply_to_env()

    import os

    assert os.environ["WAFPASS_API_BASE_URL"] == "http://setenv:8000"
    assert os.environ["WAFPASS_ACCESS_TOKEN"] == "tok"
    assert os.environ["WAFPASS_REFRESH_TOKEN"] == "reftok"


def test_config_file_path(temp_config_file: Path) -> None:
    assert config_file() == temp_config_file
