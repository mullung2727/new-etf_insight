import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDate } from "@/lib/formatters";

export interface WatchlistCardItem {
  code: string;
  name: string;
}

export function WatchlistCard({
  date,
  items,
}: {
  date: string | null;
  items: WatchlistCardItem[];
}) {
  return (
    <Card className="bg-card border-border h-full">
      <CardHeader className="flex-row items-center justify-between pb-2">
        <CardTitle className="text-base">🆕 워치리스트</CardTitle>
        <Link href="/watchlist" className="text-xs text-muted-foreground hover:text-foreground">
          전체 →
        </Link>
      </CardHeader>
      <CardContent>
        {!date || items.length === 0 ? (
          <p className="text-sm text-muted-foreground">최근 신규진입 종목이 없습니다.</p>
        ) : (
          <>
            <p className="text-xs text-muted-foreground mb-2">{formatDate(date)} 신규진입</p>
            <ul className="flex flex-wrap gap-2">
              {items.map((it) => (
                <li key={it.code}>
                  <Link
                    href={`/watchlist/${it.code}`}
                    className="inline-flex items-center gap-1.5 rounded-md bg-secondary px-2.5 py-1 text-sm hover:bg-secondary/70 transition-colors"
                  >
                    <span>{it.name}</span>
                    <span className="text-xs text-muted-foreground tabular-nums">{it.code}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  );
}
