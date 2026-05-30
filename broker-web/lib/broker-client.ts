const BASE = process.env.NEXT_PUBLIC_BROKER_API_URL ?? "http://localhost:8001";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `POST ${path} → ${res.status}`);
  }
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE ${path} → ${res.status}`);
  return res.json();
}

// --- Types ---

export interface Quote {
  symbol: string;
  price: number | null;
  raw: Record<string, unknown>;
}

export interface Holding {
  stk_cd?: string;
  stk_nm?: string;
  cur_prc?: string;
  evlt_pl?: string;
  prft_rt?: string;
  [key: string]: unknown;
}

export interface BalanceData {
  prsm_dpst_aset_amt?: string;
  tot_evlt_amt?: string;
  tot_evlt_pl?: string;
  tot_prft_rt?: string;
  acnt_evlt_remn_indv_tot?: Holding[];
  [key: string]: unknown;
}

export interface ConditionItem {
  seq: string;
  name: string;
}

export interface OrderRequest {
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  order_type: "limit" | "market";
}

export interface OrderResult {
  accepted: boolean;
  order_no: string | null;
  message: string;
}

// --- API calls ---

export const brokerClient = {
  getQuote: (symbol: string) => get<Quote>(`/quotes/${symbol}`),
  getOrderbook: (symbol: string) => get<Record<string, unknown>>(`/quotes/${symbol}/orderbook`),
  getBalance: () => get<BalanceData>("/account/balance"),
  getDeposit: () => get<Record<string, unknown>>("/account/deposit"),
  listConditions: () => get<ConditionItem[]>("/conditions"),
  runCondition: (seq: string) => get<Record<string, unknown>>(`/conditions/${seq}/run`),
  placeOrder: (req: OrderRequest) => post<OrderResult>("/orders", req),
  cancelOrder: (orderNo: string, symbol: string, qty = 0) =>
    del<OrderResult>(`/orders/${orderNo}?symbol=${symbol}&qty=${qty}`),
};
