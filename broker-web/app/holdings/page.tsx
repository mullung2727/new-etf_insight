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
  searchParams: Promise<{ etf?: string }>;
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

export default async function HoldingsPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const raw = params.etf;
  const etf = raw !== undefined && ETFS.some((e) => e.code === raw) ? raw : DEFAULT_ETF;

  const [latest, events] = await Promise.all([
    apiGet<DailyHolding[]>("/holdings/latest", { etf }),
    apiGet<HoldingEvent[]>("/holdings/events", { etf, days: 30 }),
  ]);

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">ETF 보유현황</h1>
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
                </TableRow>
              </TableHeader>
              <TableBody>
                {latest.map((h) => (
                  <TableRow
                    key={h.code}
                    className={cn(h.is_conviction && "bg-secondary/40", h.is_faded && "text-muted-foreground")}
                  >
                    <TableCell>
                      <div>{h.name ?? h.code}</div>
                      <div className="text-xs text-muted-foreground">{h.code}</div>
                    </TableCell>
                    <TableCell className="text-right font-mono">{formatQty(h.qty)}</TableCell>
                    <TableCell className="text-right font-mono">{formatAmtEok(h.amt_krw)}</TableCell>
                    <TableCell className="text-right font-mono">
                      {formatPct(h.weight_pct)}
                      {h.is_conviction && <Badge className="ml-2">확신</Badge>}
                    </TableCell>
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
