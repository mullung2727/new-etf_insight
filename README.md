# new-etf-insight

한국 주식 리서치·시그널·매매 운영 워크스페이스.

처음엔 ETF 공시 분석에서 시작했지만, 지금은 아래가 한 레포에 같이 있다.

- ETF 투자설명서(DART) 수집·LLM 분석
- 거래량 워치리스트 / 재무 랭킹
- 네이버 증권 리서치 PDF
- 텔레그램 공개채널 종목 시그널
- DART 재무제표 UI
- 키움 시세·주문·종가배팅·투자노트

서버·배치 역할과 **보안 경계**를 먼저 보고 작업한다.

---

## 구성

| 컴포넌트 | 종류 | 포트 | 역할 | 노출 |
| --- | --- | --- | --- | --- |
| `api/` | FastAPI | 8000 | SQLite/DuckDB 읽기 게이트웨이 + MCP | 공개 가능(읽기 위주) |
| `broker/` | FastAPI | 8001 | 키움 REST/WS · 주문 · notes · close-bet | **로컬 전용** |
| `broker-web/` | Next.js | 3000 | UI + DART 재무 등 일부 BFF 라우트 | 로컬(또는 터널 정책에 따름) |
| `etl/` | 배치 | - | 수집·분석·DB 적재 후 종료 (상주 안 함) | - |
| `ops/` | 스케줄 | - | Windows Task / 배치 지시서·레지스트리 | - |

> `fintech-dashboard/`는 별도 repo 참고용. 기능은 `broker-web`으로 이식 완료, 본 repo는 추적 안 함.

---

## 데이터 흐름

```
DART / KRX / 키움 / 텔레그램 / 네이버
              │
              ▼
           etl 배치
              │
              ▼
     etl/db  +  exports/
              │
     ┌────────┴────────┐
     ▼                 ▼
 api(:8000)      broker(:8001) ◀── 키움 REST/WS
  읽기 전용          매매·노트·WS
     └────────┬────────┘
              ▼
        broker-web(:3000)
              │
              ├── api / broker 프록시 호출
              └── DART 직접 호출 (/api/financial 등)
```

- **읽기 분석 데이터** → `api`
- **매매·실시간** → `broker`
- **재무제표(DART 공개)** → `broker-web` Next API 라우트에서 직접 호출

---

## 왜 서버를 분리했나

| 축 | api (:8000) | broker (:8001) |
| --- | --- | --- |
| 데이터 | ETF·워치·리서치·텔레그램 등 적재 결과 | 실시간 시세·주문·체결·잔고 |
| 메서드 | 대부분 GET (+ research 다운로드 등 일부 POST) | POST/PATCH/DELETE 포함 |
| 인증 | 없음 | 키움 OAuth · 실거래 키 |
| 노출 | 공개 가능(터널 가능) | **로컬 전용 (노출 금지)** |
| 상태 | 무상태 읽기 | WS 연결 · 토큰 캐시 · notes DB |

핵심: api는 읽기 표면, broker는 돈이 움직이는 면. 합치면 크레덴셜·장애가 같이 터진다.

---

## 데이터 저장소 (`etl/db`)

| 파일 | 용도 | 주 writer |
| --- | --- | --- |
| `etf_insight.sqlite3` | ETF 분석 레코드·보유종목 | `etl` daily pipeline / `build_db.py` |
| `watchlist.sqlite3` | 일별 워치리스트 · llm_scores · 종가배팅 관련 | watchlist / close-bet 배치 |
| `krx_ohlcv.duckdb` | 전종목 일봉 OHLCV 캐시 | `build_krx_ohlcv.py` 등 |
| `telegram_public.sqlite3` | 텔레그램 공개채널 수집·인사이트 | telegram 배치 |
| `financial_indicators.sqlite3` | 재무 지표·랭킹 | `build_financial_indicators.py` |

기타:

- `etl/runs/{YYYYMMDD}/` — ETF 일배치 PDF·JSON
- `etl/exports/stock_reports/` — 네이버 리서치 PDF
- `broker/notes.db` — 투자노트·체결 연동 (broker 로컬)

> env 변수명 `DUCKDB_PATH`는 **back-compat**. 실제 ETF 본체는 SQLite(`etf_insight.sqlite3`). OHLCV만 DuckDB.

---

## 기능 맵

### 1) ETF Insight
- DART 후보 수집 → 투자설명서 PDF → LangGraph/LLM 분석 → JSON → SQLite
- 기재정정은 기존 레코드 갱신 여부 LLM 판단 (`first_rcept_dt` 보존)
- 진입: `new_etf_insight.daily_pipeline` / skill `skills/new-etf-insight-batch`

### 2) Watchlist · Rankings
- D-1: KRX OpenAPI 거래량 신규진입 (`build_watchlist.py`)
- 당일: 키움 ka10030 (`build_intraday_ranking.py`, 장 마감 후)
- 재무 랭킹·기간 지표는 `financial_indicators` + api `/rankings`

### 3) Research
- 네이버 증권 리포트 배치 다운로드
- api `/research` 검색·PDF·선택 다운로드 잡

### 4) Telegram
- 공개채널 수집 → 종목 추출/인사이트 → Discord digest
- api telegram 라우트 + admin UI

### 5) Financial (DART)
- broker-web `/financial`, `/api/financial*` — 공시 재무제표 조회·비교

### 6) Trading · Notes · Close-bet
- 시세/호가/잔고/주문/조건식: `broker`
- 투자노트 + 체결 자동 연동
- 종가배팅: 워치리스트 기반 주문·청산·검증 배치 + UI `/close-bet`

---

## broker-web 페이지

