"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  brokerClient,
  type CloseBetPositions,
  type CloseBetBuy,
} from "@/lib/broker-client";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

function fmtDate(raw: string): string {
  return raw.length === 8
    ? `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`
    : raw;
}

function won(n: number | null): string {
  return n == null ? "-" : n.toLocaleString();
}

function TickerLink({ ticker, date }: { ticker: string; date: string }) {
  // 점수·사유·차트는 watchlist가 담당 → 링크로 위임(중복 회피)
  return (
    <Link href={`/watchlist/${ticker}?date=${date}`}>
      <Badge variant="secondary" className="cursor-pointer hover:bg-secondary/80 font-mono">
        {ticker}
      </Badge>
    </Link>
  );
}

const _EXIT_LABEL: Record<string, string> = { tp: "익절", sl: "손절", forced: "강제" };

function StatusCell({ r }: { r: CloseBetBuy }) {
  // 생애주기 한 칸: 청산완료=청산(사유) 배지, 매도전송=매도중, 그 외=매수상태.
  if (r.sell_status === "filled") {
    const profit = r.pnl_pct != null && r.pnl_pct >= 0;
    return (
      <Badge className={cn("font-mono", profit ? "bg-status-profit/15 text-status-profit" : "bg-status-loss/15 text-status-loss")}>
        청산·{_EXIT_LABEL[r.exit_reason ?? ""] ?? r.exit_reason ?? ""}
      </Badge>
    );
  }
  if (r.sell_status === "ordered") {
    return <Badge variant="outline" className="font-mono">매도중</Badge>;
  }
  return <span className="font-mono text-sm">{r.status}</span>;
}

function fmtTime(utc: string | null): string {
  // created_at은 UTC naive("YYYY-MM-DD HH:MM:SS") → KST 시:분
  if (!utc) return "-";
  const d = new Date(utc.replace(" ", "T") + "Z");
  return d.toLocaleTimeString("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export default function CloseBetPage() {
  const [data, setData] = useState<CloseBetPositions | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    brokerClient
      .getCloseBetPositions()
      .then(setData)
      .catch((e) => setErr(String(e)));
  }, []);

  return (
    <div className="fin-scope min-h-screen bg-background font-terminal">
      <main className="max-w-4xl mx-auto py-10 px-4 space-y-10">
        <h1 className="text-2xl font-bold text-primary tracking-[0.05em]">
          종가배팅 현황
        </h1>

        {err && (
          <p className="text-status-loss text-sm">조회 실패: {err}</p>
        )}
        {!err && !data && (
          <p className="text-muted-foreground text-sm">로딩…</p>
        )}

        {data && (
          <>
            {/* 매수 현황 (일자별 전체) */}
            <section className="space-y-3">
              <h2 className="text-lg font-semibold text-foreground">
                매수 현황 <span className="text-muted-foreground text-sm">({data.buys.length})</span>
              </h2>
              {data.buys.length === 0 ? (
                <p className="text-muted-foreground text-sm">없음</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-right">매수일</TableHead>
                      <TableHead className="text-right">매수시간</TableHead>
                      <TableHead>종목</TableHead>
                      <TableHead className="text-right">점수</TableHead>
                      <TableHead className="text-right">매수가</TableHead>
                      <TableHead>상태</TableHead>
                      <TableHead className="text-right">매도가</TableHead>
                      <TableHead className="text-right">손익</TableHead>
                      <TableHead className="text-right">수수료</TableHead>
                      <TableHead className="text-right">세금</TableHead>
                      <TableHead className="text-right">청산시각</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.buys.map((r) => (
                      <TableRow key={`${r.date}-${r.ticker}`}>
                        <TableCell className="text-right font-mono text-sm">{fmtDate(r.date)}</TableCell>
                        <TableCell className="text-right font-mono text-sm">{fmtTime(r.created_at)}</TableCell>
                        <TableCell><TickerLink ticker={r.ticker} date={r.date} /></TableCell>
                        <TableCell className="text-right font-mono">{r.score ?? "-"}</TableCell>
                        <TableCell className="text-right font-mono">{won(r.cntr_price)}</TableCell>
                        <TableCell><StatusCell r={r} /></TableCell>
                        <TableCell className="text-right font-mono">{won(r.sell_price)}</TableCell>
                        <TableCell
                          className={cn(
                            "text-right font-mono",
                            r.pnl_pct != null && r.pnl_pct > 0 && "text-status-profit",
                            r.pnl_pct != null && r.pnl_pct < 0 && "text-status-loss"
                          )}
                        >
                          {r.pnl_pct == null ? "-" : `${r.pnl_pct > 0 ? "+" : ""}${r.pnl_pct}%`}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs text-muted-foreground">{won(r.sell_cmsn)}</TableCell>
                        <TableCell className="text-right font-mono text-xs text-muted-foreground">{won(r.sell_tax)}</TableCell>
                        <TableCell className="text-right font-mono text-xs text-muted-foreground">
                          {r.sold_at ? r.sold_at.slice(5, 19).replace("T", " ") : "-"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </section>

            {/* 감시중 (오버나이트) — 실시간 손익은 G3에서 /quotes 폴링 */}
            <section className="space-y-3">
              <h2 className="text-lg font-semibold text-foreground">
                감시중 <span className="text-muted-foreground text-sm">({data.watching.length})</span>
              </h2>
              {data.watching.length === 0 ? (
                <p className="text-muted-foreground text-sm">없음</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>종목</TableHead>
                      <TableHead className="text-right">매수일</TableHead>
                      <TableHead className="text-right">점수</TableHead>
                      <TableHead className="text-right">매수가</TableHead>
                      <TableHead className="text-right">수량</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.watching.map((r) => (
                      <TableRow key={`${r.date}-${r.ticker}`}>
                        <TableCell><TickerLink ticker={r.ticker} date={r.date} /></TableCell>
                        <TableCell className="text-right font-mono text-sm">{fmtDate(r.date)}</TableCell>
                        <TableCell className="text-right font-mono">{r.score ?? "-"}</TableCell>
                        <TableCell className="text-right font-mono">{won(r.cntr_price)}</TableCell>
                        <TableCell className="text-right font-mono">{r.qty}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </section>

          </>
        )}
      </main>
    </div>
  );
}
