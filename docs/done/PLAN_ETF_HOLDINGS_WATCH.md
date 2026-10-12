# PLAN: ETF 보유 모니터링 탭 (TIME 4종)

한 줄: TIME 4 ETF 일별 보유를 크롤·저장하고 broker-web 새 탭에 편입/편출 + 5%/3% 하이라이트 표시, 이벤트를 Discord 신규 채널로 알림.

## 진행

- [x] 구현·테스트 머지 (PR #73, 2026-10-12) + 후속 PR #77 (비중 증감 칸·종목 추이 그래프, 탭명 ETF 추적)
- [x] 운영 등록 (스케줄 작업·config) — 2026-10-11 레지스트리 `daily-etf-holdings` 추가 + `\new-etf_insight\daily-etf-holdings` 등록, 웹훅 URL 등록·테스트 발송 확인
- [x] 첫 실가동 확인 (2026-10-12 16:30) — 4종 178행 저장, 이벤트 6건(495060 소액 편입·편출), 알림 대상 0건이라 Discord 미발송, 결과 0
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

## 1. 배경·목표

- 426030 등 TIME 액티브 ETF는 일별 보유(PDF)를 운용사 홈페이지에 공시한다.
- 확신주(상위/5%+) 편입·편출 변화를 상시 모니터링하는 탭.
- 자동매매 편입은 범위 밖(사용자 고민 중). 이번은 조회·알림만.

## 2. 범위 (사용자 확정)

| 항목 | 결정 | 출처 |
|---|---|---|
| 대상 ETF | 426030·385720·495060·456600 (TIME 4) | 사용자 |
| KoAct/ACE 2종 | 제외 (파서 확보 후 후속) | 사용자 |
| 탭 위치 | broker-web 새 라우트 `/holdings` + nav 항목 | 사용자("웹페이지 새로운 탭") |
| 하이라이트 | 보유 전수: w≥5 상시 강조, w≤3 상시 구분 표시 | 사용자 |
| 하이라이트 스타일 | 5%+ accent badge, 3%− muted 처리 (전수 강조 시 가독성 확보) | 내 판단 |
| Discord | 신규 웹훅 채널 (URL은 사용자 제공, env `ETF_HOLDINGS_DISCORD_WEBHOOK_URL`) | 사용자 |
| Discord 발송 조건 | 이벤트 발생일에만 (신규편입/편출/5%돌파/3%이탈). 전수 매일 발송 아님 | 내 판단(스팸 방지) |
| 실행 시각 | 평일 16:30 (장후) | 내 판단 |
| 초기 적재 | temp CSV 4종 이관 (426030: 22-05+, 나머지: 25-02+) | 내 판단 |

## 3. 설계

### 3.1 저장 — `etl/db/etf_insight.sqlite3` (api가 이미 read-only로 읽는 DB)

- `etf_holdings_daily(asof TEXT, etf TEXT, code TEXT, name TEXT, qty REAL, amt_krw REAL, weight_pct REAL, fetched_at TEXT, PRIMARY KEY(asof, etf, code))`
  - 작성: `etl/scripts/build_etf_holdings.py:store()`. api는 읽기만.
- `etf_holding_events(date TEXT, etf TEXT, code TEXT, name TEXT, kind TEXT, w_prev REAL, w_cur REAL, PRIMARY KEY(date, etf, code, kind))`
  - kind ∈ ENTRY(신규) / EXIT(편출) / UP5(5%돌파) / DN3(3%이탈). 작성: `etl/scripts/build_etf_holdings.py:detect_events()`.
  - 비교 기준: 당일 스냅샷 vs 직전 스냅샷 (휴장 다음날도 직전 거래일 대비로 자연 처리).

### 3.2 배치 — `etl/scripts/build_etf_holdings.py:main()`

1. TIME 4종 당일 PDF 크롤 (`fetch()`, temp/crawl_time.py 로직 이식, cert verify off — 사이트 인증서 만료).
2. `store()` 멱등 적재 (PK 존재 시 스킵).
3. `detect_events()`로 전일 대비 이벤트 산출·저장.
4. 이벤트 있으면 `etl/scripts/notify.py:send_etf_holdings_alert()` 1회 발송 (없으면 조용).
5. 크롤/DB 실패 시 예외 → 러너가 batch 채널로 실패 보고 (§5).

### 3.3 알림 — `etl/scripts/notify.py:send_etf_holdings_alert()`

- `ETF_HOLDINGS_DISCORD_WEBHOOK_URL` 미설정 → 로그 남기고 skip, False (폴백 없음 — 신규 채널 명시성).
- 메시지: 날짜 + ETF별 이벤트 목록 (종목·종류·비중 전후). 1900자 초과 시 잘라서 1건.

### 3.4 API — `api/routers/holdings.py` (신규) + `api/main.py`에 include

- `GET /holdings/latest?etf=` → 당일 보유 + 플래그 (`is_conviction`=w≥5, `is_faded`=w≤3). 플래그 계산은 API가 담당 (UI는 표시만).
- `GET /holdings/events?days=30&etf=` → 이벤트 피드.
- `GET /holdings/history?etf=&code=` → 종목별 비중 시계열.
- 읽기 전용 sqlite (`api/duck.py` 패턴).

### 3.5 UI — `broker-web/app/holdings/page.tsx` (신규) + nav 항목 추가

- ETF 선택 → 보유표 (5%+ badge, 3%− muted) + 최근 이벤트 피드.
- 데이터는 `apiGet()` 경유 (page.tsx의 `/watchlist` 호출 패턴).

### 3.6 운영 등록

- `ops/scheduled-tasks/run-etf-holdings.ps1` (run-krx-ohlcv.ps1 템플릿, UTF-8 BOM) + `ops/scheduled-tasks/etf-holdings.xml` (평일 16:30).
- `ops/batches/README.md` 매핑 행 추가.
- `.env`에 `ETF_HOLDINGS_DISCORD_WEBHOOK_URL=` 빈 키 추가 (값은 사용자 제공 후 첫 실가동).

## 4. 요구사항 → 테스트

| # | 요구사항 | 테스트 |
|---|---|---|
| 1 | 새 탭에 보유 표시 | api `GET /holdings/latest` 200 + shape 단위테스트 (`api/test_holdings.py`) |
| 2 | 5%+/3%− 하이라이트 | API 플래그 단위테스트 (`is_conviction`/`is_faded` 경계값 5.0/3.0 포함) |
| 3 | 편입/편출/돌파/이탈 검출 | 실행 검증: fixture 2일분 적재 후 events 4종 + 무변화=0건 확인 (etl 배치 단위테스트 관행 없음) |
| 4 | Discord 이벤트 알림 | webhook 미설정 → False·무예외 실행 검증 |
| 5 | 중복 적재 없음 | 배치 2회 실행 → row 변화 없음 실행 검증 |
| 6 | UI 빌드 | `npm run build` 통과 (broker-web) |

## 5. 운영 체크 (`ops/scheduled-tasks` 관행)

- 휴장일: 스냅샷 0건이면 조용히 종료(exit 0). 달력 조회 실패는 실패扱.
- 실패 알림 1회: 파이썬은 신호 알림만, 실패 보고는 러너(`send_report_messages.py` → batch 채널).
- `load_dotenv()` 필수 (notify 직접 호출 진입점).
- stdout utf-8 재설정, ps1은 UTF-8 BOM.
- 스케줄 worktree 경로 실행 금지 (정식 경로만).

## 6. 손 안 대는 것

- 자동매매/주문 로직 일체 (범위 밖).
- 기존 etf_insight 테이블 (신규 테이블만 추가).
- KoAct/ACE (후속 PLAN).

## 구현 차이

- UI ETF 선택 버튼: 코드 + 한글 약칭 (426030 나스닥100 / 385720 KOSPI / 495060 밸류업 / 456600 글로벌AI, Yahoo longName 기준).
- `ops/batches/README.md` 매핑: 별도 지침 .md 없이 PLAN 문서 참조 (`daily-etf-holdings` → 본 문서).
- 2026-10-11 드라이런: `run-etf-holdings.ps1` 직접 실행 → 4종 모두 skip (10-08 적재済, 10-09 한글날 휴장), exit 0, 알림 없음. 정식 첫 가동은 웹훅 URL 등록 후.
- 원래 이벤트=전일 대비 경계 통과(상태 없음) → 실제 종목별 확신 상태 기억: 5% 이상 진입, 3% 미만 이탈, 사이 왕복 무시 (상태 없을 땐 TSLA 5%돌파 20회 등 반복 알림)
- 원래 5%+ 신규편입 시 ENTRY+UP5 2건 → 실제 ENTRY 1건 (비중은 w_cur 로 표시)
- 원래 현금 행 포함 → 실제 이벤트·상태 계산에서 현금(code 빈값) 제외, 보유 저장·표시는 유지
- 원래 Discord=전 이벤트 → 실제 DB·화면은 전 이벤트, Discord 는 확신주 관련만 (5%+ 편입, 5% 진입, 3% 이탈, 확신주 편출). 알림일 비율 426030 12%, 나머지 6~13% (소액 편입·편출 스팸 방지)
- 원래 날짜별 이벤트 누적 → 실제 매 실행 ETF별 보유 전체로 재계산(`rebuild_events`). 백필로 중간 날짜 채워도 정합. 2026-10-11 기존 이벤트 2,908 → 1,992 재계산
- run_daily 실패 처리: 중간 ETF 실패해도 앞 ETF 알림 먼저 발송 후 예외 전파, 크롤 예외를 휴장으로 삼키지 않음 (§5 "조회 실패는 실패扱" 준수)
- 레지스트리 `instructionFile` 도 본 문서 (별도 지침 .md 없음)
- 범위 추가(PR #77, 사용자 요청): `/holdings/latest` 에 d1/d7/d30 (전일·7일 전·30일 전 이하 스냅샷 대비 %p), 종목명 클릭 시 `/holdings/history` 비중 추이 그래프(5%·3% 기준선), nav 라벨 보유현황 → ETF 추적. 편출 기간은 그래프에서 0 으로 내려가지 않고 이어 그려짐
- 이벤트 기간 기준을 최신 이벤트일 → 오늘(localtime) 기준으로 변경 (PR #73 리뷰)
