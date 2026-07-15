# 종목 개괄 허브 — 기술문서 (개발용)

> **상태**: 착수 전. **짝 문서**: 방향/화면은 [stock_hub_plan.md](./stock_hub_plan.md)
> LLM 개발용. 파일경로·API·타입·재사용대상·단계 체크리스트. 착수 직전 갱신.
> 원칙: **기존 자산 최대 재사용, 신규 코드 최소**(ponytail). TDD 단계별.

## 재사용 자산 (확인 완료)

| 필요 | 기존 자산 | 위치 |
|---|---|---|
| 캔들차트 | `CandleChart` (client, ChartLoader 래퍼) | `broker-web/components/watchlist/ChartLoader.tsx` |
| 일봉 데이터 | `brokerClient.getDailyChart(symbol, baseDt?)` → `DailyCandle[]` | `broker-web/lib/broker-client.ts:254` |
| LLM스코어 | `apiGet('/watchlist/scores/{date}')` → `LlmScore[]` (ticker 필터) | 기존 watchlist/[code] 참고 |
| 재무 테이블/차트 | `FinancialTable`, `FinancialChart` | `broker-web/components/financial/` |
| 재무 데이터 | `/api/financial?corp_code=&year=&reprt=&fs_div=` (Next route, `@/lib/dart`) | `broker-web/app/api/financial/route.ts` |
| corp 목록(코드매핑) | `fetchCorpCodes()` — **stock_code↔corp_code 포함** | `@/lib/dart` |
| 투자노트 패널 | `NotesPanel` — **`symbol` prop 이미 받음** | `broker-web/components/notes/notes-panel.tsx` |
| 노트 손익 | `brokerClient.getNotesPnlSummary({symbol})` | `broker-web/lib/broker-client.ts:281` |
| 노트 목록 | `brokerClient.listNotes({symbol, status})` | `:273` |
| 보유/잔고 | `brokerClient.getBalance()` → `BalanceData.acnt_evlt_remn_indv_tot: Holding[]` | `:256`, `:79` |
| 텔레그램 이력 | `telegram_stock_insights` 테이블 (아래 스키마) | `duck_watchlist.telegram_cursor()` / `TELEGRAM_DB_PATH` |

### telegram_stock_insights 스키마
```sql
date_kst TEXT, session TEXT, ticker TEXT, name TEXT,
mention_channels TEXT,   -- JSON 배열: 채널명 (소스채널 = 이것)
source_post_refs TEXT,   -- JSON 배열: 원문 post_ref (원문링크)
discovery_reason TEXT, analysis TEXT,  -- analysis = JSON: change_type/change_summary/themes
UNIQUE(date_kst, session, ticker)
```
- session 시간순: `morning < close < evening` (사전순 아님. `duck_watchlist`/`telegram.py:_SESSION_RANK` 참고)
- 기존 라우터: `api/routers/telegram.py` — `/telegram/digest/latest` 하나뿐. 여기에 추가.

## 신규 코드 (이것만 새로 씀)

1. **code→corp_code 매핑 유틸** — `fetchCorpCodes()`(stock_code 보유)로 룩업. `broker-web/lib/dart.ts` 또는 신규 헬퍼.
2. **API `GET /telegram/mentions/{ticker}`** — `api/routers/telegram.py`에 추가.
   - query: `from`(date_kst), `to`, `session`(옵션)
   - resp: `[{date_kst, session, channels: string[], post_refs: string[], change_type, change_summary, themes: string[]}]`
   - `mention_channels`/`source_post_refs`/`analysis`는 JSON 파싱(기존 `_loads` 재사용).
3. **API 같은테마 종목** — `GET /telegram/theme-peers/{ticker}`: 이 종목 최근 themes → 같은 theme 가진 다른 ticker 목록. (또는 mentions 응답에 themes 동봉하고 프론트에서 별 호출) — 단순화 위해 **별 엔드포인트**로.
4. **셸/탭 UI** — `broker-web/app/stock/[code]/page.tsx` (서버컴포넌트) + 탭별 서브컴포넌트.

