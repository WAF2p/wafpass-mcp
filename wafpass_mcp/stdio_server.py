"""stdio MCP server entry point for local clients such as Claude Desktop.

This runs the same dynamic MCP server as the HTTP/SSE bridge, but communicates
over stdin/stdout. The WAF++ access token is supplied via the
``WAFPASS_ACCESS_TOKEN`` environment variable and validated once at startup.

Because stdout is reserved for the MCP protocol, all logging is directed to
stderr.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import timedelta

import structlog
from mcp.server.stdio import stdio_server

from wafpass_mcp.auth import UserContext, refresh_access_token, validate_token
from wafpass_mcp.config import settings
from wafpass_mcp.mcp_server import MCPServerBridge
from wafpass_mcp.token_manager import TokenManager

logger = structlog.get_logger()


def _configure_logging() -> None:
    """Set up logging on stderr so stdout stays clean for MCP messages."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(message)s",
        stream=sys.stderr,
    )
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


async def main() -> None:
    _configure_logging()

    token = os.environ.get("WAFPASS_ACCESS_TOKEN")
    if not token:
        logger.error("missing_access_token")
        raise SystemExit(
            "WAFPASS_ACCESS_TOKEN environment variable is required for stdio mode."
        )

    refresh_token = os.environ.get("WAFPASS_REFRESH_TOKEN")

    logger.info("validating_token")
    try:
        ctx: UserContext = await validate_token(token)
    except Exception as exc:
        if not refresh_token:
            logger.exception("token_validation_failed_no_refresh_token")
            raise SystemExit(
                "Access token is invalid or expired and no "
                "WAFPASS_REFRESH_TOKEN is set. Please log in again through "
                "the WAFpass server and update Claude Desktop's environment."
            ) from exc
        logger.info("access_token_rejected_attempting_refresh")
        try:
            token, _ = await refresh_access_token(refresh_token)
        except Exception as exc:
            logger.exception("refresh_token_failed")
            raise SystemExit(
                "Access token and refresh token are both invalid or expired. "
                "Please log in again through the WAFpass server and update "
                "Claude Desktop's environment."
            ) from exc
        ctx = await validate_token(token)

    if not ctx.is_active:
        logger.error("inactive_user", user=ctx.username)
        raise SystemExit("User account is inactive.")

    token_manager: TokenManager | None = None
    if refresh_token:
        token_manager = TokenManager(
            access_token=token,
            refresh_token=refresh_token,
            threshold=timedelta(seconds=settings.wafpass_refresh_threshold_seconds),
        )
        logger.info(
            "token_refresh_enabled",
            threshold_seconds=settings.wafpass_refresh_threshold_seconds,
        )
    else:
        logger.warning("token_refresh_disabled", reason="WAFPASS_REFRESH_TOKEN not set")

    bridge = MCPServerBridge(sse=False, token_manager=token_manager)
    await bridge.load_openapi()
    bridge.set_user_context(ctx)

    logger.info(
        "stdio_bridge_ready",
        user=ctx.username,
        role=ctx.role,
        tools=len(bridge.operations),
    )

    async with stdio_server() as (read_stream, write_stream):
        await bridge.server.run(
            read_stream,
            write_stream,
            bridge.server.create_initialization_options(),
            raise_exceptions=True,
        )


def _entrypoint() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    _entrypoint()
