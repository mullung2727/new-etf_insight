import { query } from "./db";

function parseJsonArray<T>(value: unknown, fallback: T[] = []): T[] {
  if (typeof value !== "string") return fallback;
  try { return JSON.parse(value); } catch { return fallback; }
}

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

async function getEtfRecordColumns(): Promise<Set<string>> {
  const rows = await query<{ name: string }>("PRAGMA table_info('etf_records')");
  return new Set(rows.map((row) => row.name));
}

export async function getEtfList(params: {
  begin?: string;
  end?: string;
  country?: string;
  themeStatus?: string;
  themeBucket?: string;
  preListingOnly?: boolean;
}): Promise<EtfListItem[]> {
  const columns = await getEtfRecordColumns();
  const hasThemeColumns = columns.has("theme_status") && columns.has("theme_bucket");
  const themeSelect = hasThemeColumns
    ? "theme_status, theme_bucket, structure_tags, classification_confidence,"
    : "NULL AS theme_status, NULL AS theme_bucket, '[]' AS structure_tags, NULL AS classification_confidence,";
  const themeWhere = hasThemeColumns
    ? `AND ($theme_status IS NULL OR theme_status = $theme_status)
       AND ($theme_bucket IS NULL OR theme_bucket = $theme_bucket)`
    : "";
  const queryParams: Record<string, string | boolean | null | undefined> = {
    begin: params.begin ?? null,
    end: params.end ?? null,
    country: params.country ?? null,
    pre_listing: params.preListingOnly ?? null,
  };
  if (hasThemeColumns) {
    queryParams.theme_status = params.themeStatus || null;
    queryParams.theme_bucket = params.themeBucket || null;
  }
  const rows = await query<EtfListItem>(
    `SELECT etf_key, fund_name, asset_manager, index_name,
            primary_country, ${themeSelect} first_rcept_dt, is_pre_listing_etf, revision_count,
            EXISTS(SELECT 1 FROM etf_holdings h WHERE h.etf_key = r.etf_key) AS has_holdings
     FROM etf_records r
     WHERE ($begin IS NULL OR first_rcept_dt >= $begin)
       AND first_rcept_dt <= COALESCE($end, STRFTIME('%Y%m%d', CURRENT_DATE))
       AND ($country IS NULL OR primary_country = $country)
       ${themeWhere}
       AND ($pre_listing IS NULL OR is_pre_listing_etf = $pre_listing)
     ORDER BY first_rcept_dt DESC`,
    queryParams
  );
  return rows.map((row) => {
    row.structure_tags = parseJsonArray(row.structure_tags);
    return row;
  });
}

export async function getEtfDetail(etfKey: string): Promise<EtfDetail | null> {
  const rows = await query<EtfDetail>(
    "SELECT * FROM etf_records WHERE etf_key = $etf_key",
    { etf_key: etfKey }
  );
  const row = rows[0] ?? null;
  if (!row) return null;
  row.theme_status ??= null;
  row.theme_bucket ??= null;
  row.structure_tags ??= [];
  row.classification_confidence ??= null;
  row.classification_evidence ??= null;
  row.keywords = parseJsonArray(row.keywords);
  row.structure_tags = parseJsonArray(row.structure_tags);
  row.missing_info = parseJsonArray(row.missing_info);
  return row;
}

export async function getEtfHoldings(etfKey: string): Promise<Holding[]> {
  return query<Holding>(
    "SELECT name, ticker, exchange, weight FROM etf_holdings WHERE etf_key = $etf_key ORDER BY seq",
    { etf_key: etfKey }
  );
}

