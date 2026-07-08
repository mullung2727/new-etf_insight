import { apiGet, apiGetOrNull } from "./api-client";

export interface EtfListItem {
  etf_key: string;
  fund_name: string | null;
  asset_manager: string | null;
  index_name: string | null;
  primary_country: string | null;
  theme_status: string | null;
  theme_bucket: string | null;
  structure_tags: string[] | null;
  classification_confidence: number | null;
  first_rcept_dt: string | null;
  is_pre_listing_etf: boolean | null;
  revision_count: number | null;
  has_holdings: boolean;
}

export interface EtfDetail extends EtfListItem {
  route: string | null;
  index_provider: string | null;
  index_description: string | null;
  holdings_available_in_pdf: boolean | null;
  holdings_summary: string | null;
  classification_evidence: string | null;
  keywords: string[] | null;
  trend_summary: string | null;
  missing_info: string[] | null;
  rcept_no: string | null;
  rcept_dt: string | null;
  corp_code: string | null;
  corp_name: string | null;
  report_nm: string | null;
  fund_code: string | null;
  pdf_path: string | null;
}

export interface Holding {
  name: string;
  ticker: string | null;
  exchange: string | null;
  weight: string | null;
}

export interface HoldingStat {
  name: string;
  avg_weight: number;
  etf_count: number;
}

export interface StatsSummary {
  total_etfs: number;
  with_any_holdings: number;
}

export const getEtfList = (params: {
  begin?: string; end?: string; country?: string;
  themeStatus?: string; themeBucket?: string; preListingOnly?: boolean;
}) =>
  apiGet<EtfListItem[]>("/etfs", {
    begin: params.begin, end: params.end, country: params.country,
    theme_status: params.themeStatus, theme_bucket: params.themeBucket,
    pre_listing_only: params.preListingOnly,
  });

export const getEtfDetail = (etfKey: string) =>
  apiGetOrNull<EtfDetail>(`/etfs/${encodeURIComponent(etfKey)}`);

export const getEtfHoldings = (etfKey: string) =>
  apiGet<Holding[]>(`/etfs/${encodeURIComponent(etfKey)}/holdings`);

export const getHoldingsStatsByKeys = (keys: string[]) =>
  keys.length === 0 ? Promise.resolve([]) :
  apiGet<HoldingStat[]>("/stats/holdings", { etf_keys: keys });

export const getStatsSummaryByKeys = (keys: string[]) =>
  keys.length === 0 ? Promise.resolve({ total_etfs: 0, with_any_holdings: 0 }) :
  apiGet<StatsSummary>("/stats/summary", { etf_keys: keys });

export const getCountries = () => apiGet<string[]>("/countries");

// ── 재무지표 랭킹 (financial_indicators) ──────────────────────────────────────
export interface MetricInfo {
  key: string;
  label: string;
  unit: "pct" | "won";
  source: "indicators" | "accounts";
  default_order: "asc" | "desc";
}
export interface PeriodInfo {
  year: string;
  reprt: string;
  label: string;
}
export interface RankingRow {
  rank: number;
  stock_code: string | null;
  corp_name: string | null;
  value: number | null;
}

export interface DigestItem {
  ticker: string;
  name: string;
  channels: number;
  change_type: string | null;
  change_summary: string | null;
  themes: string[];
}
export interface DigestLatest {
  date: string;
  session: string;
  count: number;
  items: DigestItem[];
}
export const getLatestDigest = () => apiGetOrNull<DigestLatest>("/telegram/digest/latest");

export interface TelegramMention {
  date_kst: string;
  session: string;
  channels: string[];
  post_refs: string[];
  change_type: string | null;
  change_summary: string | null;
  themes: string[];
}
export const getTelegramMentions = (
  ticker: string,
  params?: { from?: string; to?: string; session?: string },
) => apiGet<TelegramMention[]>(`/telegram/mentions/${ticker}`, params);

export interface ThemePeer {
  ticker: string;
  name: string;
  themes: string[];
}
export const getThemePeers = (ticker: string) =>
  apiGet<ThemePeer[]>(`/telegram/theme-peers/${ticker}`);

export const getRankingMetrics = () => apiGet<MetricInfo[]>("/rankings/metrics");
export const getRankingPeriods = () => apiGet<PeriodInfo[]>("/rankings/periods");
export const getRankings = (params: {
  metric: string; year: string; reprt: string;
  excludeImpaired: boolean; order?: string; limit?: number;
}) =>
  apiGet<RankingRow[]>("/rankings", {
    metric: params.metric, year: params.year, reprt: params.reprt,
    exclude_impaired: params.excludeImpaired, order: params.order, limit: params.limit,
  });
