"""Integration tests for the MCP bridge startup and HTTP endpoints."""

from __future__ import annotations

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from wafpass_mcp.auth import UserContext
from wafpass_mcp.main import app

pytestmark = pytest.mark.httpx_mock(assert_all_responses_were_requested=False)


@pytest.fixture
def client(httpx_mock: Any) -> TestClient:
    """Provide a TestClient with the WAFpass OpenAPI endpoint mocked."""
    httpx_mock.add_response(
        url="http://localhost:8000/openapi.json",
        json={
            "openapi": "3.1.0",
            "info": {"title": "WAFpass", "version": "1.0"},
            "paths": {
                "/runs": {
                    "get": {
                        "operationId": "get_runs",
                        "summary": "List runs",
                        "parameters": [],
                    }
                },
                "/health": {
                    "get": {"operationId": "get_health", "summary": "Health check"}
                },
            },
        },
    )
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_sse_rejects_missing_auth(client: TestClient) -> None:
    """Unauthenticated SSE requests are rejected with 401 before MCP starts."""
    response = client.get("/sse")
    assert response.status_code == 401


def _make_token_claims(role: str = "admin") -> dict[str, Any]:
    return {
        "sub": "00000000-0000-0000-0000-000000000000",
        "username": "tester",
        "role": role,
        "type": "access",
        "is_active": True,
    }


@pytest.mark.anyio
async def test_call_tool_explains_backend_error() -> None:
    """A backend HTTP error is translated by the explain layer."""
    from wafpass_mcp.mcp_server import MCPServerBridge

    bridge = MCPServerBridge(sse=False)
    bridge.mapper = None
    bridge._operations = {
        "get_runs": type(
            "Op",
            (object,),
            {
                "tool": type("Tool", (object,), {"name": "get_runs"})(),
                "method": "GET",
                "path": "/runs",
                "request_model": type(
                    "Model",
                    (object,),
                    {"model_dump": lambda self, **kwargs: {}},
                )(),
                "path_param_names": set(),
                "query_param_names": set(),
                "required_role": None,
                "build_url": lambda self, args: "/runs",
                "extract_body": lambda self, args: None,
                "extract_query": lambda self, args: {},
            },
        )()
    }
    ctx = UserContext(
        user_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000000"),
        username="tester",
        role="admin",
        is_active=True,
        access_token="tok",
    )

    with respx.mock(base_url="http://localhost:8000") as routes:
        routes.get("/runs").mock(return_value=Response(403, text="Forbidden"))
        result = await bridge._invoke_backend(bridge._operations["get_runs"], {}, ctx)

    explained = bridge.explain_response("get_runs", result)
    assert explained["error"] is True
    assert explained["status_code"] == 403
    assert "not authorized" in explained["explanation"]


@pytest.mark.anyio
async def test_call_tool_passthrough_for_non_error() -> None:
    """A successful backend response is returned unchanged."""
    from wafpass_mcp.mcp_server import MCPServerBridge

    bridge = MCPServerBridge(sse=False)
    bridge.mapper = None
    bridge._operations = {
        "get_health": type(
            "Op",
            (object,),
            {
                "tool": type("Tool", (object,), {"name": "get_health"})(),
                "method": "GET",
                "path": "/health",
                "request_model": type(
                    "Model", (object,), {"model_dump": lambda self, **kwargs: {}}
                )(),
                "path_param_names": set(),
                "query_param_names": set(),
                "required_role": None,
                "build_url": lambda self, args: "/health",
                "extract_body": lambda self, args: None,
                "extract_query": lambda self, args: {},
            },
        )()
    }
    ctx = UserContext(
        user_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000000"),
        username="tester",
        role="admin",
        is_active=True,
        access_token="tok",
    )

    with respx.mock(base_url="http://localhost:8000") as routes:
        routes.get("/health").mock(return_value=Response(200, json={"status": "ok"}))
        result = await bridge._invoke_backend(bridge._operations["get_health"], {}, ctx)

    explained = bridge.explain_response("get_health", result)
    assert explained == {"status": "ok"}