export async function getHoldingsStats(params: {
  begin?: string;
  end?: string;
  country?: string;
}): Promise<HoldingStat[]> {
  return query<HoldingStat>(
    `SELECT
       h.name,
       ROUND(AVG(TRY_CAST(REPLACE(REPLACE(h.weight, '%', ''), ' ', '') AS DOUBLE)), 2) AS avg_weight,
       CAST(COUNT(DISTINCT h.etf_key) AS INTEGER) AS etf_count
     FROM etf_holdings h
     JOIN etf_records r ON h.etf_key = r.etf_key
     WHERE r.is_pre_listing_etf = true
       AND ($begin IS NULL OR r.first_rcept_dt >= $begin)
       AND r.first_rcept_dt <= COALESCE($end, STRFTIME('%Y%m%d', CURRENT_DATE))
       AND ($country IS NULL OR r.primary_country = $country)
       AND h.weight IS NOT NULL
     GROUP BY h.name
     ORDER BY avg_weight DESC
     LIMIT 20`,
    {
      begin: params.begin ?? null,
      end: params.end ?? null,
      country: params.country ?? null,
    }
  );
}

export async function getStatsSummary(params: {
  begin?: string;
  end?: string;
  country?: string;
}): Promise<StatsSummary> {
  const rows = await query<StatsSummary>(
    `SELECT
       CAST(COUNT(DISTINCT r.etf_key) AS INTEGER)  AS total_etfs,
       CAST(COUNT(DISTINCT h.etf_key) AS INTEGER) AS with_any_holdings
     FROM etf_records r
     LEFT JOIN etf_holdings h ON r.etf_key = h.etf_key
     WHERE r.is_pre_listing_etf = true
       AND ($begin IS NULL OR r.first_rcept_dt >= $begin)
       AND r.first_rcept_dt <= COALESCE($end, STRFTIME('%Y%m%d', CURRENT_DATE))
       AND ($country IS NULL OR r.primary_country = $country)`,
    {
      begin: params.begin ?? null,
      end: params.end ?? null,
      country: params.country ?? null,
    }
  );
  return rows[0] ?? { total_etfs: 0, with_any_holdings: 0 };
}

export async function getHoldingsStatsByKeys(keys: string[]): Promise<HoldingStat[]> {
  if (keys.length === 0) return [];
  const ph = keys.map((_, i) => `$k${i}`).join(", ");
  const params = Object.fromEntries(keys.map((k, i) => [`k${i}`, k]));
  return query<HoldingStat>(
    `SELECT
       h.name,
       ROUND(AVG(TRY_CAST(REPLACE(REPLACE(h.weight, '%', ''), ' ', '') AS DOUBLE)), 2) AS avg_weight,
       CAST(COUNT(DISTINCT h.etf_key) AS INTEGER) AS etf_count
     FROM etf_holdings h
     WHERE h.etf_key IN (${ph})
       AND h.weight IS NOT NULL
     GROUP BY h.name
     ORDER BY avg_weight DESC
     LIMIT 20`,
    params
  );
}

export async function getStatsSummaryByKeys(keys: string[]): Promise<StatsSummary> {
  if (keys.length === 0) return { total_etfs: 0, with_any_holdings: 0 };
  const ph = keys.map((_, i) => `$k${i}`).join(", ");
  const params = Object.fromEntries(keys.map((k, i) => [`k${i}`, k]));
  const rows = await query<StatsSummary>(
    `SELECT
       CAST(COUNT(DISTINCT r.etf_key) AS INTEGER) AS total_etfs,
       CAST(COUNT(DISTINCT h.etf_key) AS INTEGER) AS with_any_holdings
     FROM etf_records r
     LEFT JOIN etf_holdings h ON r.etf_key = h.etf_key
     WHERE r.etf_key IN (${ph})`,
    params
  );
  return rows[0] ?? { total_etfs: 0, with_any_holdings: 0 };
}

export async function getCountries(): Promise<string[]> {
  const rows = await query<{ primary_country: string }>(
    "SELECT DISTINCT primary_country FROM etf_records WHERE primary_country IS NOT NULL ORDER BY primary_country"
  );
  return rows.map((r) => r.primary_country);
}
