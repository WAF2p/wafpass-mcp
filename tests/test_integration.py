"""Integration tests for the MCP bridge startup and HTTP endpoints."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

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
