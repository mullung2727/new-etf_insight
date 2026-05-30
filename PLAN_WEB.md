# Plan: ETF Insight 웹 대시보드

## Context

DuckDB(`etl/db/etf_insight.duckdb`)에 싱크된 ETF 레코드를 시각화하는 Next.js 프론트엔드 추가.
목표: PDF 요약 개별 확인 + 특정기간 상장예정 ETF 구성종목 비중 통계 + 국가 필터.

---

## 구조

```
web/                          ← Next.js 앱 (project root 아래)
├── app/
│   ├── layout.tsx
│   ├── page.tsx              # ETF 목록 (기간 필터 + 국가 필터)
│   ├── etfs/[etf_key]/
│   │   └── page.tsx          # ETF 상세 (PDF 요약 전체 표시)
│   ├── stats/
│   │   └── page.tsx          # 통계 (구성종목 비중 차트)
│   └── api/
│       ├── etfs/route.ts     # GET ?begin=&end=&country=&pre_listing=
│       ├── etfs/[etf_key]/route.ts
│       └── stats/holdings/route.ts  # GET ?begin=&end=&country=
├── components/
│   ├── ui/                   # shadcn 자동 생성
│   ├── etf-table.tsx         # 목록 테이블
│   ├── etf-detail.tsx        # 상세 카드
│   ├── holdings-chart.tsx    # 구성종목 바 차트
│   └── filter-bar.tsx        # 기간 + 국가 필터 공용
├── lib/
│   ├── db.ts                 # DuckDB 싱글턴 연결
│   └── queries.ts            # SQL 쿼리 함수
├── tests/                    # Playwright E2E 테스트 (testDir: ./tests)
│   ├── api.test.ts           # API 라우트 테스트 (Steps 2+3)
│   ├── etf-list.test.ts      # 목록 페이지 테스트 (Step 4)
│   ├── etf-detail.test.ts    # 상세 페이지 테스트 (Step 5)
│   └── stats.test.ts         # 통계 페이지 테스트 (Step 6)
├── .env.local                # ETF_DB_PATH=../etl/db/etf_insight.duckdb
├── next.config.ts            # serverExternalPackages: ['@duckdb/node-api']
├── playwright.config.ts      # testDir: ./tests, baseURL: http://localhost:3000
└── package.json
```

---

## 기술 스택

- **Next.js 15** (App Router)
- **shadcn/ui** (테이블, 카드, 셀렉트, 달력)
- **shadcn charts** (recharts 기반 - 구성종목 바 차트)
- **@duckdb/node-api** — DuckDB Node.js 바인딩 (서버사이드 전용)
- 패키지 관리: `npm`

---

## 페이지별 상세

### 1. ETF 목록 (`/`)
- 필터: 기간(begin/end, `first_rcept_dt` 기준), 국가(`primary_country`)
- 테이블 컬럼: [체크박스], fund_name, asset_manager, index_name, primary_country, theme_status, first_rcept_dt, revision_count, has_holdings
- `has_holdings`: `etf_holdings` rows 유무 (EXISTS 서브쿼리) — `false`면 체크박스 disabled
- `theme_status`: LLM 분류 결과 (theme/mixed/non_theme/unknown)
- `is_pre_listing_etf` 필터/컬럼 없음 — 프로젝트 전체가 상장예정 ETF만 다룸
- 체크박스 선택 → 상단 "N개 ETF 통계 보기" 버튼 노출 → `/stats?keys=...` 이동
- 행 클릭 → ETF 상세 이동

### 2. ETF 상세 (`/etfs/[etf_key]`)
- 상단: fund_name, asset_manager, index 정보, primary_country, 공시일
- 상단 배지: theme_status, theme_bucket, structure_tags, classification_confidence
- 중단: holdings 테이블 (name, ticker, exchange, weight) + 없을 경우 holdings_summary 표시
- 하단: keywords, trend_summary, classification_evidence, missing_info

