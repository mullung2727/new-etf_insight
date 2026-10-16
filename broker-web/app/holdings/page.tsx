import Link from "next/link";
import { apiGet } from "@/lib/api-client";
import { cn } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { WeightHistoryChart } from "@/components/holdings/weight-history-chart";

interface DailyHolding {
  asof: string;
  etf: string;
  code: string;
  name: string | null;
  qty: number | null;
  amt_krw: number | null;
  weight_pct: number;
  is_conviction: boolean;
  is_faded: boolean;
  d1: number | null;
  d7: number | null;
  d30: number | null;
}

interface HoldingPoint {
  asof: string;
  weight_pct: number;
}

interface HoldingEvent {
  date: string;
  etf: string;
  code: string;
  name: string | null;
  kind: string;
  w_prev: number;
  w_cur: number;
}

interface PageProps {
  searchParams: Promise<{ etf?: string; code?: string }>;
}

const ETFS = [
  { code: "426030", label: "나스닥100" },
  { code: "385720", label: "KOSPI" },
  { code: "495060", label: "밸류업" },
  { code: "456600", label: "글로벌AI" },
] as const;

const DEFAULT_ETF = "426030";

const KIND_LABELS: Record<string, string> = {
  ENTRY: "신규편입",
  EXIT: "편출",
  UP5: "5%돌파",
  DN3: "3%이탈",
};

function formatQty(qty: number | null): string {
  if (qty === null || qty === undefined) return "-";
  return qty.toLocaleString("ko-KR");
}

function formatAmtEok(amt: number | null): string {
  if (amt === null || amt === undefined) return "-";
  return (amt / 1e8).toFixed(1);
}

function formatPct(w: number | null): string {
  if (w === null || w === undefined) return "-";
  return `${w.toFixed(2)}%`;
}

function formatDelta(v: number | null): string {
  if (v === null || v === undefined) return "-";
  if (v === 0) return "0.00";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}`;
}

function deltaClass(v: number | null): string | undefined {
  if (v === null || v === undefined || v === 0) return undefined;
  return v > 0 ? "text-status-profit" : "text-status-loss";
}

export default async function HoldingsPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const raw = params.etf;
  const etf = raw !== undefined && ETFS.some((e) => e.code === raw) ? raw : DEFAULT_ETF;
  const code = params.code;

  const [latest, events, history] = await Promise.all([
    apiGet<DailyHolding[]>("/holdings/latest", { etf }),
    apiGet<HoldingEvent[]>("/holdings/events", { etf, days: 30 }),
    code ? apiGet<HoldingPoint[]>("/holdings/history", { etf, code }) : Promise.resolve([] as HoldingPoint[]),
  ]);

  const selectedName = code ? (latest.find((h) => h.code === code)?.name ?? code) : null;

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">ETF 추적</h1>
        <p className="text-sm text-muted-foreground mt-1">TIME ETF 편입종목 보유 비중과 최근 편입·편출 이벤트를 봅니다.</p>
      </div>

      <div className="flex items-center gap-1">
        {ETFS.map(({ code, label }) => (
          <Link
            key={code}
            href={`/holdings?etf=${code}`}
            className={cn(
              "px-3 py-1.5 rounded-md text-sm transition-colors",
              code === etf
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
            )}
          >
            {code} {label}
          </Link>
        ))}
      </div>

      {code && (
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">{selectedName} 비중 추이</h2>
            <Link href={`/holdings?etf=${etf}`} className="text-sm text-muted-foreground hover:text-foreground">
              닫기
            </Link>
          </div>
          <WeightHistoryChart data={history} />
        </section>
      )}

      {latest.length > 0 ? (
        <>
          <p className="text-xs text-muted-foreground">기준일: {latest[0].asof}</p>
          <div className="rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>종목명</TableHead>
                  <TableHead className="text-right">수량</TableHead>
                  <TableHead className="text-right">평가금액(억원)</TableHead>
                  <TableHead className="text-right">비중</TableHead>
                  <TableHead className="text-right">전일</TableHead>
                  <TableHead className="text-right">1주</TableHead>
                  <TableHead className="text-right">1개월</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {latest.map((h) => (
                  <TableRow
                    key={h.code}
                    className={cn(h.is_conviction && "bg-secondary/40", h.is_faded && "text-muted-foreground")}
                  >
                    <TableCell>
                      <Link href={`/holdings?etf=${etf}&code=${encodeURIComponent(h.code)}`} className="hover:underline">
                        <div>{h.name ?? h.code}</div>
                      </Link>
                      <div className="text-xs text-muted-foreground">{h.code}</div>
                    </TableCell>
                    <TableCell className="text-right font-mono">{formatQty(h.qty)}</TableCell>
                    <TableCell className="text-right font-mono">{formatAmtEok(h.amt_krw)}</TableCell>
                    <TableCell className="text-right font-mono">
                      {formatPct(h.weight_pct)}
                      {h.is_conviction && <Badge className="ml-2">확신</Badge>}
                    </TableCell>
                    <TableCell className={cn("text-right font-mono", deltaClass(h.d1))}>{formatDelta(h.d1)}</TableCell>
                    <TableCell className={cn("text-right font-mono", deltaClass(h.d7))}>{formatDelta(h.d7)}</TableCell>
                    <TableCell className={cn("text-right font-mono", deltaClass(h.d30))}>{formatDelta(h.d30)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">보유 데이터 없음</p>
      )}

      <h2 className="text-lg font-semibold">최근 이벤트 (30일)</h2>
      {events.length > 0 ? (
        <div className="rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>날짜</TableHead>
                <TableHead>종류</TableHead>
                <TableHead>종목명</TableHead>
                <TableHead className="text-right">비중 변화</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.map((e, i) => (
                <TableRow key={`${e.date}-${e.code}-${e.kind}-${i}`}>
                  <TableCell className="font-mono text-sm">{e.date}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{KIND_LABELS[e.kind] ?? e.kind}</Badge>
                  </TableCell>
                  <TableCell>{e.name ?? e.code}</TableCell>
                  <TableCell className="text-right font-mono">
                    {formatPct(e.w_prev)} → {formatPct(e.w_cur)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">최근 30일 이벤트 없음</p>
      )}
    </div>
  );
}
