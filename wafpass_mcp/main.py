"""FastAPI application exposing the WAF++ MCP bridge over HTTP + SSE.

OIDC pass-through flow
--------------------
1. End-user authenticates via the upstream WAFpass IdP (Keycloak/Entra/etc.)
   and receives a WAF++ access token.
2. AI client opens an SSE connection to ``/sse`` with
   ``Authorization: Bearer <wafpass-token>``.
3. FastAPI dependency ``require_user_context`` validates the token
   (introspection or local HS256) and rejects unauthenticated attempts with 401.
4. The validated ``UserContext`` is stored in the ASGI scope and copied into a
   context variable so MCP tool handlers can read it.
5. ``list_tools`` only returns tools the user's role is allowed to execute.
6. ``call_tool`` proxies the call to WAFpass, forwarding the same Bearer token,
   so the backend performs row-level / endpoint-level authorization.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from wafpass_mcp.auth import require_user_context
from wafpass_mcp.config import settings
from wafpass_mcp.mcp_server import MCPServerBridge as _MCPServerBridge

_STATIC_DIR = Path(__file__).parent / "static"

logger = structlog.get_logger()
_bridge: _MCPServerBridge | None = None


def _get_bridge() -> _MCPServerBridge:
    if _bridge is None:
        raise RuntimeError("MCP bridge not initialized")
    return _bridge


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _bridge
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
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

    _bridge = _MCPServerBridge()
    await _bridge.load_openapi()
    logger.info("mcp_bridge_ready")
    yield
    _bridge = None


app = FastAPI(title="wafpass-mcp", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/favicon.ico")
async def favicon() -> Response:
    return FileResponse(_STATIC_DIR / "logo.png")


@app.get("/health")
async def health(request: Request) -> Response:
    bridge_state = "ready" if _bridge else "initializing"
    if "text/html" in request.headers.get("accept", ""):
        styles = "font-family: system-ui, sans-serif; "
        styles += "max-width: 600px; margin: 40px auto; text-align: center;"
        return HTMLResponse(
            f"""
            <!doctype html>
            <html lang="en">
              <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>WAF++ MCP Bridge</title>
                <link rel="icon" type="image/png" href="/favicon.ico">
              </head>
              <body style="{styles}">
                <img src="/static/logo.png" width="120"
                     alt="WAF++ logo" style="margin-bottom: 20px;">
                <h1>WAF++ MCP Bridge</h1>
                <p>Status: <strong>{bridge_state}</strong></p>
                <p>SSE endpoint: <code>http://localhost:3001/sse</code></p>
              </body>
            </html>
            """
        )
    return JSONResponse({"status": "ok", "bridge": bridge_state})


@app.get("/sse")
async def sse_endpoint(request: Request) -> Response:
    """Establish an MCP SSE session.

    Unauthenticated requests are rejected with 401 before the MCP session starts.
    """
    ctx = await require_user_context(request)
    request.scope["wafpass_user_context"] = ctx
    logger.info("sse_connect", user=ctx.username, role=ctx.role)

    bridge = _get_bridge()
    assert bridge.sse is not None
    async with bridge.sse.connect_sse(
        request.scope, request.receive, request._send
    ) as (read_stream, write_stream):
        options = bridge.server.create_initialization_options()
        await bridge.server.run(
            read_stream,
            write_stream,
            options,
            raise_exceptions=True,
        )
    return Response()


@app.post("/messages/")
async def messages_endpoint(request: Request) -> Response:
    """Handle individual MCP messages posted by the client.

    The same Bearer token used to open the SSE stream must be presented on
    every POST so the bridge can propagate the authenticated user context.
    """
    ctx = await require_user_context(request)
    request.scope["wafpass_user_context"] = ctx

    bridge = _get_bridge()
    assert bridge.sse is not None
    await bridge.sse.handle_post_message(request.scope, request.receive, request._send)
    return Response()


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    logger.warning("http_exception", status=exc.status_code, detail=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def main() -> None:
    import uvicorn

    uvicorn.run(
        "wafpass_mcp.main:app",
        host=settings.mcp_host,
        port=settings.mcp_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
