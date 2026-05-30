# broker — Kiwoom REST API MCP server

Local **stdio** MCP server giving Claude access to Kiwoom Securities trading:
시세조회 · 잔고조회 · 수동 매수매도(가드) · 조건검색식 조회.

Isolated from `api/` (read-only public ETF data) because this holds secret
trading credentials and moves money — a separate security domain.

## Layout

```
server.py          FastMCP stdio entry, 8 tools
kiwoom/
  config.py        env + paper/real host selection
  auth.py          token issue/cache/refresh
  client.py        thin httpx TR caller (api-id header, cont-yn paging)
  tr.py            ⚠️ TR codes + endpoints — single source of truth, VERIFY
  quotes.py        get_quote / get_orderbook
  account.py       get_balance / get_deposit
  orders.py        place_order / cancel_order (only order path)
  guards.py        amount/qty caps, enforced pre-wire
  conditions.py    list_conditions / run_condition (WebSocket)
  models.py        pydantic inputs/outputs
docs/mcp_setup.md  install + Claude Desktop config
```

## Quick start

See `docs/mcp_setup.md`. TL;DR: copy `.env.example`→`.env`, fill paper
credentials, `uv sync`, then `uv run python server.py`.

## Scope

- **v1 (this)**: read + manual, guarded orders. stdio, single user, 모의투자.
- **v2 (later)**: standalone always-on worker for condition-triggered
  autonomous trading (WebSocket `ka10173` realtime). MCP becomes the control
  plane (start/stop strategy) writing shared state the worker executes.

## Safety

- `.env` / `.token_cache.json` are git-ignored.
- Default `KIWOOM_ENV=paper`; switching to `real` is an explicit env change.
- All orders pass `guards.check_order` (MAX_ORDER_AMOUNT / MAX_ORDER_QTY).
