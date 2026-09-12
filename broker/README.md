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

## 왜 broker 를 거치나 · 왜 계좌마다 포트가 따로인가

- 원리상으로는 env 키로 키움 endpoint 를 직접 호출해도 매매·조회가 된다. 포트는 **키움 호출을 broker 서버 한 곳에 모아둔 구조**라서 생긴다
- 한 곳에 모아서 하는 일
  - 토큰 발급·갱신
  - 호출 간격 제한 (`KIWOOM_MIN_INTERVAL`, 0.3초). 청산 워커와 주문 배치가 동시에 돌아도 한 줄로 세운다
  - 주문 가드 (`MAX_ORDER_AMOUNT`), 주문·체결 기록, 투자노트 자동 연결
- 매매 배치(`etl/scripts/*`)의 주문·잔고·시세 함수는 키움이 아니라 **broker 에 HTTP 로 요청**한다 (`--broker-url`). 그래서 broker 가 떠 있어야 매매가 된다
- broker 프로세스 하나는 **계좌 하나**만 다룬다 (기동 시 `load_config()` 가 앱키·계좌를 고정). 계좌가 늘면 broker 를 포트를 달리해 하나 더 띄운다
  (설계: `docs/PLAN_MULTI_ACCOUNT_TRADING.md`)
- broker 없이 배치가 키움을 직접 부르면 위 기능을 배치마다 다시 갖춰야 하고, 동시에 도는 배치끼리 호출 한도·토큰이 충돌할 수 있다

### 계좌별 broker 는 같은 코드다 — 다른 건 계좌와 전략뿐

- :8001(기존 계좌)과 :8002(high52 계좌)는 **같은 `broker/` 코드·같은 `.venv`** 로 뜬다. 코드 수정은 양쪽에 똑같이 적용된다
- 차이는 `scripts/restart_all_servers.ps1` 이 기동 시 넣는 설정뿐이다

  | 설정 | 무엇에 따른 차이 |
  |---|---|
  | `KIWOOM_PROFILE` (어느 계좌 키를 읽나, `.env` 의 `KIWOOM_<PROFILE>_<ENV>_*`) | 계좌 |
  | `TOKEN_CACHE_PATH`, `NOTES_DB_PATH` | 계좌 (토큰·노트가 계좌별로 따로 쌓인다) |
  | `ALLOWED_ORDER_SOURCES` (그 밖의 전략 주문은 422) | 전략 |

- `MAX_ORDER_AMOUNT`(주문 1건 상한) 등 나머지는 루트 `.env` 를 공유한다
- **재기동은 반드시 `restart_all_servers.ps1` 로 전부 같이.** 떠 있는 broker 는 기동 시점 코드로 돈다(`--reload` 없음) → 한쪽만 재기동하면 두 broker 코드 버전이 어긋난다
- **MCP**: MCP 서버는 broker 마다 붙어 있어 `:8002/mcp` 도 존재하지만, 클라이언트(Claude Code `.mcp.json`, Hermes)는 **:8001 에만 연결**한다. broker-web 화면도 :8001 만 본다
- 매매 배치(`etl/scripts`)는 실행마다 새 프로세스라 항상 최신 코드다. 어느 broker 에 붙을지는 러너의 `--broker-url`, 계좌 확인은 `require_profile`

## Safety

- `.env` / `.token_cache.json` are git-ignored.
- Default `KIWOOM_ENV=paper`; switching to `real` means editing the root `.env`
  and restarting — there is no runtime switch (API or UI). `KIWOOM_ENV` selects the
  host *and* the `KIWOOM_PAPER_*` / `KIWOOM_REAL_*` credential set together.
- All orders pass `guards.check_order` (MAX_ORDER_AMOUNT). Limit orders are capped on
  `price*qty`, market **buys** on `current_price*qty` (rejected if the quote fails).
  Market **sells** are exempt so a position can always be closed.
