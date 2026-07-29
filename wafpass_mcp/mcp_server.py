"""MCP server implementation with dynamic tool registration.

The server is created once, tools are registered at startup from the WAFpass
OpenAPI spec, and the authenticated user context is passed through the ASGI
scope so it is available in every tool handler.
"""
from __future__ import annotations

import json
from typing import Any, cast

import httpx
import structlog
from mcp.server import Server as MCPServer
from mcp.server.sse import SseServerTransport
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from wafpass_mcp.auth import UserContext
from wafpass_mcp.config import settings
from wafpass_mcp.openapi_mapper import OpenAPIMapper, OperationMeta

logger = structlog.get_logger()


class MCPServerBridge:
    """Wraps an MCP Server and dynamically exposes WAFpass endpoints as tools."""

    def __init__(self) -> None:
        self.sse = SseServerTransport("/messages/")
        self.mapper: OpenAPIMapper | None = None
        self._operations: dict[str, OperationMeta] = {}
        self.server = MCPServer(
            "wafpass-mcp",
            on_list_tools=self._on_list_tools,
            on_call_tool=self._on_call_tool,
        )

    async def load_openapi(self) -> None:
        """Fetch WAFpass OpenAPI and build MCP tool definitions + validators."""
        async with httpx.AsyncClient(
            base_url=settings.wafpass_api_base_url, timeout=15
        ) as client:
            resp = await client.get("/openapi.json")
            resp.raise_for_status()
            spec = resp.json()

        self.mapper = OpenAPIMapper(spec)
        self._operations = self.mapper.operations
        logger.info("openapi_tools_loaded", count=len(self._operations))

    async def _on_list_tools(
        self,
        ctx: Any,  # ServerRequestContext – typed as Any to avoid SDK churn
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        """Return tools the authenticated caller is allowed to see."""
        user_ctx = self._current_user(ctx)
        tools: list[Tool] = []
        for op in self._operations.values():
            if user_ctx is None or not user_ctx.is_active:
                continue
            if op.required_role and not user_ctx.role_sufficient(op.required_role):
                continue
            tools.append(op.tool)
        return ListToolsResult(tools=tools)

    async def _on_call_tool(
        self,
        ctx: Any,
        params: CallToolRequestParams,
    ) -> CallToolResult:
        """Execute a dynamically mapped tool with user context propagation."""
        user_ctx = self._current_user(ctx)
        if user_ctx is None:
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text="Not authenticated.")],
            )

        op = self._operations.get(params.name)
        if op is None:
            return CallToolResult(
                is_error=True,
                content=[TextContent(type="text", text=f"Unknown tool: {params.name}")],
            )

        if op.required_role and not user_ctx.role_sufficient(op.required_role):
            return CallToolResult(
                is_error=True,
                content=[
                    TextContent(
                        type="text",
                        text=(
                            f"Role '{op.required_role}' or higher required "
                            f"to call {params.name}."
                        ),
                    )
                ],
            )

        # Validate and sanitize inputs using the generated Pydantic model.
        arguments = params.arguments or {}
        validated = op.request_model(**arguments).model_dump(exclude_unset=True)

        result = await self._invoke_backend(op, validated, user_ctx)
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(result, default=str, indent=2),
                )
            ]
        )

    def _current_user(self, ctx: Any) -> UserContext | None:
        """Extract the validated user context from the request scope.

        The FastAPI endpoints store the context under ``wafpass_user_context``
        in the ASGI scope before handing the streams to the MCP runner.
        """
        request = getattr(ctx, "request", None)
        if request is None:
            return None
        scope = getattr(request, "scope", None)
        if not isinstance(scope, dict):
            return None
        return scope.get("wafpass_user_context")

    async def _invoke_backend(
        self,
        op: OperationMeta,
        arguments: dict[str, Any],
        ctx: UserContext,
    ) -> dict[str, Any]:
        """Forward the validated tool call to the WAFpass backend."""
        url = op.build_url(arguments)
        body = op.extract_body(arguments)
        query = op.extract_query(arguments)

        async with httpx.AsyncClient(
            base_url=settings.wafpass_api_base_url, timeout=60
        ) as client:
            request = client.build_request(
                method=op.method,
                url=url,
                headers={
                    "Authorization": f"Bearer {ctx.access_token}",
                    "Content-Type": "application/json",
                },
                json=body if body is not None else None,
                params=query,
            )
            logger.info(
                "proxying_tool_call",
                tool=op.tool.name,
                method=op.method,
                path=url,
                user=ctx.username,
                role=ctx.role,
            )
            resp = await client.send(request)

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            logger.warning(
                "backend_error",
                status=resp.status_code,
                tool=op.tool.name,
                body=resp.text[:500],
            )
            return {
                "error": True,
                "status_code": resp.status_code,
                "detail": resp.text[:1000],
            }

        try:
            return cast(dict[str, Any], resp.json())
        except Exception:
            return {"data": resp.text}
