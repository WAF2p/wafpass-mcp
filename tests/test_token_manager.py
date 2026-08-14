"""Tests for access-token refresh in long-lived stdio sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from wafpass_mcp.token_manager import TokenManager

pytestmark = pytest.mark.asyncio


def _jwt_with_exp(exp: datetime) -> str:
    """Build an unsigned HS256-shaped JWT for testing token_expiry parsing.

    The bridge decodes without verification, so the signature is irrelevant.
    """
    import base64
    import json

    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(exp.timestamp()), "sub": "user-1"}).encode()
    ).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.dummy-signature"


@pytest.fixture
def far_future_token() -> str:
    return _jwt_with_exp(datetime.now(tz=UTC) + timedelta(hours=1))


@pytest.fixture
def near_expiry_token() -> str:
    return _jwt_with_exp(datetime.now(tz=UTC) + timedelta(seconds=60))


@pytest.fixture
def expired_token() -> str:
    return _jwt_with_exp(datetime.now(tz=UTC) - timedelta(seconds=10))


@pytest.fixture
def invalid_token() -> str:
    return "not.a.jwt"


async def test_token_not_refreshed_when_far_from_expiry(
    far_future_token: str, monkeypatch: Any
) -> None:
    refresh = AsyncMock(return_value=("new-access", "new-refresh"))
    monkeypatch.setattr(
        "wafpass_mcp.token_manager.refresh_access_token",
        refresh,
    )

    manager = TokenManager(
        far_future_token, "old-refresh", threshold=timedelta(seconds=300)
    )
    token = await manager.get_access_token()

    assert token == far_future_token
    refresh.assert_not_awaited()


async def test_token_refreshed_near_expiry(
    near_expiry_token: str, monkeypatch: Any
) -> None:
    refresh = AsyncMock(return_value=("new-access", "new-refresh"))
    monkeypatch.setattr(
        "wafpass_mcp.token_manager.refresh_access_token",
        refresh,
    )

    manager = TokenManager(
        near_expiry_token, "old-refresh", threshold=timedelta(seconds=300)
    )
    token = await manager.get_access_token()

    assert token == "new-access"
    refresh.assert_awaited_once_with("old-refresh")


async def test_refresh_token_rotated(near_expiry_token: str, monkeypatch: Any) -> None:
    refresh = AsyncMock(return_value=("new-access", "new-refresh"))
    monkeypatch.setattr(
        "wafpass_mcp.token_manager.refresh_access_token",
        refresh,
    )

    manager = TokenManager(
        near_expiry_token, "old-refresh", threshold=timedelta(seconds=300)
    )
    await manager.get_access_token()

    # A second call within the same short-lived test still has the new far-future
    # token, so no further refresh should occur.
    refresh.reset_mock()
    second = await manager.get_access_token()
    assert second == "new-access"
    refresh.assert_not_awaited()


async def test_no_refresh_without_refresh_token(
    near_expiry_token: str, monkeypatch: Any
) -> None:
    refresh = AsyncMock(return_value=("new-access", "new-refresh"))
    monkeypatch.setattr(
        "wafpass_mcp.token_manager.refresh_access_token",
        refresh,
    )

    manager = TokenManager(near_expiry_token, "", threshold=timedelta(seconds=300))
    token = await manager.get_access_token()

    assert token == near_expiry_token
    refresh.assert_not_awaited()


async def test_refresh_failure_raises(near_expiry_token: str, monkeypatch: Any) -> None:
    refresh = AsyncMock(side_effect=Exception("upstream rejected"))
    monkeypatch.setattr(
        "wafpass_mcp.token_manager.refresh_access_token",
        refresh,
    )

    manager = TokenManager(
        near_expiry_token, "old-refresh", threshold=timedelta(seconds=300)
    )
    with pytest.raises(Exception, match="upstream rejected"):
        await manager.get_access_token()
