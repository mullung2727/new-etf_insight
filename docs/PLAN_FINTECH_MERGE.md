# fintech-dashboard → broker-web 통합 계획

## 한 줄 요약
재무제표(DART)는 Next API 라우트 통째 복사(추가형, 중복 없음). watchlist는 UI만 이식하고
데이터는 기존 broker FastAPI 재사용 — **일봉(ka10081) 엔드포인트 1개만** 백엔드 신규.

## 결정사항 (확정)
- **repo 병합 X.** 필요한 파일만 broker-web으로 단순 복사. fintech-dashboard repo는 별도 보관.
- **watchlist 데이터 = broker 백엔드 재사용.** fintech의 kiwoom 서버액션(auth/잔고/시세) 버림.
- **재무제표 = Next.js /api 라우트 유지.** DART는 Kiwoom 무관 → 백엔드 둘 필요 없음.

## 중복 처리 원칙 (가져오지 않음 — broker 백엔드에 이미 존재)
| fintech 서버액션 | 대체(이미 있음) |
|---|---|
| `actions/auth.ts`, `libs/token.tsx` | `broker/kiwoom/auth.py` |
| `actions/excuted_balcance.js` (잔고 kt00005) | `broker/kiwoom/account.py` |
| `actions/stock_info.js` (현재가) | `broker/kiwoom/quotes.py` |
| `actions/daily_prc.js` (일봉 ka10081) | **없음 → 신규 1개** |

## 핵심 갭
broker `quotes.py` = 현재가 + 호가만. **일봉(ka10081) 없음.** watchlist 차트용 백엔드 신규 필요.

---

## Feature A — 재무제표 (독립, 저위험, 중복 0)

### A0 — 한 페이즈로 충분
- **복사(lib)**: `lib/dart.ts`, `lib/parser.ts`, `lib/balanceSheetUtils.ts` → broker-web/lib/
- **복사(types)**: `types/dart.ts`, `types/balanceSheet.ts` → broker-web/types/
- **복사(api)**: `app/api/{company,corps,financial}/route.ts` → broker-web/app/api/
- **복사(컴포넌트)**: `app/components/Financial{Chart,Table}.tsx`, `SearchBar.tsx` → broker-web/components/financial/
- **신규 라우트**: broker-web `app/financial/page.tsx` (fintech `app/page.tsx` 참고)
- **env**: `DART_API_KEY` broker-web `.env`에 확인/추가
- **nav**: `components/common/nav.tsx` links에 `재무제표` 추가
- **검증**: 종목 검색 → 재무제표 표/차트 렌더
> 끝나면 확인 받고 Feature B.

---

## Feature B — watchlist (백엔드 재사용)

### B0 뼈대 — 일봉 end-to-end (차트 1개 뚫기)
- **백엔드**: `tr.py` `TR_DAILY_CHART = "ka10081"` / `quotes.py` `get_daily_chart(symbol,start,end)`
  (`/api/dostk/chart`, 기존 `get_quote` TR 호출 패턴 그대로) / `routers/quotes.py`
  `GET /quotes/{symbol}/daily?start=&end=`
- **검증**: `curl http://localhost:8001/quotes/005930/daily?...` → 일봉 배열
- **프론트**: `broker-client.ts` `getDailyChart()` / watchlist `[code]` 차트 페이지 이식
  (`CandleChart.tsx`, `ChartLoader.tsx`) / `getStockDataAction` 호출을 `brokerClient.getDailyChart`로 교체
- **검증**: 차트 1개 렌더
> 끝나면 확인 받고 B1.

### B1 살 — 목록 페이지 + 네비
- watchlist 목록 페이지 이식: `data/stock_data.json` + 종목명은 Feature A의 `dart.ts` `fetchCorpCodes` 재사용
- `nav.tsx`에 `watchlist` 링크 추가
- **검증**: 목록 → 종목 클릭 → 차트 이동

---

## 작업 순서
A0 → (확인) → B0 → (확인) → B1. 각 단계 검증 결과 보고 후 다음 단계.
