import Link from "next/link";
import {
  getDaily, getSummaries, twr, twrIndex, STRATEGIES,
  type DailyRow, type StrategySummary,
} from "@/lib/performance-db";
import { TwrChart } from "@/components/performance/twr-chart";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface PageProps {
  searchParams: Promise<{ s?: string }>;
}

function fmtDate(yyyymmdd: string | null): string {
  if (!yyyymmdd) return "–";
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

function fmtMoney(v: number | null, currency: "KRW" | "USD"): string {
  if (v === null) return "–";
  const rounded = Math.round(v);
  return currency === "KRW"
    ? `${rounded.toLocaleString("ko-KR")}원`
    : `$${rounded.toLocaleString("en-US")}`;
}

function fmtPct(v: number | null): string {
  if (v === null) return "–";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
}

function twrColor(v: number | null): string {
  if (v === null || v === 0) return "";
  return v > 0 ? "text-up" : "text-down";
}

export default async function PerformancePage({ searchParams }: PageProps) {
  const params = await searchParams;
  const keys = STRATEGIES.map((s) => s.key);
  const selectedKey = params.s && keys.includes(params.s) ? params.s : STRATEGIES[0].key;
  const selectedMeta = STRATEGIES.find((s) => s.key === selectedKey)!;

  let summaries: StrategySummary[] | null = null;
  let rows: DailyRow[] | null = null;
  let error: string | null = null;
  try {
    summaries = getSummaries();
    rows = getDaily(selectedKey);
  } catch (err) {
    error = err instanceof Error ? err.message : String(err);
  }

  if (error || !summaries || !rows) {
    return (
      <div className="mx-auto w-full max-w-6xl px-4 py-8 space-y-4">
        <h1 className="text-2xl font-semibold">전략 성과 (실매매)</h1>
        <p className="text-sm text-muted-foreground">매일 11:30 전날분 기준 · 수익률은 입출금 제외 시간가중(TWR) · MDD 는 일별 TWR 기준</p>
        <p className="text-sm text-destructive">{error ?? "데이터를 불러오지 못했습니다."}</p>
      </div>
    );
  }

  const idx = twrIndex(rows);
  const daily = twr(rows);
  const chartData = rows.map((r, i) => ({
    date: r.date,
    twrPct: (idx[i] - 1) * 100,
    flowNote: r.flow_note || null,
  }));
  const recent = rows.map((r, i) => ({ ...r, dayTwr: daily[i] })).slice(-20).reverse();

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">전략 성과 (실매매)</h1>
        <p className="text-sm text-muted-foreground">매일 11:30 전날분 기준 · 수익률은 입출금 제외 시간가중(TWR) · MDD 는 일별 TWR 기준</p>
      </div>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
        {summaries.map((s) => (
          <Link key={s.key} href={`/performance?s=${s.key}`}>
            <Card className={cn(s.key === selectedKey && "ring-2 ring-primary")}>
              <CardHeader><CardTitle className="text-sm font-medium">{s.label}</CardTitle></CardHeader>
              <CardContent className="space-y-1 text-sm">
                <p className="text-xs text-muted-foreground">기준일 {fmtDate(s.lastDate)}</p>
                <p>시드 {fmtMoney(s.seed, s.currency)}</p>
                <p>평가액 {fmtMoney(s.equity, s.currency)}</p>
                <p className={twrColor(s.cumTwr)}>누적 TWR {fmtPct(s.cumTwr)}</p>
                <p>일별 MDD {fmtPct(s.mdd)}</p>
                <p className="text-xs text-muted-foreground">거래 {s.tradesCum ?? "–"}회 · 보유 {s.openN ?? "–"}종목</p>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
      <Card>
        <CardHeader><CardTitle className="text-base">{selectedMeta.label} 누적 TWR</CardTitle></CardHeader>
        <CardContent><TwrChart data={chartData} /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle className="text-base">최근 20일</CardTitle></CardHeader>
        <CardContent>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-1 pr-4 font-medium">날짜</th>
                <th className="py-1 pr-4 font-medium text-right">평가액</th>
                <th className="py-1 pr-4 font-medium text-right">그날 TWR</th>
                <th className="py-1 font-medium text-right">입출금</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((r) => (
                <tr key={r.date} className="border-t border-border">
                  <td className="py-1 pr-4">{fmtDate(r.date)}</td>
                  <td className="py-1 pr-4 text-right">{fmtMoney(r.equity, selectedMeta.currency)}</td>
                  <td className={cn("py-1 pr-4 text-right", twrColor(r.dayTwr))}>{fmtPct(r.dayTwr)}</td>
                  <td className="py-1 text-right">
                    {r.flow ? fmtMoney(r.flow, selectedMeta.currency) : "–"}
                    {r.flow_note ? <span className="text-muted-foreground"> ({r.flow_note})</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
