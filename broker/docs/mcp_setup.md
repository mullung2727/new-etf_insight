# Kiwoom Broker MCP — Setup

A **stdio** MCP server (local, single user) exposing Kiwoom REST/WebSocket
trading as tools. Unlike `api/` (SSE + mcp-remote bridge), this runs as a
direct child process of the MCP client.

## Tools

| Tool | 기능 | TR (verify) |
|---|---|---|
| `get_quote` | 현재가 | ka10001 |
| `get_orderbook` | 호가 | ka10004 |
| `get_balance` | 계좌평가잔고 | kt00018 |
| `get_deposit` | 예수금 | kt00001 |
| `place_order` | 매수/매도 (가드) | kt10000/kt10001 |
| `cancel_order` | 주문취소 | kt10003 |
| `list_conditions` | 조건검색 목록 | ka10171 (ws) |
| `run_condition` | 조건검색 단발 | ka10172 (ws) |

> TR 코드는 `kiwoom/tr.py` 한 곳에 모임. 공식 가이드와 불일치 시 거기서 수정.

## 1. 자격증명

루트 `.env` 에 채움(`broker/.env.example` 참고). `broker/.env` 는 만들지 않는다:

```
KIWOOM_ENV=paper          # 모의투자. 실전 전환 시 real → broker 재기동 필요
KIWOOM_PAPER_APPKEY=...
KIWOOM_PAPER_SECRETKEY=...
KIWOOM_PAPER_ACCOUNT_NO=...
KIWOOM_REAL_APPKEY=...    # 실전 앱키는 모의와 별도 발급
KIWOOM_REAL_SECRETKEY=...
KIWOOM_REAL_ACCOUNT_NO=...
MAX_ORDER_AMOUNT=1000000  # 1회 주문 최대 금액(원)
```

`KIWOOM_ENV` 한 줄이 주소·키·계좌를 함께 바꾼다 — 혼합 상태가 생기지 않는다.

`.env`는 `.gitignore`에 있음 — 절대 커밋 금지.

## 2. 의존성 설치

```powershell
cd broker
uv sync           # uv 있으면
# 또는
python -m venv .venv; .\.venv\Scripts\python -m pip install -e .
```

## 3. 토큰 발급 확인

```powershell
cd broker
uv run python -c "from kiwoom.auth import get_token; print(get_token()[:10])"
```

토큰 앞 10자가 출력되면 인증 성공.

## 4. 서버 실행

```powershell
cd broker
.\.venv\Scripts\uvicorn main:app --reload --port 8001
```

## 5. MCP Inspector로 점검 (SSE)

```powershell
npx @modelcontextprotocol/inspector
# UI에서 SSE 선택, URL: http://localhost:8001/mcp
```

UI에서 도구 호출: `get_quote` symbol=`005930`, `get_balance`, `list_conditions`.

## 6. Claude Desktop 설정

서버를 `http://localhost:8001`에서 실행한 뒤, `mcp-remote` 브릿지로 연결.

`%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kiwoom-broker": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8001/mcp"]
    }
  }
}
```

재시작하면 도구 목록에 8개 노출.

## 안전

- v1은 `KIWOOM_ENV=paper` 기본. 실전은 명시적 변경 필요.
- 모든 주문은 `orders.place_order` 단일 경로 → 금액/수량 가드 우회 불가.
- 자동매매(조건검색→자동주문)는 v2 상주 워커로 분리 예정 (stdio는 클라
  꺼지면 죽으므로 자율매매 부적합).
