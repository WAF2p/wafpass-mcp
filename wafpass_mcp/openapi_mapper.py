"""Convert a WAFpass OpenAPI specification into MCP tool definitions.

The mapper is intentionally conservative: it only exposes endpoints that are
safe for an AI assistant to call and that map cleanly to JSON arguments.
Binary/streaming endpoints and auth callbacks are skipped. Every exposed
operation gets a strict Pydantic input validator derived from its OpenAPI
schema.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from mcp.types import Tool
from pydantic import BaseModel, Field, create_model
from pydantic.fields import FieldInfo

_OPENAPI_TYPES_TO_PYTHON: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

# Map known WAFpass endpoints to minimum required roles.
# Mirrors the backend role matrix in wafpass-server/README.md.
ROLE_MAP: dict[tuple[str, str], str] = {
    ("GET", "/runs"): "clevel",
    ("GET", "/runs/{id}"): "clevel",
    ("POST", "/runs"): "engineer",
    ("GET", "/runs/{id}/controls"): "clevel",
    ("GET", "/runs/{id}/findings"): "clevel",
    ("GET", "/controls"): "clevel",
    ("POST", "/controls"): "architect",
    ("GET", "/controls/{id}"): "clevel",
    ("DELETE", "/controls/{id}"): "architect",
    ("GET", "/waivers"): "clevel",
    ("PUT", "/waivers/{id}"): "ciso",
    ("DELETE", "/waivers/{id}"): "ciso",
    ("GET", "/risks"): "clevel",
    ("PUT", "/risks/{id}"): "ciso",
    ("DELETE", "/risks/{id}"): "ciso",
    ("GET", "/auth/users"): "engineer",
    ("POST", "/auth/users"): "engineer",
    ("PUT", "/auth/users/{id}"): "engineer",
    ("DELETE", "/auth/users/{id}"): "engineer",
    ("GET", "/sso/config"): "admin",
    ("PUT", "/sso/config/{provider}"): "admin",
    ("POST", "/evidence"): "engineer",
    ("GET", "/evidence"): "clevel",
    ("GET", "/projects/passports"): "clevel",
    ("PUT", "/projects/{project}/passport"): "architect",
    ("POST", "/sandbox"): "architect",
    ("GET", "/sandbox/status"): "architect",
    ("POST", "/widgets"): "engineer",
    ("GET", "/leaderboard"): "clevel",
    ("GET", "/achievements"): "clevel",
}

# Operations we never expose through the MCP bridge (auth flows, callbacks).
SKIP_OPERATIONS: set[tuple[str, str]] = {
    ("GET", "/auth/oidc/authorize"),
    ("GET", "/auth/oidc/callback"),
    ("GET", "/auth/saml/login"),
    ("POST", "/auth/saml/acs"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/saml/metadata"),
}


def _tool_name(method: str, path: str) -> str:
    """Create a stable, human-readable MCP tool name."""
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", path).strip("_").lower()
    return f"{method.lower()}_{clean}"


def _schema_to_field(
    name: str, schema: dict[str, Any], required: bool
) -> tuple[type | None, FieldInfo]:
    """Convert an OpenAPI property schema to a Pydantic field tuple."""
    openapi_type = schema.get("type", "string")
    py_type: type = _OPENAPI_TYPES_TO_PYTHON.get(openapi_type, str)

    if openapi_type == "array":
        items = schema.get("items", {})
        item_type = _OPENAPI_TYPES_TO_PYTHON.get(items.get("type"), Any)
        py_type = list[item_type]  # type: ignore[valid-type]
    elif openapi_type == "object":
        py_type = dict[str, Any]

    description = schema.get("description", "")

    if required:
        return (py_type, Field(description=description))
    optional_field = cast(
        tuple[type | None, FieldInfo],
        (py_type, Field(default=None, description=description)),
    )
    return optional_field


def _build_request_model(
    model_name: str,
    path_param_names: set[str],
    query_params: list[dict[str, Any]],
    request_body: dict[str, Any] | None,
) -> type[BaseModel]:
    """Create a Pydantic model that validates arguments for one operation."""
    fields: dict[str, tuple[type | None, FieldInfo]] = {}

    for param in query_params:
        name = param["name"]
        schema = param.get("schema", {})
        required = param.get("required", False)
        fields[name] = _schema_to_field(name, schema, required)

    if request_body:
        body_schema = (
            request_body.get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        required_props = set(body_schema.get("required", []))
        for prop_name, prop_schema in body_schema.get("properties", {}).items():
            fields[prop_name] = _schema_to_field(
                prop_name, prop_schema, prop_name in required_props
            )
        for prop_name in required_props:
            if prop_name not in fields:
                fields[prop_name] = cast(
                    tuple[type | None, FieldInfo],
                    (dict[str, Any], Field(description="Required body field")),
                )

    return create_model(model_name, **fields, __base__=BaseModel)  # type: ignore[no-any-return,call-overload]


@dataclass(frozen=True)
class OperationMeta:
    """Runtime metadata for one mapped OpenAPI operation."""

    method: str
    path: str
    tool: Tool
    request_model: type[BaseModel]
    required_role: str | None
    path_param_names: set[str]
    query_param_names: set[str]

    def build_url(self, arguments: dict[str, Any]) -> str:
        """Substitute path parameters into the operation path."""
        url = self.path
        for key, value in arguments.items():
            placeholder = "{" + key + "}"
            if placeholder in url:
                url = url.replace(placeholder, str(value))
        return url

    def extract_body(self, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Return request-body fields for mutating methods."""
        if self.method not in {"POST", "PUT", "PATCH"}:
            return None
        body_fields = {
            k: v
            for k, v in arguments.items()
            if k not in self.path_param_names
            and k not in self.query_param_names
            and v is not None
        }
        return body_fields if body_fields else None

    def extract_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return query parameters for safe/lookup methods."""
        if self.method not in {"GET", "DELETE"}:
            return {}
        return {
            k: v
            for k, v in arguments.items()
            if k in self.query_param_names and v is not None
        }


class OpenAPIMapper:
    """Parse an OpenAPI spec and produce a registry of MCP-exposable operations."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.operations: dict[str, OperationMeta] = {}
        self._map()

    def _map(self) -> None:
        paths = self.spec.get("paths", {})
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, details in methods.items():
                method = method.upper()
                if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                if (method, path) in SKIP_OPERATIONS:
                    continue
                if not isinstance(details, dict):
                    continue

                operation_id = details.get("operationId") or _tool_name(method, path)
                tool_name = operation_id
                summary = (
                    details.get("summary", "")
                    or details.get("description", "")
                    or tool_name
                )
                parameters = details.get("parameters", []) or []
                request_body = details.get("requestBody")

                path_param_names = {
                    p["name"] for p in parameters if p.get("in") == "path"
                }
                query_params = [p for p in parameters if p.get("in") == "query"]
                query_param_names = {p["name"] for p in query_params}

                request_model = _build_request_model(
                    tool_name, path_param_names, query_params, request_body
                )
                required_role = ROLE_MAP.get((method, path))

                tool = Tool(
                    name=tool_name,
                    description=summary,
                    input_schema=request_model.model_json_schema(),
                )

                self.operations[tool_name] = OperationMeta(
                    method=method,
                    path=path,
                    tool=tool,
                    request_model=request_model,
                    required_role=required_role,
                    path_param_names=path_param_names,
                    query_param_names=query_param_names,
                )
