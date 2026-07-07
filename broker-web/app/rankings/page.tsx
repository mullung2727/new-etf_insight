import {
  getRankings, getRankingMetrics, getRankingPeriods,
  type MetricInfo, type PeriodInfo,
} from "@/lib/queries";
import { RankingControls } from "@/components/rankings/RankingControls";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

interface PageProps {
  searchParams: Promise<{
    metric?: string; year?: string; reprt?: string;
    exclude?: string; order?: string;
  }>;
}

/** 금액(원) → 조/억 축약. 비율은 그대로 % */
function formatValue(v: number | null, unit: string): string {
  if (v == null) return "-";
  if (unit === "won") {
    const abs = Math.abs(v);
    if (abs >= 1e12) return `${(v / 1e12).toFixed(2)}조`;
    if (abs >= 1e8) return `${Math.round(v / 1e8).toLocaleString()}억`;
    return v.toLocaleString();
  }
  return `${v.toFixed(2)}%`;
}

export default async function RankingsPage({ searchParams }: PageProps) {
  const sp = await searchParams;
  const [metrics, periods] = await Promise.all([getRankingMetrics(), getRankingPeriods()]);

  const metric: MetricInfo =
    metrics.find((m) => m.key === sp.metric) ?? metrics[0];
  const period: PeriodInfo =
    periods.find((p) => p.year === sp.year && p.reprt === sp.reprt) ?? periods[0];
  const excludeImpaired = sp.exclude !== "0"; // 기본 제외
  const order = sp.order === "asc" || sp.order === "desc" ? sp.order : metric?.default_order;

  const rows =
    metric && period
      ? await getRankings({
          metric: metric.key, year: period.year, reprt: period.reprt,
          excludeImpaired, order, limit: 100,
        })
      : [];

  return (
    <div className="fin-scope min-h-screen bg-background font-terminal">
      <main className="max-w-3xl mx-auto py-10 px-4">
        <h1 className="text-2xl font-bold mb-2 text-primary tracking-[0.05em]">
          재무지표 랭킹
        </h1>
        <p className="text-sm text-muted-foreground mb-6">
          전 상장사 · DART 재무지표/주요계정 · 상위 100
        </p>

        <RankingControls
          metrics={metrics}
          periods={periods}
          current={{
            metric: metric?.key ?? "",
            year: period?.year ?? "",
            reprt: period?.reprt ?? "",
            exclude: excludeImpaired,
            order: order ?? "desc",
          }}
        />

        <Table className="mt-6">
          <TableHeader>
            <TableRow>
              <TableHead className="w-12 text-right">#</TableHead>
              <TableHead>종목</TableHead>
              <TableHead className="w-24 font-mono">코드</TableHead>
              <TableHead className="text-right">{metric?.label ?? "값"}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={`${r.rank}-${r.stock_code}`}>
                <TableCell className="text-right text-muted-foreground">{r.rank}</TableCell>
                <TableCell>{r.corp_name ?? "-"}</TableCell>
                <TableCell className="font-mono text-sm text-muted-foreground">{r.stock_code}</TableCell>
                <TableCell className="text-right font-mono tabular-nums">
                  {formatValue(r.value, metric?.unit ?? "pct")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {rows.length === 0 && (
          <p className="text-sm text-muted-foreground mt-6">데이터 없음.</p>
        )}
      </main>
    </div>
  );
}
