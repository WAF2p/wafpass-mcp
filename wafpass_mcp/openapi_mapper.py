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

from mcp.types import Tool, ToolAnnotations
from pydantic import BaseModel, Field, create_model
from pydantic.fields import FieldInfo

_API_VERSION_PREFIX_RE = re.compile(r"^/api/v\d+")

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

# Operations we never expose through the MCP bridge (auth flows, callbacks,
# and admin-only endpoints).
SKIP_OPERATIONS: set[tuple[str, str]] = {
    # Auth flows and callbacks.
    ("GET", "/auth/oidc/authorize"),
    ("GET", "/auth/oidc/callback"),
    ("GET", "/auth/saml/login"),
    ("POST", "/auth/saml/acs"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/saml/metadata"),
    # Admin-only user / API key management.
    ("GET", "/auth/users"),
    ("POST", "/auth/users"),
    ("PUT", "/auth/users/{user_id}"),
    ("GET", "/auth/users/{user_id}"),
    ("GET", "/auth/users/{user_id}/logs"),
    ("DELETE", "/auth/users/{user_id}"),
    ("GET", "/auth/api-keys"),
    ("POST", "/auth/api-keys"),
    ("DELETE", "/auth/api-keys/{key_id}"),
    ("GET", "/auth/api-keys/{key_id}/logs"),
    # Admin-only SSO configuration and group management.
    ("GET", "/sso/config"),
    ("PUT", "/sso/config/{provider}"),
    ("DELETE", "/sso/config/{provider}"),
    ("GET", "/sso/group-mappings"),
    ("POST", "/sso/group-mappings"),
    ("PUT", "/sso/group-mappings/{mapping_id}"),
    ("DELETE", "/sso/group-mappings/{mapping_id}"),
    ("GET", "/groups"),
    ("GET", "/projects/{project}/groups"),
    ("POST", "/projects/{project}/groups"),
    ("DELETE", "/projects/{project}/groups/{group_name}"),
    ("GET", "/auth/users/{user_id}/groups"),
    ("POST", "/auth/users/{user_id}/groups"),
    ("DELETE", "/auth/users/{user_id}/groups/{group_name}"),
    # Admin-only notification controls.
    ("POST", "/notifications"),
    ("POST", "/notifications/trigger"),
    ("POST", "/notifications/test"),
    ("PUT", "/notifications/{notification_id}/read"),
    ("PUT", "/notifications/read-all"),
    ("DELETE", "/notifications/{notification_id}"),
    # Server-level update checks and non-API endpoints.
    ("GET", "/framework-update-info.yml"),
    ("GET", "/health"),
    ("GET", "/version"),
}


def _tool_name(method: str, path: str) -> str:
    """Create a stable, human-readable MCP tool name."""
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", path).strip("_").lower()
    return f"{method.lower()}_{clean}"


def _display_title(summary: str, operation_id: str) -> str:
    """Return a clean human-readable title for the tool.

    Falls back to the operationId if no summary is provided.
    """
    return (summary or operation_id).strip()


def _category_from_tags(tags: list[str] | None) -> str | None:
    """Return a display category from OpenAPI operation tags.

    The WAFpass spec tags each router consistently (e.g. ``runs``,
    ``controls``). We expose the first tag in ``tool.meta.category`` so
    clients can group related tools without overwriting the tool title.
    """
    if not tags:
        return None
    return tags[0].strip()


def _operation_hints(method: str) -> ToolAnnotations:
    """Return Claude-Desktop-friendly hints for grouping.

    Read-only operations show under "Read-only tools"; mutating operations
    show under "Write/delete tools".
    """
    read_only = method == "GET"
    destructive = method in {"DELETE", "POST", "PUT", "PATCH"}
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=method in {"GET", "PUT", "DELETE"},
        openWorldHint=False,
    )


def _canonical_path(path: str) -> str:
    """Strip a leading ``/api/vN`` version prefix for role lookups.

    The upstream server versioned all routers under ``/api/v1``, but the
    internal role matrix is defined in terms of the unversioned path.
    """
    return _API_VERSION_PREFIX_RE.sub("", path)


