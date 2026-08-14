"""Tests for the OpenAPI-to-MCP tool mapper."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from wafpass_mcp.openapi_mapper import OpenAPIMapper


@pytest.fixture
def sample_spec() -> dict[str, object]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Test API", "version": "1.0"},
        "paths": {
            "/api/v1/runs": {
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
                    "tags": ["runs"],
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
            "/api/v1/runs/{id}": {
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
            "/api/v1/auth/login": {
                "post": {"operationId": "login", "summary": "Local login"}
            },
            "/api/v1/auth/api-keys": {
                "get": {"operationId": "list_api_keys", "summary": "List API keys"}
            },
            "/api/v1/auth/users/{user_id}/logs": {
                "get": {"operationId": "get_user_logs", "summary": "Get user logs"}
            },
            "/api/v1/sso/config": {
                "get": {
                    "operationId": "list_sso_configs",
                    "summary": "List SSO configs",
                }
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
    assert "list_api_keys" not in names
    assert "get_user_logs" not in names
    assert "list_sso_configs" not in names


def test_required_role_for_runs(sample_spec: dict[str, object]) -> None:
    mapper = OpenAPIMapper(sample_spec)
    assert mapper.operations["list_runs"].required_role == "clevel"
    assert mapper.operations["create_run"].required_role == "engineer"


def test_tool_description_includes_category_and_role(
    sample_spec: dict[str, object],
) -> None:
    """Descriptions repeat category and role for clients that don't render meta."""
    mapper = OpenAPIMapper(sample_spec)
    create_tool = mapper.operations["create_run"].tool
    assert create_tool.description is not None
    assert "Ingest a run result" in create_tool.description
    assert "Category: runs" in create_tool.description
    assert "Minimum role required: engineer" in create_tool.description
    assert create_tool.meta is not None
    assert create_tool.meta.get("category") == "runs"
    assert create_tool.meta.get("required_role") == "engineer"

    list_tool = mapper.operations["list_runs"].tool
    assert list_tool.description is not None
    assert "Minimum role required: clevel" in list_tool.description


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


def test_request_model_handles_nullable_anyof(
    sample_spec: dict[str, object],
) -> None:
    """OpenAPI ``anyOf`` with a null branch must map to an optional Pydantic field."""
    spec: dict[str, Any] = dict(sample_spec)
    spec["paths"]["/api/v1/auto-fix/classify"] = {
        "post": {
            "operationId": "classify_findings",
            "summary": "Classify findings",
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "run_id": {
                                    "anyOf": [
                                        {"type": "string"},
                                        {"type": "null"},
                                    ]
                                },
                                "control_ids": {
                                    "anyOf": [
                                        {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        {"type": "null"},
                                    ]
                                },
                                "findings": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["findings"],
                        }
                    }
                }
            },
        }
    }
    mapper = OpenAPIMapper(spec)
    model = mapper.operations["classify_findings"].request_model

    # Passing an explicit list for a nullable array field must work.
    valid = model(
        run_id=None,
        control_ids=["WAF-SOV-070"],
        findings=["f1"],
    )
    dumped = valid.model_dump()
    assert dumped["run_id"] is None
    assert dumped["control_ids"] == ["WAF-SOV-070"]
    assert dumped["findings"] == ["f1"]


def test_request_model_resolves_body_refs(sample_spec: dict[str, object]) -> None:
    """Request bodies that use ``$ref`` must expose the referenced schema fields."""
    spec: dict[str, Any] = dict(sample_spec)
    spec["components"] = {
        "schemas": {
            "ClassifyRequest": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "findings": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["findings"],
            }
        }
    }
    spec["paths"]["/api/v1/auto-fix/classify"] = {
        "post": {
            "operationId": "classify_findings",
            "summary": "Classify findings",
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ClassifyRequest"}
                    }
                }
            },
        }
    }
    mapper = OpenAPIMapper(spec)
    model = mapper.operations["classify_findings"].request_model
    with pytest.raises(ValidationError):
        model(run_id="abc")

    valid = model(run_id="abc", findings=["f1"])
    assert valid.model_dump() == {"run_id": "abc", "findings": ["f1"]}


def test_url_building_substitutes_path_params(
    sample_spec: dict[str, object],
) -> None:
    mapper = OpenAPIMapper(sample_spec)
    op = mapper.operations["get_run"]
    url = op.build_url({"id": "abc-123"})
    # The full versioned path is preserved for proxying to the backend.
    assert url == "/api/v1/runs/abc-123"


def test_request_model_includes_required_path_params(
    sample_spec: dict[str, object],
) -> None:
    """Path parameters must survive Pydantic validation so build_url can use them."""
    mapper = OpenAPIMapper(sample_spec)
    op = mapper.operations["get_run"]
    model = op.request_model

    with pytest.raises(ValidationError):
        model()

    validated = model(id="abc-123").model_dump(exclude_unset=True)
    assert validated == {"id": "abc-123"}
    assert op.build_url(validated) == "/api/v1/runs/abc-123"


def test_versioned_paths_still_use_role_map(
    sample_spec: dict[str, object],
) -> None:
    """Role gating must match even when the spec includes the /api/v1 prefix."""
    mapper = OpenAPIMapper(sample_spec)
    assert mapper.operations["list_runs"].required_role == "clevel"
    assert mapper.operations["create_run"].required_role == "engineer"


def test_tools_are_grouped_by_openapi_tag(
    sample_spec: dict[str, object],
) -> None:
    """OpenAPI tags should be surfaced in tool meta without changing the title."""
    mapper = OpenAPIMapper(sample_spec)
    create_tool = mapper.operations["create_run"].tool
    assert create_tool.title == "Ingest a run result"
    assert create_tool.meta is not None
    assert create_tool.meta.get("category") == "runs"
    # Operations without tags still carry role info.
    list_tool = mapper.operations["list_runs"].tool
    assert list_tool.title == "List compliance runs"
    assert list_tool.meta is not None
    assert list_tool.meta.get("category") is None
    assert list_tool.meta.get("required_role") == "clevel"


def test_operation_hints_reflect_http_method(
    sample_spec: dict[str, object],
) -> None:
    """Read-only vs destructive hints let Claude Desktop group by operation type."""
    mapper = OpenAPIMapper(sample_spec)
    list_annotations = mapper.operations["list_runs"].tool.annotations
    assert list_annotations is not None
    assert list_annotations.read_only_hint is True
    assert list_annotations.destructive_hint is False
    create_annotations = mapper.operations["create_run"].tool.annotations
    assert create_annotations is not None
    assert create_annotations.read_only_hint is False
    assert create_annotations.destructive_hint is True
