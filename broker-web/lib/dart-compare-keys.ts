/**
 * DART API 실호출(2026-06-10)로 확정된 키 매핑
 * 검증: bs-topn.spec + acnt-*-2025-cfs.json fixture
 */

/** fnlttSinglAcntAll에서 추출할 고정 계정
 *  account_id가 여러 sj_div에 중복되는 경우 sj_div 필터 필수
 *  자본 3개는 6개사(삼성전자·신한지주·현대차·카카오·M83·삼성생명) fixture로
 *  전 종목 존재 확인 (2026-06-10) */
export const AMOUNT_ACCOUNTS = [
  { key: "revenue",        label: "매출액",     account_id: "ifrs-full_Revenue",                 sj_div: "IS" },
  { key: "opProfit",       label: "영업이익",    account_id: "dart_OperatingIncomeLoss",          sj_div: "IS" },
  { key: "netIncome",      label: "당기순이익",  account_id: "ifrs-full_ProfitLoss",              sj_div: "IS" },
  { key: "totalAssets",    label: "자산총계",    account_id: "ifrs-full_Assets",                  sj_div: "BS" },
  { key: "totalLiab",      label: "부채총계",    account_id: "ifrs-full_Liabilities",             sj_div: "BS" },
  { key: "totalEquity",    label: "자본총계",    account_id: "ifrs-full_Equity",                  sj_div: "BS" },
  { key: "equityCapital",  label: "자본금",     account_id: "ifrs-full_IssuedCapital",           sj_div: "BS" },
  { key: "equityRetained", label: "이익잉여금",  account_id: "ifrs-full_RetainedEarnings",        sj_div: "BS" },
  { key: "equityNci",      label: "비지배지분",  account_id: "ifrs-full_NoncontrollingInterests", sj_div: "BS" },
] as const;

export type AmountKey = typeof AMOUNT_ACCOUNTS[number]["key"];

/** fnlttSinglIndx에서 추출할 3개 지표 (비율 행 4개 중 3개는 Indx, 1개는 금액 계산)
 *  영업이익률 = opProfit / revenue × 100 — DART M210000에 직접 없어 금액에서 계산 */
export const RATIO_INDICES = [
  {
    key:         "roe",
    label:       "ROE",
    idx_cl_code: "M210000",
    idx_code:    "M211550",
  },
  {
    key:         "debtRatio",
    label:       "부채비율",
    idx_cl_code: "M220000",
    idx_code:    "M221100",
  },
  {
    key:         "revenueGrowth",
    label:       "매출증가율",
    idx_cl_code: "M230000",
    idx_code:    "M231000",
  },
] as const;

export type RatioKey = typeof RATIO_INDICES[number]["key"] | "opMargin";

/** 비율 표시 순서 (영업이익률은 금액 계산이라 별도 항목) */
export const RATIO_ROW_ORDER: { key: RatioKey; label: string }[] = [
  { key: "opMargin",      label: "영업이익률" },
  { key: "roe",           label: "ROE" },
  { key: "debtRatio",     label: "부채비율" },
  { key: "revenueGrowth", label: "매출증가율" },
];

/** 테스트 기준 종목 */
export const TEST_CORPS = {
  samsung: { corp_code: "00126380", stock_code: "005930", name: "삼성전자" },
  m83:     { corp_code: "01704680", stock_code: "476080", name: "M83" },
} as const;
