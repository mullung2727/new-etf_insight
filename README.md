# new-etf-insight

ETF 분석 + 키움 매매 + 재무제표 통합 워크스페이스.
서버 여러 개로 구성되니 역할·포트·경계를 먼저 본다.

## 구성 (상시 서버 3 + 배치 1)

| 컴포넌트      | 종류    | 포트 | 역할                         | 인증 / 노출        |
| ------------- | ------- | ---- | ---------------------------- | ------------------ |
| `api/`        | FastAPI | 8000 | DuckDB ETF/통계 읽기 게이트웨이 | 없음 / 공개 가능   |
| `broker/`     | FastAPI | 8001 | 키움 실시간·매매(주문/체결/잔고) | 키움 OAuth / 로컬 전용 |
| `broker-web/` | Next.js | 3000 | 프론트엔드 + DART 재무제표 API 라우트 | -                  |
| `etl/`        | 배치    | -    | DuckDB 빌드 후 종료 (상주 안 함) | -                  |

> `fintech-dashboard/`는 별도 git repo (참고용). 기능은 broker-web으로 이식 완료, 본 repo는 추적 안 함.

## 데이터 흐름

```
etl ──build──> DuckDB ──> api(:8000) ─┐
                                       ├─> broker-web(:3000) ─> 브라우저
키움 REST/WS ──> broker(:8001) ────────┘
                          DART(opendart) ──> broker-web 내부 API 라우트(/api/financial 등)
```

- 읽기 데이터(ETF/통계)는 `api`, 매매는 `broker`로 **분리**. 보안 경계 때문(아래 참조).
- DART 재무제표는 읽기전용 공개 데이터 → 별도 서버 없이 broker-web의 Next API 라우트에서 직접 호출.

## 왜 서버를 분리했나

| 축    | api (:8000)        | broker (:8001)            |
| ----- | ------------------ | ------------------------- |
| 데이터 | 읽기전용 분석(DuckDB) | 실시간 매매(주문/체결)    |
| 메서드 | GET only           | POST/PATCH/DELETE         |
| 인증   | 없음               | 키움 OAuth 토큰·실거래 키 |
| 노출   | 공개 의도(터널 가능) | **로컬 전용**(노출 금지)  |
| 상태   | 무상태             | WS 연결·토큰 캐시         |

핵심: api는 공개해도 안전(읽기전용 ETF), broker는 실거래 키를 들고 주문하므로 절대 공개 불가.
한 서버로 합치면 매매 크레덴셜이 공개 표면에 노출되고 장애가 전파됨 → 분리 유지.

## 실행

```bash
# 1) ETF 데이터 API
cd api && uv run uvicorn main:app --port 8000

# 2) 키움 브로커 (로컬 전용)
cd broker && .venv/Scripts/python -m uvicorn main:app --port 8001

# 3) 웹 프론트엔드
cd broker-web && npx next build && npx next start   # 포트 3000
```

> ⚠️ broker-web은 `next dev`(turbopack) 금지 — Tailwind v4 postcss fork-bomb으로 OOM 발생.
> 검증은 반드시 `next build && next start`.

## 환경 변수 (각 컴포넌트 .env / 루트 .env)

| 키                        | 위치              | 용도                         |
| ------------------------- | ----------------- | ---------------------------- |
| `KIWOON_MOCK_TR_APP_KEY/SECRET` | 루트 `.env`       | 키움 모의(paper) OAuth       |
| `KIWOOM_ENV`              | 루트 `.env`       | `paper`(기본) / `real`       |
| `DUCKDB_PATH`             | `api/.env`        | DuckDB 경로(기본 `../etl/db/etf_insight.duckdb`) |
| `DART_API_KEY`            | `broker-web/.env.local` | DART 전자공시 키(재무제표) |

## broker-web 주요 페이지

| 경로                 | 백엔드        | 설명                        |
| -------------------- | ------------- | --------------------------- |
| `/etfs`              | api(:8000)    | ETF 분석                    |
| `/trading`, `/notes` | broker(:8001) | 트레이딩 / 투자노트         |
| `/financial`         | DART(내부 라우트) | 재무제표 조회               |
| `/watchlist`         | broker(:8001) | 일별 모니터링 종목 + 일봉 차트 |
