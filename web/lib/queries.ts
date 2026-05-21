import { query } from "./db";

export interface EtfListItem {
  etf_key: string;
  fund_name: string | null;
  asset_manager: string | null;
  index_name: string | null;
  primary_country: string | null;
  first_rcept_dt: string | null;
  is_pre_listing_etf: boolean | null;
  revision_count: number | null;
}

export interface EtfDetail extends EtfListItem {
  route: string | null;
  index_provider: string | null;
  index_description: string | null;
  holdings_available_in_pdf: boolean | null;
  holdings_summary: string | null;
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

export async function getEtfList(params: {
  begin?: string;
  end?: string;
  country?: string;
  preListingOnly?: boolean;
}): Promise<EtfListItem[]> {
  return query<EtfListItem>(
    `SELECT etf_key, fund_name, asset_manager, index_name,
            primary_country, first_rcept_dt, is_pre_listing_etf, revision_count
     FROM etf_records
     WHERE ($begin IS NULL OR first_rcept_dt >= $begin)
       AND first_rcept_dt <= COALESCE($end, STRFTIME('%Y%m%d', CURRENT_DATE))
       AND ($country IS NULL OR primary_country = $country)
       AND ($pre_listing IS NULL OR is_pre_listing_etf = $pre_listing)
     ORDER BY first_rcept_dt DESC`,
    {
      begin: params.begin ?? null,
      end: params.end ?? null,
      country: params.country ?? null,
      pre_listing: params.preListingOnly ?? null,
    }
  );
}

export async function getEtfDetail(etfKey: string): Promise<EtfDetail | null> {
  const rows = await query<EtfDetail>(
    "SELECT * FROM etf_records WHERE etf_key = $etf_key",
    { etf_key: etfKey }
  );
  const row = rows[0] ?? null;
  if (!row) return null;
  // JSON columns come back as strings — parse them
  if (typeof row.keywords === "string") {
    try { row.keywords = JSON.parse(row.keywords); } catch { row.keywords = []; }
  }
  if (typeof row.missing_info === "string") {
    try { row.missing_info = JSON.parse(row.missing_info); } catch { row.missing_info = []; }
  }
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

export async function getCountries(): Promise<string[]> {
  const rows = await query<{ primary_country: string }>(
    "SELECT DISTINCT primary_country FROM etf_records WHERE primary_country IS NOT NULL ORDER BY primary_country"
  );
  return rows.map((r) => r.primary_country);
}
