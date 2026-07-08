import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RankingRow } from "@/lib/queries";

export function RoeCard({ rows, periodLabel }: { rows: RankingRow[]; periodLabel: string | null }) {
  return (
    <Card className="bg-card border-border h-full">
      <CardHeader className="flex-row items-center justify-between pb-2">
        <CardTitle className="text-base">🏆 ROE Top5</CardTitle>
        <Link
          href="/rankings"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          {periodLabel ? `${periodLabel} · ` : ""}전체 →
        </Link>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">랭킹 데이터가 없습니다.</p>
        ) : (
          <ol className="flex flex-col gap-1.5">
            {rows.map((r) => (
              <li key={r.stock_code ?? r.rank} className="flex items-center gap-2 text-sm">
                <span className="w-5 text-right tabular-nums text-muted-foreground">{r.rank}</span>
                <Link
                  href={r.stock_code ? `/watchlist/${r.stock_code}` : "#"}
                  className="flex-1 truncate hover:text-primary transition-colors"
                >
                  {r.corp_name ?? "-"}
                </Link>
                <span className="tabular-nums font-medium text-primary">
                  {r.value != null ? `${r.value.toFixed(2)}%` : "-"}
                </span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