## 라우팅

- `broker-web/app/stock/[code]/page.tsx` — 서버컴포넌트. 탭 = URL 쿼리 `?tab=overview|chart|financial|telegram|notes` (기본 overview).
  - `ui/tabs.tsx`(client)는 서버데이터 탭에 부적합 → 쿼리 기반 탭 네비 + 조건부 서버 렌더.
- `broker-web/app/watchlist/[code]/page.tsx` → `redirect('/stock/${code}?tab=chart' + date/name 쿼리 보존)`.
- 링크 교체:
  - `components/home/digest-card.tsx:36` `/watchlist/${it.ticker}` → `/stock/${it.ticker}`
  - watchlist 목록의 종목 링크 (`app/watchlist/page.tsx` 확인 후 교체)

## 헤더 데이터

- 현재가/등락: `brokerClient.getQuote(symbol)` → `Quote` (존재 확인됨, `broker-client.ts`). 차트 최신봉 폴백 불필요.
- 보유손익: `getBalance().acnt_evlt_remn_indv_tot`에서 `stk_cd===code` 항목의 `evltv_prft`(평가손익)·`prft_rt`(수익률). 필드 실측 확인됨(`broker-client.ts:65` Holding).
- ~~watchlist 담기 버튼~~ — **MVP 제외**(쓰기 API 없음, 배치 소유). plan "미해결/나중" 참고.

## 단계별 체크리스트 (각 단계 = 테스트+보고+확인)

- [ ] **1. 토대** — code→corp_code 유틸 + 헤더용 종목메타(현재가) 로더. 테스트: 알려진 종목코드→corp_code 매핑 assert.
- [ ] **2. 셸+라우팅** — `/stock/[code]` 셸, 탭 네비(URL), watchlist/[code] redirect, digest 링크 교체. 테스트: 탭 전환·redirect 동작.
- [ ] **3. 차트 탭** — CandleChart 이식 + 기간선택(1M/3M/6M/1Y). 기존 DAYS_BEFORE/AFTER 로직 참고.
- [ ] **4. 재무 탭** — FinancialTable/Chart + corp_code 배선. code→corp_code 실패 시 빈상태 처리.
- [ ] **5. 텔레그램 탭** — `/telegram/mentions/{ticker}` API(+test_telegram_digest.py 스타일 유닛테스트) + 기간/세션 필터 UI + 소스채널 뱃지 + 원문링크.
- [ ] **6. 노트+보유손익 탭** — `NotesPanel symbol={code}` + balance 필터 손익.
- [ ] **7. 개요 탭** — 요약 집계(핵심 재무지표·LLM스코어·텔레그램 언급3·보유손익미니·최근노트1) + 이벤트 타임라인(mentions+노트events+스코어 날짜 조인). 재무 핵심지표는 재무탭과 동일 corp_code 로더 재사용(별도 fetch 아님).
- [ ] **8. 같은테마 위젯** — `/telegram/theme-peers/{ticker}` + 텔레그램 탭 하위 나열/링크.

## 주의

- broker-web는 **Next 변형** — `broker-web/AGENTS.md` 규약. 코드 전 `node_modules/next/dist/docs/` 확인.
- 색: semantic 토큰만 (하드코딩색 hook 차단). `broker-web/DESIGN.md`.
- 테스트: api쪽 `PYTHONPATH=. uv run python -m unittest` / pytest(telegram은 pytest 사용). 맨몸 python 금지.
- `.env`는 root에서만.

## 착수 시 확인 필요 (미검증)

- `app/watchlist/page.tsx` 종목 링크 위치
- `LlmScore` 스코어 조회 엔드포인트 정확 경로 (`/watchlist/scores/{date}`)

### 해소됨 (코드 실측)
- ~~`Holding` 평가손익 필드명~~ → `stk_cd`/`evltv_prft`/`prft_rt` 확인 (`broker-client.ts:65`)
- ~~`getQuote` 존재 여부~~ → 존재 (`brokerClient.getQuote`)

