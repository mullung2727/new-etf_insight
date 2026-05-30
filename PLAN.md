# new-etf-insight — 프로젝트 방향

## 핵심 목표

LLM(Claude)이 공개 투자 정보를 읽고 키움증권 계좌로 주식을 매매하는 시스템.
투자 정보는 공유 서버에서 누구나 쓰고, 매매 실행은 각자 로컬에서 돌린다.

---

## 레이어 구조

```
[공유 — 누구나 접근]          [개인 로컬 — 각자 실행]
─────────────────────         ──────────────────────
etl/                          broker/
  DART/공시 수집                 키움 REST API MCP (stdio)
  PDF 파싱                       매수매도 / 잔고 / 시세
  DuckDB 저장                    조건검색
  LLM 분류                       각자 .env (앱키/계좌)

api/                          (로컬 broker에서 직접 실행)
  FastAPI + fastapi-mcp
  ETF 데이터 게이트웨이
  투자 정보 확장 예정

web/
  Next.js 대시보드
  ETF 분석 / 투자 정보 시각화
```

---

## LLM 활용 흐름

```
Claude
  ├── api/ MCP 툴 → ETF 정보, 시그널, 투자 데이터 조회
  └── broker/ MCP 툴 → 매수매도 실행, 잔고 확인
```

Claude가 두 MCP를 동시에 들고 정보 기반 매매 판단.

---

## 레이어별 상태 및 계획

### etl/ — 데이터 파이프라인 ✅ 운영중
- DART 공시 수집 → PDF 파싱 → LLM 분류 → DuckDB
- 데이터 소스 확장 예정 (공시 외 투자 정보 추가)

### api/ — 공유 데이터 게이트웨이 ✅ 운영중
- FastAPI + fastapi-mcp (SSE)
- 현재: ETF 목록/상세/구성종목/통계
- 확장: 투자 시그널, 시세 요약, 포트폴리오 분석 등

### web/ — 대시보드 ✅ 운영중 (일부 미완)
- ETF 목록 / 상세 / 구성종목 통계
- 미완: 목록 페이지에 구성종목 비중 통합 (현재 /stats 분리)

### broker/ — 키움 매매 MCP ✅ v1 완성
- stdio MCP, 로컬 단일사용자
- 현재: 시세조회 / 잔고조회 / 매수매도(가드) / 조건검색 목록+단발
- v2 예정: 조건검색 실시간(ka10173) + 자동매매 워커 (상주 프로세스, MCP와 분리)

---

## 확장 방향

1. **투자 정보 소스 추가** — api/에 붙임 (뉴스, 재무, 수급 등 공개 데이터)
2. **broker v2** — 조건검색 실시간 구독 + 전략 기반 자동주문 워커
3. **web 트레이딩 뷰** — broker를 FastAPI+fastapi-mcp로 전환, 로컬 web/에서 수동매매 UI 추가. LLM 매매(MCP)와 수동 매매(web) 동시 지원
4. **멀티 증권사** — broker/ 구조를 다른 증권사 REST API에도 적용 가능

---

## 사용자 온보딩

```
git clone <repo>
cp .env.example .env          # DART_API_KEY 등 공유 키
cp broker/.env.example broker/.env   # 키움 앱키/계좌 (개인)
cd broker && pip install -e .
# Claude Desktop mcp_setup.md 참고 → broker/ MCP 연결
```

투자 데이터는 공유 api/에서, 매매는 본인 broker/에서.
