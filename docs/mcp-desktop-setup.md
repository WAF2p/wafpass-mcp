# Claude Desktop setup guide

This guide walks through installing the WAF++ MCP bridge, logging in once with `wafpass-mcp-configure`, and connecting it to Claude Desktop on macOS or Windows.

> For headless servers, CI, or advanced HTTP/SSE deployment options, see `README.md`.

---

## 1. Install the bridge

```bash
git clone https://github.com/WAF2p/wafpass-mcp.git
cd wafpass-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

This installs three commands:

- `wafpass-mcp` — HTTP/SSE bridge.
- `wafpass-mcp-stdio` — stdio bridge for Claude Desktop.
- `wafpass-mcp-configure` — interactive login and credential storage.

## 2. Start the upstream WAFpass server

The bridge needs a running `wafpass-server`. For local development, seed a bootstrap admin in `../wafpass-server/.env`:

```bash
WAFPASS_ADMIN_USERNAME=admin
WAFPASS_ADMIN_PASSWORD=changeme123
WAFPASS_ADMIN_ROLE=admin
```

The server creates this user automatically on first startup.

## 3. Log in and store credentials

Run the interactive helper:

```bash
wafpass-mcp-configure
```

You will be prompted for:

1. WAFpass API base URL (default `http://localhost:8000`).
2. Username.
3. Password.

The helper logs in through `/api/v1/auth/login`, fetches your profile from `/api/v1/auth/me`, and stores the following in the user's config directory:

- `api_base_url`
- `access_token`
- `refresh_token`
- `username`
- `token_mode`
- `log_level`

The file is created with owner-only read permissions (`0o600`) on platforms that support it.

Config file locations:

- **macOS**: `~/Library/Application Support/wafpass-mcp/settings.json`
- **Linux**: `~/.config/wafpass-mcp/settings.json`
- **Windows**: `%APPDATA%\wafpass-mcp\settings.json`

## 4. Configure Claude Desktop

Open Claude Desktop and go to **Settings → Developer → Edit Config**. This opens `claude_desktop_config.json`. Add the stdio server entry:

```json
{
  "mcpServers": {
    "wafpass": {
      "command": "/Users/lewandos/git/waf++/wafpass-mcp/.venv/bin/wafpass-mcp-stdio"
    }
  }
}
```

Adjust the `command` path to match your virtual environment location. Use an absolute path; relative paths often fail because Claude Desktop starts with a different working directory.

Save the file and restart Claude Desktop.

## 5. Verify the connection

After restart, open a new chat and ask Claude to list the available WAFpass tools, for example:

> "List the WAFpass tools you have access to."

You should see tools such as `list_runs_runs_get` and `get_run_runs__run_id__get`, filtered by your user's role.

## 6. Updating or switching accounts

Run `wafpass-mcp-configure` again to overwrite the stored config. The next time Claude Desktop starts `wafpass-mcp-stdio`, the new credentials are used.

## 7. Environment variable overrides

Environment variables in the Claude Desktop config take precedence over the stored config. This is useful for temporarily switching backends or testing without overwriting the saved credentials:

```json
{
  "mcpServers": {
    "wafpass": {
      "command": "/Users/lewandos/git/waf++/wafpass-mcp/.venv/bin/wafpass-mcp-stdio",
      "env": {
        "WAFPASS_ACCESS_TOKEN": "<WAF++_TOKEN_HERE>",
        "WAFPASS_REFRESH_TOKEN": "<WAF++_REFRESH_TOKEN_HERE>",
        "WAFPASS_API_BASE_URL": "http://localhost:8000",
        "WAFPASS_TOKEN_MODE": "introspection"
      }
    }
  }
}
```

## Troubleshooting

### "No WAF++ access token found"

Run `wafpass-mcp-configure` first, or set `WAFPASS_ACCESS_TOKEN` in the Claude Desktop config.

### "Access token is invalid or expired"

The stored refresh token may also be expired. Re-run `wafpass-mcp-configure` to log in again.

### Tools do not appear

1. Check that `wafpass-server` is running and reachable at the configured URL.
2. Check Claude Desktop logs for stderr output from `wafpass-mcp-stdio`.
3. Verify the `command` path is absolute and points to an executable inside an activated virtual environment.

### Wrong role / missing tools

The tool list is filtered by the authenticated user's role. Log in with a user that has the required role, or ask your WAF++ administrator to adjust role assignments.
