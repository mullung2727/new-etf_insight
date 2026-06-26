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
- `broker-web/.env.local` 이미 존재
  - `NEXT_PUBLIC_BROKER_API_URL` **미설정 권장** — 미설정 시 클라가 접속 host에서 broker(:8001)
    런타임 유도(localhost로 열면 localhost:8001, IP로 열면 그 IP:8001). IP 박으면 환경 바뀔 때 깨짐.
  - `FASTAPI_BASE_URL=http://localhost:8000` (서버측 프록시용, localhost 고정 OK)

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

## 트러블슈팅 (2026-06-26 실측)

### 1. node 프로세스 수백 개 → PC 프리징/재부팅
- **현상**: `npm run dev` 후 페이지 컴파일하면 node 프로세스가 수백~900개로 폭증, 메모리 고갈, PC 멈춤.
- **원인**: Next16 `next dev`는 turbopack 기본. turbopack이 Tailwind v4 postcss를 **node 자식 프로세스**
  (`.next/dev/build/postcss.js`)로 띄우고 회수를 안 함 → 누적 폭주.
- **해결**: dev를 webpack로. `package.json`에 `"dev": "next dev --webpack"` (적용 완료). webpack은
  postcss를 인프로세스 실행 → 자식 0개. (turbopack보다 약간 느리지만 폭주 없음.)
- **응급정리**: `Get-CimInstance Win32_Process -Filter "Name='node.exe'" | ? { $_.CommandLine -like '*broker-web*' } | % { Stop-Process -Id $_.ProcessId -Force }`

### 2. 페이지는 뜨는데 "끊김"·"로딩…"에서 멈춤
- **현상**: broker는 떠 있는데(`/health` 200) UI 우상단 "끊김", 데이터 "로딩…"만.
- **원인**: 브라우저가 broker를 **하드코딩 IP**(예전 `.env.local`의 `172.30.1.94:8001`)로 부르는데
  broker는 로컬전용(`127.0.0.1`)이라 그 IP는 안 받음 → 무한대기.
- **해결**: broker 주소를 접속 host에서 런타임 유도(`lib/broker-base.ts`). `.env.local`의
  `NEXT_PUBLIC_BROKER_API_URL` 비우면 됨. **이 PC에선 `http://localhost:3000`으로 접속**.
- **코드 바꿔도 안 바뀌면**: 브라우저가 옛 번들 캐시 → **하드 새로고침 Ctrl+Shift+R**.
- **폰·다른 PC에서 보려면**: broker를 `--host 0.0.0.0`로 띄워야 함(매매 broker가 LAN에 노출됨 — 보안 판단 필요).
  기본은 `--host 127.0.0.1`(로컬전용, 안전).
