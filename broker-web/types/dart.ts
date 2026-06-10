export type FsDivType = "CFS" | "OFS";

export type ReprtCode = "11011" | "11012" | "11013" | "11014";

export interface FinancialItem {
  sj_div: string;
  sj_nm: string;
  account_nm: string;
  account_id: string;
  thstrm_amount: string | null;
  frmtrm_amount: string | null;
  bfefrmtrm_amount: string | null;
  currency: string;
  ord: string;
}

export interface FinancialResponse {
  status: string;
  message: string;
  list: FinancialItem[];
}

export interface CompanyInfo {
  corp_name: string;
  stock_code: string;
  corp_code: string;
  induty_code: string;
}

export interface CorpCode {
  corp_code: string;
  corp_name: string;
  stock_code: string;
}

export interface FinancialIndexItem {
  idx_cl_code: string;
  idx_cl_nm: string;
  idx_code: string;
  idx_nm: string;
  idx_val?: string;
}

export interface FinancialIndexResponse {
  status: string;
  message: string;
  list?: FinancialIndexItem[];
}

export interface CompareRow {
  key: string;
  label: string;
  type: "amount" | "ratio";
  values: (number | null)[];
}

export interface CompareResponse {
  corpName: string;
  fsDiv: FsDivType;
  periods: number[];
  rows: CompareRow[];
}
