import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { stockHubHref } from "@/lib/stock-hub";
import type { DigestLatest } from "@/lib/queries";

const SESSION_LABEL: Record<string, string> = {
  morning: "장전", close: "종가", evening: "장마감후",
};
const CHANGE_LABEL: Record<string, string> = { new: "🆕 신규", continued: "🔁 지속" };

export function DigestCard({ digest }: { digest: DigestLatest | null }) {
  return (
    <Card className="bg-card border-border">
      <CardHeader className="flex-row items-center justify-between pb-2">
        <CardTitle className="text-base">📊 텔레그램 종목요약</CardTitle>
        {digest && (
          <span className="text-xs text-muted-foreground">
            {digest.date} · {SESSION_LABEL[digest.session] ?? digest.session} · {digest.count}종목
          </span>
        )}
      </CardHeader>
      <CardContent>
        {!digest || digest.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">아직 분석된 종목요약이 없습니다.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border">
            {digest.items.map((it) => (
              <li key={it.ticker} className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0">
                <div className="flex items-center gap-2 flex-wrap">
                  {it.change_type && (
                    <Badge variant="secondary" className="shrink-0">
                      {CHANGE_LABEL[it.change_type] ?? it.change_type}
                    </Badge>
                  )}
                  <Link
                    href={stockHubHref(it.ticker, { name: it.name ?? undefined })}
                    className="font-medium hover:text-primary transition-colors"
                  >
                    {it.name}
                    <span className="text-muted-foreground font-normal"> ({it.ticker})</span>
                  </Link>
                  <span className="text-xs text-muted-foreground">{it.channels}채널</span>
                  {it.themes.slice(0, 3).map((t) => (
                    <span key={t} className="text-xs text-primary">#{t}</span>
                  ))}
                </div>
                {it.change_summary && (
                  <p className="text-sm text-muted-foreground">{it.change_summary}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
