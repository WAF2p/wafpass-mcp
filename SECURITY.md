# Security policy for wafpass-mcp

This document covers how to report security issues in the WAF++ MCP Bridge, what is in scope, and how to configure the bridge securely.

For architecture details, token flows, and internal design see `TECH.md`. For upstream API security, see `../wafpass-server/SECURITY.md`.

---

## Supported versions

Only the current `main` branch and the latest published release on PyPI are actively supported with security fixes. Releases are published automatically by the `release.yml` workflow.

| Version | Supported |
|---------|-----------|
| Latest release on PyPI | Yes |
| `main` branch | Yes |
| Older releases | No |

---

## Reporting a vulnerability

If you discover a security vulnerability in `wafpass-mcp`, please report it privately rather than opening a public issue or pull request.

Options:

- Open a [GitHub Security Advisory](https://github.com/WAF2p/wafpass-mcp/security/advisories/new) if private vulnerability reporting is enabled for the repository.
- Otherwise, send a direct message to one of the maintainers listed in `pyproject.toml` under `authors`.

Please include:

- A clear description of the vulnerability
- Steps to reproduce, or a minimal proof of concept
- The affected version or commit
- Any suggested mitigation

We aim to acknowledge reports within 5 business days and will keep you informed during investigation.

---

## Scope

The following are in scope for vulnerability reports:

- The `wafpass-mcp` Python package (`wafpass_mcp/`)
- The published Docker image (`Dockerfile`)
- The HTTP/SSE API surface (`/health`, `/sse`, `/messages/`)
- Token validation logic in `wafpass_mcp/auth.py`
- Role-based tool filtering in `wafpass_mcp/openapi_mapper.py` and `wafpass_mcp/mcp_server.py`
- Authorization header propagation to the upstream API

Out of scope:

- The upstream `wafpass-server` API (report in the `wafpass-server` repository)
- The `wafpass-core` engine or `wafpass` CLI (report in the `pass` repository)
- The `wafpass-dashboard` React UI (report in the `wafpass-dashboard` repository)
- Generic dependency vulnerabilities unless exploitable through this bridge's own code

---

## Security-sensitive configuration

### Token validation mode

`wafpass_mcp/config.py` reads `WAFPASS_TOKEN_MODE`:

| Mode | Guidance |
|------|----------|
| `introspection` (default) | Recommended. The bridge asks `wafpass-server` to validate every token. No shared secret is required and revocation is respected. |
| `jwt_secret` | Requires `WAFPASS_JWT_SECRET` to be set to the same HS256 secret used by `wafpass-server`. Faster but cannot detect revoked tokens. |

In `jwt_secret` mode, `WAFPASS_JWT_SECRET` must be a strong, randomly generated key shared with the upstream server. Generate one with `openssl rand -hex 32` and store it in a secret manager — never commit it.

### Upstream trust boundary

The bridge does **not** implement its own OIDC/SAML2 flows. It treats `wafpass-server` as the token authority and forwards the original `Authorization: Bearer` header on every backend call. Therefore:

- The bridge only needs outbound access to `wafpass-server`.
- It must not be exposed to the public internet without a TLS-terminating reverse proxy.
- It must not be given access to the IdP or database used by `wafpass-server`.

### Environment variables

| Variable | Risk if leaked | Mitigation |
|----------|----------------|------------|
| `WAFPASS_JWT_SECRET` | Forges tokens accepted by the bridge in `jwt_secret` mode | Store in a secret manager; use `introspection` mode if possible |
| `WAFPASS_API_BASE_URL` | Could redirect bridge traffic to a malicious upstream | Validate at deployment; prefer internal network names |

---

## Security design highlights

- Both `/sse` and `/messages/` validate the Bearer token before processing the MCP session.
- Unauthenticated requests receive `401 Unauthorized` before the MCP protocol begins.
- Tool visibility is filtered by the authenticated user's role; unauthorized tools are never advertised.
- The original Bearer token is forwarded unchanged on every backend request so `wafpass-server` applies its own endpoint- and row-level authorization.
- Auth endpoints (`/auth/login`, OIDC/SAML callbacks, etc.) are excluded from the tool registry and cannot be invoked through the bridge.

---

## Disclosure policy

We follow a coordinated disclosure approach. Once a fix is available, we will:

1. Merge the fix and publish a new release.
2. Create a GitHub Security Advisory and/or release notes describing the issue without unnecessary detail.
3. Credit the reporter if they wish to be named.

Thank you for helping keep WAF++ MCP secure.
