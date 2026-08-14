"""Authentication and authorization for the MCP bridge.

The bridge operates as an OIDC pass-through proxy:

1. The AI client connects to the MCP SSE endpoint with an
   ``Authorization: Bearer <wafpass-access-token>`` header.
2. The bridge validates that token either by introspecting the upstream
   WAFpass API (GET /api/v1/auth/me) or by locally verifying its HS256 signature.
3. The extracted user context (id, username, role, active) is attached to the
   ASGI scope and reused for every downstream tool invocation.

This keeps the bridge IdP-agnostic: Keycloak / Entra / Okta / etc. are handled
entirely by the upstream WAFpass OIDC/SAML flows. The bridge only trusts tokens
issued by WAFpass.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import jwt
import structlog
from fastapi import HTTPException, Request, status

from wafpass_mcp.config import settings

logger = structlog.get_logger()


@dataclass(frozen=True)
class UserContext:
    """Validated principal extracted from the WAFpass access token."""

    user_id: uuid.UUID
    username: str
    role: str
    is_active: bool
    # Raw token so we can proxy it to the backend on tool calls.
    access_token: str

    def headers(self) -> dict[str, str]:
        """Headers to forward to the WAFpass backend."""
        return {"Authorization": f"Bearer {self.access_token}"}

    def role_sufficient(self, minimum: str) -> bool:
        """Return True if this user's role meets *minimum* privilege."""
        return role_sufficient(self.role, minimum)


# Role ordering used by the backend (lowest privilege first).
ROLE_HIERARCHY: list[str] = ["clevel", "ciso", "architect", "engineer", "admin"]


def role_sufficient(role: str, minimum: str) -> bool:
    """Return True if *role* is at least *minimum* in the hierarchy."""
    try:
        return ROLE_HIERARCHY.index(role) >= ROLE_HIERARCHY.index(minimum)
    except ValueError:
        return False


async def _introspect_token(token: str) -> dict[str, Any]:
    """Validate token by calling WAFpass /api/v1/auth/me."""
    async with httpx.AsyncClient(
        base_url=settings.wafpass_api_base_url, timeout=10
    ) as client:
        resp = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
    if resp.status_code == status.HTTP_401_UNAUTHORIZED:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token."
        )
    resp.raise_for_status()
    return cast(dict[str, Any], resp.json())


def _decode_local_token(token: str) -> dict[str, Any]:
    """Validate an HS256 WAFpass token locally."""
    if not settings.wafpass_jwt_secret:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="WAFPASS_JWT_SECRET not configured for local JWT mode.",
        )
    try:
        payload = jwt.decode(
            token, settings.wafpass_jwt_secret, algorithms=["HS256"]
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {exc}"
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Token is not an access token."
        )
    return payload


def token_expiry(token: str) -> datetime | None:
    """Return the access token's ``exp`` claim as an aware UTC datetime.

    The token has already been validated at this point, so we decode without
    verification to avoid needing the JWT secret in introspection mode.
    """
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return None
    return datetime.fromtimestamp(exp, tz=UTC)


async def refresh_access_token(refresh_token: str) -> tuple[str, str]:
    """Exchange a refresh token for a new access/refresh token pair.

    Returns ``(new_access_token, new_refresh_token)``. Raises HTTPException(401)
    when the upstream server rejects the refresh token.
    """
    async with httpx.AsyncClient(
        base_url=settings.wafpass_api_base_url, timeout=10
    ) as client:
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )

    if resp.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Refresh token invalid or expired."
        )
    resp.raise_for_status()

    data = cast(dict[str, Any], resp.json())
    new_access = data.get("access_token")
    new_refresh = data.get("refresh_token")
    if not new_access or not new_refresh:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upstream /api/v1/auth/refresh returned an incomplete token response.",
        )
    return new_access, new_refresh


async def validate_token(token: str) -> UserContext:
    """Validate a Bearer token and return the user context."""
    if settings.wafpass_token_mode == "introspection":
        data = await _introspect_token(token)
    else:
        data = _decode_local_token(token)

    user_id = data.get("id") or data.get("sub")
    if not user_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Token missing user identifier."
        )

    return UserContext(
        user_id=uuid.UUID(str(user_id)),
        username=data.get("username", ""),
        role=data.get("role", "clevel"),
        is_active=data.get("is_active", True),
        access_token=token,
    )


async def require_user_context(request: Request) -> UserContext:
    """Extract and validate the Bearer token from a request."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization: Bearer header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[7:].strip()
    ctx = await validate_token(token)
    if not ctx.is_active:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="User account is inactive."
        )
    return ctx
