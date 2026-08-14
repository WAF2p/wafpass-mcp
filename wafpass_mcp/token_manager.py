"""Access-token rotation for long-lived stdio MCP sessions.

In stdio mode the bridge runs as a child process of an MCP host such as Claude
Desktop. The access token supplied at startup eventually expires, so this module
uses a refresh token to obtain a new access token before the old one expires.

The refresh token is rotated by the upstream WAFpass server: a successful
``POST /auth/refresh`` returns both a new access token and a new refresh token.
Both are stored only in memory.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from wafpass_mcp.auth import refresh_access_token, token_expiry

logger = structlog.get_logger()


class TokenManager:
    """Keeps a valid access token in memory by refreshing it before expiry."""

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        threshold: timedelta = timedelta(seconds=300),
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._threshold = threshold
        self._lock = asyncio.Lock()

    @property
    def access_token(self) -> str:
        """Current access token (may be expired; use ``get_access_token``)."""
        return self._access_token

    def _should_refresh(self) -> bool:
        """Return True if the current token is missing or about to expire."""
        expiry = token_expiry(self._access_token)
        if expiry is None:
            logger.debug("no_token_expiry", refresh_threshold=str(self._threshold))
            return False
        now = datetime.now(tz=UTC)
        remaining = expiry - now
        logger.debug(
            "token_expiry_check",
            expires_at=expiry.isoformat(),
            remaining_seconds=remaining.total_seconds(),
            threshold_seconds=self._threshold.total_seconds(),
        )
        return remaining <= self._threshold

    async def get_access_token(self) -> str:
        """Return a non-expired access token, refreshing if necessary."""
        if not self._refresh_token:
            return self._access_token

        async with self._lock:
            if self._should_refresh():
                logger.info("refreshing_access_token")
                new_access, new_refresh = await refresh_access_token(
                    self._refresh_token
                )
                self._access_token = new_access
                self._refresh_token = new_refresh
                logger.info("access_token_refreshed")
            return self._access_token
