# wafpass-mcp — Technical Reference

This document covers the internal architecture, design decisions, and contribution guidance for the WAF++ MCP bridge. For user-facing documentation see `README.md`.

The bridge is part of the WAF++ tool chain:

| Component | Package | Responsibility |
|-----------|---------|----------------|
| `../pass` | `wafpass-core` | IaC compliance engine that produces scan results |
| `../wafpass-server` | `wafpass-server` | FastAPI + PostgreSQL API that stores results and issues JWTs |
| `../wafpass-mcp` | `wafpass-mcp` | **This package** — exposes the WAFpass REST API as MCP tools over HTTP/SSE |

---

## Directory structure

```
wafpass-mcp/
├── wafpass_mcp/
│   ├── __init__.py          # Package marker
│   ├── main.py              # FastAPI app, /health, /sse, /messages/ endpoints
│   ├── config.py            # pydantic-settings (env var parsing)
│   ├── auth.py              # UserContext, token introspection / JWT validation
│   ├── openapi_mapper.py    # OpenAPI → OperationMeta + Pydantic request models
│   └── mcp_server.py        # MCPServerBridge: SDK Server, on_list_tools, on_call_tool
├── tests/                   # pytest suite
├── scripts/                 # Standalone SSE test clients
├── .github/workflows/       # CI and release automation
├── Dockerfile
├── .dockerignore
├── pyproject.toml
├── VERSION
├── .env.example
├── README.md
└── LICENSE
```

---

## Request lifecycle

```
AI Client (MCP host)
    │  GET /sse  Authorization: Bearer <WAF++ token>
    ▼
main.py:sse_endpoint
    │  await require_user_context(request)
    ▼
auth.py
    ├─ introspection mode  → GET /auth/me on wafpass-server
    └─ jwt_secret mode     → local HS256 verify + claim extraction
    │  returns UserContext {id, username, role, is_active}
    ▼
request.scope["wafpass_user_context"] = ctx
    │  MCP SSE session established
    ▼
MCP "tools/list" request
    ▼
mcp_server.py:_on_list_tools
    ├─ reads UserContext from ASGI scope
    ├─ filters operations where role_sufficient(required_role) is True
    └─ returns Tool list with input schemas
    ▼
MCP "tools/call" request
    ▼
mcp_server.py:_on_call_tool
    ├─ validates arguments against generated Pydantic model
    ├─ enforces required role
    ├─ extracts path params, query params, body
    └─ proxies to wafpass-server with the same Authorization header
    ▼
wafpass-server applies endpoint + row-level authorization
    │  returns JSON
    ▼
MCP TextContent / is_error result returned to AI Client
```

---

## Token validation (`auth.py`)

Two modes are supported; the active mode is chosen by `WAFPASS_TOKEN_MODE`.

| Mode | Mechanism | Use case |
|------|-----------|----------|
| `introspection` | `GET <WAFPASS_API_BASE_URL>/auth/me` with `Authorization: Bearer <token>` | Recommended. IdP-agnostic, works with secret rotation, instant revocation. |
| `jwt_secret` | Local HS256 verification using `WAFPASS_JWT_SECRET` | Faster, but requires sharing the secret and does not detect revocation. |

Both modes produce a `UserContext` with `id`, `username`, `role`, and `is_active`. Unauthenticated requests raise `HTTPException(401)` before the MCP session starts.

### Role hierarchy

```python
ROLE_HIERARCHY = ["clevel", "ciso", "architect", "engineer", "admin"]
```

`UserContext.role_sufficient("architect")` returns `True` for `architect`, `engineer`, and `admin`.

---

## OpenAPI mapping (`openapi_mapper.py`)

At startup `MCPServerBridge.load_openapi()` fetches `<WAFPASS_API_BASE_URL>/openapi.json` and builds an `OperationMeta` for every safe operation.

### Tool naming

The tool name is the OpenAPI `operationId` when present, otherwise a generated name from the HTTP method and path. Examples from the real backend:

| Tool name | Method | Path |
|-----------|--------|------|
| `list_runs_runs_get` | `GET` | `/runs` |
| `get_run_runs__run_id__get` | `GET` | `/runs/{run_id}` |
| `get_runs_id_findings_get` | `GET` | `/runs/{id}/findings` |

### Input schema generation

For each operation a dynamic Pydantic model is created with:

