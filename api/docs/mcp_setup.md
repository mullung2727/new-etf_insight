# MCP Setup

The FastAPI server exposes every REST endpoint (except `/health`) as an MCP
tool via [`fastapi-mcp`](https://github.com/tadata-org/fastapi_mcp). The MCP
transport is **SSE** and is mounted at `/mcp`.

## Tools

| Tool name | HTTP route |
|---|---|
| `list_etfs` | `GET /etfs` |
| `get_etf_detail` | `GET /etfs/{etf_key}` |
| `get_etf_holdings` | `GET /etfs/{etf_key}/holdings` |
| `list_countries` | `GET /countries` |
| `holdings_stats` | `GET /stats/holdings` |
| `stats_summary` | `GET /stats/summary` |

## Quick check with the MCP Inspector

```powershell
npx @modelcontextprotocol/inspector
# In the inspector UI, choose "SSE" transport and enter:
#   http://localhost:8000/mcp
```

## Claude Desktop config

Claude Desktop talks stdio, so use `mcp-remote` as a bridge to the SSE
endpoint. Edit `%APPDATA%\Claude\claude_desktop_config.json` and add:

```json
{
  "mcpServers": {
    "etf-insight": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8000/mcp"]
    }
  }
}
```

Restart Claude Desktop and the six tools above appear in the tools list.

When the FastAPI server is exposed through Cloudflare Tunnel (see Step 7 of
the rollout plan), point `mcp-remote` at the public URL instead, e.g.
`https://api.<your-domain>/mcp`.
