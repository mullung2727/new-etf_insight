import Link from "next/link";
import { getHoldingsStatsByKeys, getStatsSummaryByKeys } from "@/lib/queries";
import { HoldingsBarChart, HoldingsPieChart } from "@/components/etf/holdings-chart";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface PageProps {
  searchParams: Promise<{ keys?: string }>;
}

export default async function StatsPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const keys = params.keys ? params.keys.split(",").map((k) => k.trim()).filter(Boolean) : [];

  const backLink = (
    <Link href="/etfs" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "-ml-2")}>
      ← 목록으로
    </Link>
  );

  if (keys.length === 0) {
    return (
      <div className="mx-auto w-full max-w-4xl px-4 py-8 space-y-4">
        {backLink}
        <p className="text-sm text-muted-foreground">목록에서 ETF를 선택한 후 통계 보기를 눌러주세요.</p>
      </div>
    );
  }

  const [stats, summary] = await Promise.all([getHoldingsStatsByKeys(keys), getStatsSummaryByKeys(keys)]);

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-8 space-y-6">
      {backLink}
      <h1 className="text-2xl font-semibold">구성종목 비중 통계</h1>
      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">선택 ETF</CardTitle></CardHeader>
          <CardContent><p data-testid="etf-count" className="text-3xl font-bold">{summary.total_etfs}개</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium text-muted-foreground">구성종목 보유</CardTitle></CardHeader>
          <CardContent><p data-testid="holdings-etf-count" className="text-3xl font-bold">{summary.with_any_holdings}개</p></CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader><CardTitle className="text-base">구성종목 비중 Top 20 기준 (3% 미만 → 기타)</CardTitle></CardHeader>
        <CardContent><HoldingsPieChart data={stats} /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle className="text-base">구성종목별 평균 비중 Top 20</CardTitle></CardHeader>
        <CardContent><div data-testid="holdings-chart"><HoldingsBarChart data={stats} /></div></CardContent>
      </Card>
    </div>
  );
}