- Path parameters as required fields.
- Query parameters as optional fields.
- Request body JSON properties merged in as top-level fields.

The model is then converted to JSON Schema and used as the MCP tool's `input_schema`.

### Role assignment

`ROLE_MAP` in `openapi_mapper.py` maps `(method, path)` to the minimum required role. This mirrors the backend's role matrix. `list_tools` removes tools the caller cannot execute.

### Skipped operations

Auth callbacks and login endpoints are excluded by `SKIP_OPERATIONS` so the AI client cannot trigger OIDC/SAML flows or obtain tokens through the bridge.

---

## MCP server bridge (`mcp_server.py`)

`MCPServerBridge` wraps the `mcp` SDK's low-level `Server`:

```python
self.server = Server(
    "wafpass-mcp",
    on_list_tools=self._on_list_tools,
    on_call_tool=self._on_call_tool,
)
```

### `_on_list_tools`

- Fetches `UserContext` from `request_context.meta` (populated via ASGI scope in `main.py`).
- Iterates over `self.operations` and filters by `ctx.role_sufficient(op.required_role)`.
- Returns the visible `Tool` definitions.

### `_on_call_tool`

- Looks up the requested tool name in `self.operations`.
- Re-checks role requirements.
- Validates arguments with the operation's Pydantic request model.
- Builds the URL by substituting path parameters.
- Extracts query parameters for `GET`/`DELETE` and body fields for `POST`/`PUT`/`PATCH`.
- Forwards the call to `wafpass-server` with the original `Authorization: Bearer` header.
- Returns `CallToolResult` with `TextContent(...)` or `isError=True` on failure.

---

## FastAPI integration (`main.py`)

Two HTTP endpoints are exposed:

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness check for containers and load balancers |
| `GET /sse` | Establish MCP SSE session; validates Bearer token |
| `POST /messages/` | Handle MCP messages; validates Bearer token again |

Both `/sse` and `/messages/` call `require_user_context(request)` and store the validated `UserContext` in the ASGI scope. The MCP SDK's `SseServerTransport` reads from and writes to the same `scope`, `receive`, and `_send` objects.

The `lifespan` context manager:

1. Configures standard logging and structlog.
2. Instantiates `MCPServerBridge` and calls `load_openapi()`.
3. Yields while the app serves traffic.

---

## Configuration (`config.py`)

Settings are loaded once at startup via `pydantic-settings`.

| Variable | Default | Description |
|----------|---------|-------------|
| `WAFPASS_API_BASE_URL` | `http://localhost:8000` | Upstream WAFpass API |
| `WAFPASS_TOKEN_MODE` | `introspection` | `introspection` or `jwt_secret` |
| `WAFPASS_JWT_SECRET` | *(empty)* | Required for `jwt_secret` mode |
| `MCP_HOST` | `0.0.0.0` | Bind host |
| `MCP_PORT` | `3001` | Bind port |
| `LOG_LEVEL` | `INFO` | Logging level |

`WAFPASS_TOKEN_MODE` is validated to be one of the two allowed strings.

---

## Docker and compose

The project builds a standalone image from `Dockerfile` and is also referenced from the monorepo root `docker-compose.yml`.

```
docker compose up -d wafpass-mcp
```

The compose service sets `WAFPASS_API_BASE_URL=http://wafpass-server:8000` and `WAFPASS_TOKEN_MODE=introspection` by default.

---

## Development

```bash
pip install -e ".[dev]"

# Lint
ruff check wafpass_mcp tests scripts

# Type check
mypy wafpass_mcp tests scripts

# Test
pytest tests -q

# Manual end-to-end check against a running wafpass-server
python scripts/mcp_list_tools.py <WAF++_TOKEN>
python scripts/mcp_call_tool_test.py <WAF++_TOKEN>
```

---

## Adding or changing a tool mapping

1. If a backend endpoint is missing or its role needs adjustment, edit `openapi_mapper.py`:
   - Add/update an entry in `ROLE_MAP[(method, path)]`.
   - Add auth-related paths to `SKIP_OPERATIONS` if they should not be exposed.
2. If the bridge itself needs a new HTTP endpoint or lifecycle step, edit `main.py`.
3. If token validation logic changes, update `auth.py` and the tests in `tests/test_auth.py`.
4. Update `README.md` and this file.