### 3. 통계 (`/stats?keys=etf_key1,etf_key2,...`)
- 진입: 목록 페이지 체크박스 선택 후 "통계 보기" 버튼
- keys 없으면 → "목록에서 ETF를 선택해주세요" + 목록으로 버튼
- 요약 카드: 선택 ETF 수, 구성종목 보유 ETF 수
- 차트: 선택 ETF 구성종목별 평균 비중 Top 20 (가로 바)
- 목록으로 버튼 (항상 표시)

---

## API 응답 Shape

### `GET /api/etfs`
```ts
{ etfs: EtfListItem[], countries: string[] }
```

### `GET /api/etfs/[etf_key]`
```ts
{ detail: EtfDetail, holdings: Holding[] }
```

### `GET /api/stats/holdings?keys=key1,key2,...`
```ts
{ stats: HoldingStat[], summary: StatsSummary }
```

---

## 핵심 SQL

### 목록 조회
```sql
SELECT etf_key, fund_name, asset_manager, index_name,
       primary_country, first_rcept_dt, is_pre_listing_etf, revision_count
FROM etf_records
WHERE ($begin IS NULL OR first_rcept_dt >= $begin)
  AND first_rcept_dt <= COALESCE($end, STRFTIME('%Y%m%d', CURRENT_DATE))
  AND ($country IS NULL OR primary_country = $country)
ORDER BY first_rcept_dt DESC
-- $pre_listing 파라미터 제거 — 상장예정 ETF 전용 프로젝트
```

### 구성종목 비중 통계 (선택 ETF keys 기준)
```sql
SELECT
    h.name,
    ROUND(AVG(TRY_CAST(REPLACE(REPLACE(h.weight, '%', ''), ' ', '') AS DOUBLE)), 2) AS avg_weight,
    CAST(COUNT(DISTINCT h.etf_key) AS INTEGER) AS etf_count
FROM etf_holdings h
WHERE h.etf_key IN ($keys)   -- DuckDB array binding
  AND h.weight IS NOT NULL
GROUP BY h.name
ORDER BY avg_weight DESC
LIMIT 20
```

---

## DuckDB 연결 (`lib/db.ts`)

`@duckdb/node-api` 싱글턴 패턴. `next.config.ts`에서 외부 패키지 처리:

```ts
// next.config.ts
const nextConfig: NextConfig = {
  serverExternalPackages: ["@duckdb/node-api"],  // Next.js 15: experimental 밖으로 이동
};
```

DB 경로: `process.env.ETF_DB_PATH` (기본값: `../etl/db/etf_insight.duckdb`)

---

## 국가 필터 참고

`primary_country`는 스키마상 required enum (KR/US/CN/HK/JP/IN/VN/GLOBAL/MIXED/UNKNOWN).
커밋 `8598b21` 이전 생성 레코드는 NULL → UI에서 "미분류" 표시, 필터 미선택 시 전체 조회.

---

## 구현 순서

각 스텝 끝에 Playwright E2E 테스트 작성 + 통과 확인 후 git push.

---

### Step 1 — 프로젝트 초기화 ✅

**작업**
- `web/` 디렉토리, Next.js 15 + shadcn/ui 설치
- `.env.local` 작성 (`ETF_DB_PATH`)
- `next.config.ts` DuckDB external 설정
- Playwright 설치 (`npx playwright install --with-deps chromium`)
- `playwright.config.ts` 설정 (`testDir: "./tests"`)

---

### Step 2 — DuckDB 연결 + 쿼리 ✅

**작업**
- `lib/db.ts` 싱글턴 연결
- `lib/queries.ts` SQL 함수 (`getEtfList`, `getEtfDetail`, `getEtfHoldings`, `getHoldingsStats`, `getStatsSummary`, `getCountries`)

---

### Step 3 — API Route Handlers ✅