| 경로 | 백엔드 | 설명 |
| --- | --- | --- |
| `/` | - | 홈 |
| `/etfs`, `/etfs/[etf_key]` | api | ETF 목록·상세 |
| `/stats` | api | ETF 통계 |
| `/rankings` | api | 지표 랭킹 |
| `/research` | api | 종목 리서치 PDF |
| `/watchlist`, `/watchlist/[code]` | api | 워치리스트·일봉 |
| `/stock/[code]` | api + broker-web | 종목 허브 |
| `/financial` | DART(내부) | 재무제표 |
| `/trading` | broker | 트레이딩 |
| `/notes` | broker | 투자노트 |
| `/close-bet` | broker + 설정 | 종가배팅 |
| `/admin/settings` | broker | 설정 |
| `/admin/telegram` | api / 로컬 설정 | 텔레그램 채널 관리 |

---

## 실행

### 로컬 3서버

```powershell
# 한 번에 재시작
.\scripts\restart_all_servers.ps1
```

개별:

```powershell
# 1) 데이터 API
cd api; .\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000

# 2) 키움 브로커 (로컬 전용)
cd broker; .\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8001

# 3) 웹
cd broker-web; npm run dev
# 검증 빌드: npm run build; npm run start
```

헬스:

- `http://localhost:8000/health`
- `http://localhost:8001/health`
- `http://localhost:3000`

### Docker

`compose.yml`: `broker(:8001)` + `broker-web(:3000)`.  
`api(:8000)`와 `etl/db`는 호스트에 두고 컨테이너가 `host.docker.internal`로 접근.

### broker-web 주의

- `next dev --turbopack` 금지 이력 있음 (Tailwind v4 postcss OOM).
- 현재 스크립트는 `npm run dev` → `next dev --webpack`.
- 배포/검증은 `build` + `start` 권장.

---

## 배치 · 스케줄

- **운영 실행 주체**: Windows Task Scheduler (`ops/scheduled-tasks/`)
- **지시서·레지스트리**: `ops/batches/` (`openclaw-cron.registry.json` 포함)
- OpenClaw cron은 레거시/폴백 메타로 취급

주요 잡 (KST, 레지스트리·README 기준):

| 잡 | 대략 시각 | 하는 일 |
| --- | --- | --- |
| daily-new-etf-insight-batch | 07:00 | ETF 일배치 + DB sync |
| daily-etf-watchlist-krx-ohlcv | 화–토 08:00 | 전일 KRX OHLCV |
| daily-etf-watchlist-intraday-kiwoom | 월–금 14:59 시작 | 당일 워치 사전 생성 + 15:00 ka10001 시세 스냅샷 + 스코어 |
| close-bet-order | 월–금 15:19 | 종가배팅 주문 |
| close-bet-order-report | 월–금 15:21 | 주문 결과 리포트(주문 재시도 금지) |
| close-bet-verify 등 | 장후 | 체결 검증·청산 계열 |
| telegram-session (morning/close/evening) | 10:00 / 16:00 / 00:00 | 수집→분석→Discord |
| daily-naver-research | 18:00 | 네이버 리서치 PDF |

상세 시각·러너는 `ops/batches/README.md`와 레지스트리를 본다.

---

## 환경 변수

| 키 | 위치 | 용도 |
| --- | --- | --- |
| `KIWOON_MOCK_TR_APP_KEY` / `SECRET` | 루트 `.env` | 키움 모의 OAuth |
| `KIWOOM_ENV` | 루트 `.env` | `paper`(기본) / `real` |
| `DART_API_KEY` | 루트 또는 `broker-web/.env.local` | DART 공시·PDF·재무 |
| `DUCKDB_PATH` | `api` 등 | ETF DB 경로 (기본 `etl/db/etf_insight.sqlite3`) |
| `WATCHLIST_DB_PATH` | api/배치 | `watchlist.sqlite3` |
| `KRX_DB_PATH` | api/배치 | `krx_ohlcv.duckdb` |
| `TELEGRAM_DB_PATH` | api/배치 | `telegram_public.sqlite3` |
| `FINANCIAL_DB_PATH` | api | `financial_indicators.sqlite3` |
| `KRX_API_KEY` | 루트 `.env` | KRX OpenAPI |
| `DISCORD_WEBHOOK_URL` 등 | 루트 `.env` | 배치 알림 |
| `CORS_ORIGINS` | `api` | 기본 `http://localhost:3000` |

시크릿은 커밋하지 않는다. 에이전트도 `.env` 내용을 출력하지 않는다.

---

## 개발 시 주의

- **broker(:8001) 외부 노출 금지**
- 배치·테스트 작업 디렉터리는 보통 `etl/` (루트 venv와 다름)
- ETF 본체 SQLite WAL + OHLCV DuckDB 혼재 — reader는 `query_only` 패턴
- 배치 지시서 한글은 UTF-8 안전 리더로 읽을 것 (PowerShell 5.1 기본 인코딩 주의)
- 서버 작업: `skills/new-etf-insight-server-dev`
- ETF 배치: `skills/new-etf-insight-batch`
- ETL 코드 레퍼런스: `skills/new-etf-insight-etl-reference`
- 텔레그램: `skills/new-etf-insight-telegram-collect` / `telegram-query`

---

## 관련 문서

| 경로 | 내용 |
| --- | --- |
| `Agents.md` / `Claude.md` | 에이전트 작업 규칙 |
| `etl/DAILY_PIPELINE_FLOW.md` | ETF 일배치 흐름 |
| `etl/OPENCLAW_PIPELINE_GUIDE.md` | OpenClaw 파이프라인 |
| `ops/batches/README.md` | 배치 잡·스케줄 운영 |
| `broker/README.md` | 키움 broker / MCP |
| `api/docs/` | 터널·MCP 설정 |
| `docs/` | 설계·완료 플랜 아카이브 |
