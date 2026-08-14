"""Tests for the interactive configuration helper."""
from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from wafpass_mcp.configure import configure
from wafpass_mcp.settings_store import load_user_config


@pytest.fixture
def temp_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the settings file into a temporary directory."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(
        "wafpass_mcp.settings_store._CONFIG_FILE",
        path,
    )
    return path


def _fixed_inputs(
    monkeypatch: pytest.MonkeyPatch, values: list[str]
) -> None:
    """Replace ``input`` and ``getpass.getpass`` with a deterministic queue."""
    queue = list(values)

    def fake_input(_prompt: str = "") -> str:
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr("getpass.getpass", fake_input)


def test_configure_saves_tokens_after_successful_login(
    temp_config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    httpx_mock.add_response(
        url="http://example.com/api/v1/auth/login",
        json={
            "access_token": "access-123",
            "refresh_token": "refresh-456",
            "user": {"id": "u1", "username": "alice"},
        },
    )
    httpx_mock.add_response(
        url="http://example.com/api/v1/auth/me",
        json={"id": "u1", "username": "alice"},
    )

    _fixed_inputs(monkeypatch, ["http://example.com", "alice", "secret"])
    configure()

    config = load_user_config()
    assert config is not None
    assert config.api_base_url == "http://example.com"
    assert config.access_token == "access-123"
    assert config.refresh_token == "refresh-456"
    assert config.username == "alice"

    captured = capsys.readouterr()
    assert "Saved configuration" in captured.out
    assert "wafpass-mcp-stdio" in captured.out


def test_configure_fails_on_bad_credentials(
    temp_config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="http://example.com/api/v1/auth/login",
        status_code=401,
        json={"detail": "Invalid credentials"},
    )

    _fixed_inputs(monkeypatch, ["http://example.com", "alice", "wrong"])
    with pytest.raises(SystemExit) as exc_info:
        configure()
    assert exc_info.value.code == 1
    assert load_user_config() is None


def test_configure_requires_username(
    temp_config_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_inputs(monkeypatch, ["http://example.com", ""])
    with pytest.raises(SystemExit) as exc_info:
        configure()
    assert exc_info.value.code == 1