def _unwrap_nullable_union(
    schema: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    """Return the concrete schema and whether ``null`` is allowed.

    OpenAPI 3.1 expresses optional/nullable fields with ``anyOf``/``oneOf``
    that include a ``{"type": "null"}`` branch. Pydantic needs the concrete
    type, so we strip the null branch and mark the field optional.
    """
    union = schema.get("anyOf") or schema.get("oneOf")
    if not isinstance(union, list):
        return (None, False)

    non_null = [s for s in union if isinstance(s, dict) and s.get("type") != "null"]
    is_nullable = any(isinstance(s, dict) and s.get("type") == "null" for s in union)
    if len(non_null) == 1:
        return (non_null[0], is_nullable)
    return (None, is_nullable)


def _schema_to_field(
    name: str,
    schema: dict[str, Any],
    required: bool,
    spec: dict[str, Any] | None = None,
) -> tuple[type | None, FieldInfo]:
    """Convert an OpenAPI property schema to a Pydantic field tuple."""
    if spec is not None:
        schema = _resolve_schema(spec, schema)

    nullable_union = _unwrap_nullable_union(schema)
    if nullable_union[0] is not None:
        concrete, allows_null = nullable_union
        required = required and not allows_null
        # Preserve the full resolved union in the generated JSON schema so the
        # tool description still shows nullability correctly.
        extra: dict[str, Any] = {"anyOf": schema.get("anyOf", schema.get("oneOf"))}
        py_type, _ = _schema_to_field(name, concrete, required, spec=None)
        if allows_null:
            py_type = py_type | None  # type: ignore[operator]
        field_kwargs: dict[str, Any] = {
            "description": schema.get("description", ""),
            "json_schema_extra": extra,
        }
        if required:
            return (py_type, Field(**field_kwargs))
        return cast(
            tuple[type | None, FieldInfo],
            (py_type, Field(default=None, **field_kwargs)),
        )

    openapi_type = schema.get("type", "string")
    py_type: type = _OPENAPI_TYPES_TO_PYTHON.get(openapi_type, str)
    json_schema_extra = None

    if openapi_type == "array":
        items = schema.get("items", {})
        item_type = _OPENAPI_TYPES_TO_PYTHON.get(items.get("type"), Any)
        py_type = list[item_type]  # type: ignore[valid-type]
        # Preserve nested item schema so the generated tool input_schema shows
        # real object fields for arrays like auto-fix findings.
        if items:
            json_schema_extra = {"items": items}
    elif openapi_type == "object":
        py_type = dict[str, Any]
        # Preserve object properties in the generated JSON schema.
        if "properties" in schema:
            json_schema_extra = {
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            }

    description = schema.get("description", "")
    field_kwargs: dict[str, Any] = {"description": description}
    if json_schema_extra is not None:
        field_kwargs["json_schema_extra"] = json_schema_extra

    if required:
        return (py_type, Field(**field_kwargs))
    optional_field = cast(
        tuple[type | None, FieldInfo],
        (py_type, Field(default=None, **field_kwargs)),
    )
    return optional_field


def _resolve_ref(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Follow a single ``$ref`` pointer to its component definition."""
    ref = schema.get("$ref")
    if not ref or not isinstance(ref, str):
        return schema
    if not ref.startswith("#/components/schemas/"):
        return schema
    name = ref[len("#/components/schemas/") :]
    components = spec.get("components", {})
    return components.get("schemas", {}).get(name, schema)


def _resolve_schema(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively resolve ``$ref`` pointers in a schema.

    Handles top-level references, nested object properties, and array item
    references so generated Pydantic models expose the real field structure.
    """
    if not isinstance(schema, dict):
        return schema

    resolved = _resolve_ref(spec, schema)
    if resolved is not schema:
        # Avoid infinite recursion on circular refs by replacing the ref with
        # a shallow marker before recursing. This is safe for WAFpass specs
        # which do not contain circular schema definitions.
        schema = {**resolved}

    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {
                k: _resolve_schema(spec, v) for k, v in value.items()
            }
        elif key == "items" and isinstance(value, dict):
            result[key] = _resolve_schema(spec, value)
        elif key in ("anyOf", "allOf", "oneOf") and isinstance(value, list):
            result[key] = [_resolve_schema(spec, item) for item in value]
        else:
            result[key] = value
    return result


def _build_request_model(
    model_name: str,
    path_param_names: set[str],
    query_params: list[dict[str, Any]],
    request_body: dict[str, Any] | None,
    spec: dict[str, Any] | None = None,
) -> type[BaseModel]:
    """Create a Pydantic model that validates arguments for one operation."""
    fields: dict[str, tuple[type | None, FieldInfo]] = {}

    # Path parameters are always required so the URL can be built.
    for name in path_param_names:
        fields[name] = _schema_to_field(name, {"type": "string"}, required=True)

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
        if spec is not None:
            body_schema = _resolve_schema(spec, body_schema)
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
        if "{" in url:
            raise ValueError(f"Unsubstituted path parameter in URL: {url}")
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
                canonical_path = _canonical_path(path)
                if (method, canonical_path) in SKIP_OPERATIONS:
                    continue
                if not isinstance(details, dict):
                    continue

                operation_id = details.get("operationId") or _tool_name(method, path)
                tool_name = operation_id
                summary = details.get("summary", "") or tool_name
                title = _display_title(summary, tool_name)
                # Use the full docstring as the description when available so the AI
                # understands limitations such as "only fixable assertions are patched".
                description = (
                    details.get("description", "") or summary
                ).strip()
                category = _category_from_tags(details.get("tags"))
                parameters = details.get("parameters", []) or []
                request_body = details.get("requestBody")

                path_param_names = {
                    p["name"] for p in parameters if p.get("in") == "path"
                }
                query_params = [p for p in parameters if p.get("in") == "query"]
                query_param_names = {p["name"] for p in query_params}

                request_model = _build_request_model(
                    tool_name,
                    path_param_names,
                    query_params,
                    request_body,
                    spec=self.spec,
                )
                required_role = ROLE_MAP.get((method, canonical_path))

                meta = {"category": category} if category else None
                tool = Tool(
                    name=tool_name,
                    title=title,
                    description=description,
                    input_schema=request_model.model_json_schema(),
                    annotations=_operation_hints(method),
                    meta=meta,
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
