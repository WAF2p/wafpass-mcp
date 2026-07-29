"""Tests for the OpenAPI-to-MCP tool mapper."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from wafpass_mcp.openapi_mapper import OpenAPIMapper


@pytest.fixture
def sample_spec() -> dict[str, object]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0"},
        "paths": {
            "/runs": {
                "get": {
                    "operationId": "list_runs",
                    "summary": "List compliance runs",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "default": 50},
                        }
                    ],
                },
                "post": {
                    "operationId": "create_run",
                    "summary": "Ingest a run result",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "project": {"type": "string"},
                                        "score": {"type": "integer"},
                                    },
                                    "required": ["project"],
                                }
                            }
                        }
                    },
                },
            },
            "/runs/{id}": {
                "get": {
                    "operationId": "get_run",
                    "summary": "Get a run",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                }
            },
            "/auth/login": {
                "post": {"operationId": "login", "summary": "Local login"}
            },
        },
    }


def test_mapper_exposes_expected_tools(sample_spec: dict[str, object]) -> None:
    mapper = OpenAPIMapper(sample_spec)
    names = set(mapper.operations.keys())
    assert "list_runs" in names
    assert "create_run" in names
    assert "get_run" in names
    assert "login" not in names


def test_required_role_for_runs(sample_spec: dict[str, object]) -> None:
    mapper = OpenAPIMapper(sample_spec)
    assert mapper.operations["list_runs"].required_role == "clevel"
    assert mapper.operations["create_run"].required_role == "engineer"


def test_request_model_enforces_required_body_field(
    sample_spec: dict[str, object],
) -> None:
    mapper = OpenAPIMapper(sample_spec)
    model = mapper.operations["create_run"].request_model
    # Missing required field should raise ValidationError.
    with pytest.raises(ValidationError):
        model(score=80)

    valid = model(project="my-app", score=80)
    assert valid.model_dump() == {"project": "my-app", "score": 80}


def test_url_building_substitutes_path_params(
    sample_spec: dict[str, object],
) -> None:
    mapper = OpenAPIMapper(sample_spec)
    op = mapper.operations["get_run"]
    url = op.build_url({"id": "abc-123"})
    assert url == "/runs/abc-123"
