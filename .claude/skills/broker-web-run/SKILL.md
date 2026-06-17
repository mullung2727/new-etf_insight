---
name: broker-web-run
description: broker-web 풀스택 실행 가이드 — 3개 서버(api :8000, broker :8001, broker-web :3000) 순서와 포트, 전제 조건 한 번에 확인. UI 개발·디버깅·서버 시작 전 반드시 읽을 것.
---

# broker-web 실행 가이드

broker-web(Next.js) 동작에 서버 3개 필요. **순서 중요**: api → broker → broker-web.

## 서버 구성

| # | 이름 | 디렉토리 | 포트 | 역할 |
|---|------|----------|------|------|
| 1 | **api** | `api/` | **8000** | ETF 데이터 FastAPI (etf/watchlist=SQLite, krx=DuckDB, read-only) |
| 2 | **broker** | `broker/` | **8001** | 키움증권 게이트웨이 FastAPI (WS Manager 포함) |
| 3 | **broker-web** | `broker-web/` | **3000** | Next.js UI |

## 실행 명령 (각 터미널 별도)

```powershell
# 터미널 1 — api
cd api
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

```powershell
# 터미널 2 — broker
cd broker
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8001
```

```powershell
# 터미널 3 — broker-web
cd broker-web
npm run dev
# → http://localhost:3000
```

## 전제 조건

- `api/.env` 존재 (`api/.env.example` 복사 후 작성)
  - `DUCKDB_PATH=../etl/db/etf_insight.sqlite3` (SQLite. 변수명은 back-compat용 유지. ETL 배치가 먼저 생성)
  - `WATCHLIST_DB_PATH=../etl/db/watchlist.sqlite3` (SQLite)
  - `KRX_DB_PATH=../etl/db/krx_ohlcv.duckdb` (OHLCV는 DuckDB 유지)
- `broker/.env` 존재 (`broker/.env.example` 복사 후 작성)
  - `KIWOOM_APPKEY`, `KIWOOM_SECRETKEY` 필수
  - `KIWOOM_ENV=paper` (기본, 모의투자)
  - `KIWOOM_ACCOUNT_NO` 필수
- `broker-web/.env.local` 이미 존재 (커밋됨, 수정 불필요)
  - `NEXT_PUBLIC_BROKER_API_URL=http://localhost:8001`
  - `FASTAPI_BASE_URL=http://localhost:8000`

## broker 주의

- 기동 시 `KiwoomWSManager.start()` 자동 실행 → 키움 WS 연결 시도
- 키움 앱 로그인 상태 + 토큰 유효해야 WS 정상 연결
- 토큰 캐시: `broker/.token_cache.json` (broker/etl 배치 공유)

## 헬스체크

```bash
curl http://localhost:8000/health   # api
curl http://localhost:8001/health   # broker
```

## MCP 엔드포인트

- api MCP: `http://localhost:8000/mcp`
- broker MCP: `http://localhost:8001/mcp`
