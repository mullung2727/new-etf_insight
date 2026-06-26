import { brokerBase } from "./broker-base";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${brokerBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${brokerBase()}${path}`, {
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

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${brokerBase()}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `PATCH ${path} → ${res.status}`);
  }
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${brokerBase()}${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE ${path} → ${res.status}`);
  return res.json();
}

async function del204(path: string): Promise<void> {
  const res = await fetch(`${brokerBase()}${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE ${path} → ${res.status}`);
}

// --- Types ---

export interface Quote {
  symbol: string;
  price: number | null;
  raw: Record<string, unknown>;
}

export interface DailyCandle {
  dt: string;
  open_pric: string;
  high_pric: string;
  low_pric: string;
  cur_prc: string;
  trde_qty: string;
  pred_pre: string;
  [key: string]: unknown;
}

export interface Holding {
  stk_cd?: string;
  stk_nm?: string;
  cur_prc?: string;
  pur_pric?: string;
  rmnd_qty?: string;
  evltv_prft?: string;
  prft_rt?: string;
  pur_amt?: string;
  evlt_amt?: string;
  poss_rt?: string;
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

export interface DepositData {
  entr?: string;
  ord_alow_amt?: string;
  [key: string]: unknown;
}

export interface Settings {
  env: "paper" | "real";
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

// --- Notes types ---

export type NoteStatus = "open" | "partial" | "closed";
export type EventType = "buy" | "add_buy" | "partial_sell" | "sell";

export interface Note {
  uid: string;
  symbol: string;
  status: NoteStatus;
  target_price: number | null;
  holding_period: string | null;
  buy_reason: string | null;
  memo: string | null;
  user_id: string;
  created_at: string;
  updated_at: string;
}

export interface NoteEvent {
  id: number;
  note_uid: string;
  event_type: EventType;
  price: number;
  qty: number;
  executed_at: string;
  memo: string | null;
  created_at: string;
}

export interface NoteDetail extends Note {
  events: NoteEvent[];
}

export interface NoteCreate {
  symbol: string;
  target_price?: number | null;
  holding_period?: string | null;
  buy_reason?: string | null;
  memo?: string | null;
  user_id?: string;
}

export interface NoteUpdate {
  status?: NoteStatus;
  target_price?: number | null;
  holding_period?: string | null;
  buy_reason?: string | null;
  memo?: string | null;
}

export interface EventCreate {
  event_type: EventType;
  price: number;
  qty: number;
  executed_at: string;
  memo?: string | null;
}

// --- Close-bet types ---

export interface CloseBetBuy {
  date: string;
  ticker: string;
  score: number | null;
  cntr_price: number | null;
  status: string;
  order_no: string;
  created_at: string | null;
}

export interface CloseBetWatch {
  date: string;
  ticker: string;
  score: number | null;
  cntr_price: number | null;
  qty: number;
}

export interface CloseBetHistory {
  date: string;
  ticker: string;
  cntr_price: number | null;
  sell_price: number | null;
  sell_qty: number | null;
  pnl_pct: number | null;
  sold_at: string | null;
}

export interface CloseBetPositions {
  buys: CloseBetBuy[];
  watching: CloseBetWatch[];
  history: CloseBetHistory[];
}

// --- API calls ---

export const brokerClient = {
  getCloseBetPositions: () =>
    get<CloseBetPositions>("/close-bet/positions"),
  getQuote: (symbol: string) => get<Quote>(`/quotes/${symbol}`),
  getOrderbook: (symbol: string) => get<Record<string, unknown>>(`/quotes/${symbol}/orderbook`),
  getDailyChart: (symbol: string, baseDt?: string) =>
    get<DailyCandle[]>(`/quotes/${symbol}/daily${baseDt ? `?base_dt=${baseDt}` : ""}`),
  getBalance: () => get<BalanceData>("/account/balance"),
  getDeposit: () => get<DepositData>("/account/deposit"),
  listConditions: () => get<ConditionItem[]>("/conditions"),
  runCondition: (seq: string) => get<Record<string, unknown>>(`/conditions/${seq}/run`),
  placeOrder: (req: OrderRequest) => post<OrderResult>("/orders", req),
  cancelOrder: (orderNo: string, symbol: string, qty = 0) =>
    del<OrderResult>(`/orders/${orderNo}?symbol=${symbol}&qty=${qty}`),

  // settings
  getSettings: () => get<Settings>("/settings"),
  updateSettings: (env: "paper" | "real") => post<Settings>("/settings", { env }),

  // notes
  listNotes: (params?: { symbol?: string; status?: NoteStatus }) => {
    const q = new URLSearchParams();
    if (params?.symbol) q.set("symbol", params.symbol);
    if (params?.status) q.set("status", params.status);
    const qs = q.toString();
    return get<Note[]>(`/notes${qs ? `?${qs}` : ""}`);
  },
  getNote: (uid: string) => get<NoteDetail>(`/notes/${uid}`),
  createNote: (req: NoteCreate) => post<Note>("/notes", req),
  updateNote: (uid: string, req: NoteUpdate) => patch<NoteDetail>(`/notes/${uid}`, req),
  deleteNote: (uid: string) => del204(`/notes/${uid}`),
  addNoteEvent: (uid: string, req: EventCreate) =>
    post<NoteEvent>(`/notes/${uid}/events`, req),
  deleteNoteEvent: (uid: string, eventId: number) =>
    del204(`/notes/${uid}/events/${eventId}`),
};