**작업**
- `app/api/etfs/route.ts` → `GET ?begin=&end=&country=&pre_listing=`
- `app/api/etfs/[etf_key]/route.ts`
- `app/api/stats/holdings/route.ts` → `GET ?begin=&end=&country=`

**E2E 테스트** (`tests/api.test.ts`) ✅ 완료

**verify**
```bash
npx playwright test tests/api.test.ts
```

**git push**
```bash
git add web/app/api/ web/tests/api.test.ts
git commit -m "feat: API route handlers (etfs + stats)"
git push
```

---

### Step 4 — ETF 목록 페이지 + filter-bar ✅

**작업**
- `app/page.tsx` 목록 렌더링 (서버 컴포넌트, DB 직접 조회)
- `components/etf-table.tsx`
- `components/filter-bar.tsx` (기간 + 국가 필터)

**E2E 테스트** (`tests/etf-list.test.ts`) ✅ 완료

**verify**
```bash
npx playwright test tests/etf-list.test.ts
```

---

### Step 5 — ETF 상세 페이지

**작업**
- `app/etfs/[etf_key]/page.tsx`
- `components/etf-detail.tsx`

**E2E 테스트** (`tests/etf-detail.test.ts`)
```ts
import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page, request }) => {
  const { etfs } = await (await request.get("/api/etfs")).json();
  await page.goto(`/etfs/${etfs[0].etf_key}`);
});

test("detail page shows fund_name", async ({ page }) => {
  await expect(page.locator("[data-testid='fund-name']")).toBeVisible();
});

test("detail page shows holdings or fallback", async ({ page }) => {
  const hasHoldings = await page.locator("[data-testid='holdings-table']").isVisible();
  const hasFallback = await page.locator("[data-testid='holdings-fallback']").isVisible();
  expect(hasHoldings || hasFallback).toBe(true);
});

test("detail page shows trend_summary section", async ({ page }) => {
  await expect(page.locator("[data-testid='trend-summary']")).toBeVisible();
});
```

**verify**
```bash
npx playwright test tests/etf-detail.test.ts
```

**git push**
```bash
git add web/app/etfs/ web/components/etf-detail.tsx web/tests/etf-detail.test.ts
git commit -m "feat: ETF detail page"
git push
```

---

### Step 6 — 통계 페이지 + holdings-chart

**작업**
- `app/stats/page.tsx`
- `components/holdings-chart.tsx` (recharts 바 차트)
- 요약 카드 (ETF 수, 구성종목 있는 ETF 수)

**E2E 테스트** (`tests/stats.test.ts`)
```ts
import { test, expect } from "@playwright/test";

test("stats page renders chart", async ({ page }) => {
  await page.goto("/stats");
  await expect(page.locator("[data-testid='holdings-chart']")).toBeVisible();
});

test("stats page shows summary cards", async ({ page }) => {
  await page.goto("/stats");
  await expect(page.locator("[data-testid='etf-count']")).toBeVisible();
  await expect(page.locator("[data-testid='holdings-etf-count']")).toBeVisible();
});

test("stats country filter updates chart", async ({ page }) => {
  await page.goto("/stats");
  const select = page.locator('[data-slot="select-trigger"]');
  await select.click();
  const options = page.locator('[data-slot="select-item"]');
  const count = await options.count();
  if (count > 1) {
    await options.nth(1).click();
    await page.getByRole("button", { name: "조회" }).click();
    await expect(page).toHaveURL(/country=/);
    await expect(page.locator("[data-testid='holdings-chart']")).toBeVisible();
  }
});
```

**verify**
```bash
npx playwright test tests/stats.test.ts
```

**git push**
```bash
git add web/app/stats/ web/components/holdings-chart.tsx web/tests/stats.test.ts
git commit -m "feat: stats page with holdings bar chart"
git push
```

---

### 전체 E2E 최종 실행

모든 스텝 완료 후:
```bash
cd web
npx playwright test
```

전체 통과 확인 후 main 브랜치 최종 push.
