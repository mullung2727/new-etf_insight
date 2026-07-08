"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { brokerClient, type BalanceData } from "@/lib/broker-client";
import { useBrokerEvents, isFillEvent } from "@/lib/use-broker-events";
import { StatCard } from "@/components/common/stat-card";
import { ChangeBadge } from "@/components/common/change-badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatKrw, parseKrwString } from "@/lib/formatters";

export function PositionSummary() {
  const [data, setData] = useState<BalanceData | null>(null);
  const [offline, setOffline] = useState(false);

  const load = async () => {
    try {
      setData(await brokerClient.getBalance());
      setOffline(false);
    } catch {
      setOffline(true); // broker(:8001) 미기동/미연결 — 메인 전체를 막지 않고 카드만 폴백
    }
  };

  useEffect(() => { load(); }, []);
  useBrokerEvents((e) => { if (isFillEvent(e)) load(); });

  if (offline) {
    return (
      <Card className="bg-card border-border h-full">
        <CardContent className="flex flex-col items-start gap-2 py-2">
          <p className="text-sm text-muted-foreground">💼 트레이딩 미연결</p>
          <Link href="/trading" className="text-sm text-primary hover:underline">
            트레이딩 열기 →
          </Link>
        </CardContent>
      </Card>
    );
  }

  const evalAmt = parseKrwString(data?.tot_evlt_amt);
  const pl = parseKrwString(data?.tot_evlt_pl);
  const rt = parseFloat((data?.tot_prft_rt as string ?? "").replace(/[^0-9.-]/g, ""));
  const holdings = data?.acnt_evlt_remn_indv_tot?.length ?? 0;

  return (
    <div className="grid grid-cols-3 gap-3 h-full">
      <StatCard label="총평가금액" value={formatKrw(evalAmt)} />
      <Card className="bg-card border-border">
        <CardContent className="flex flex-col gap-1 py-2">
          <p className="text-sm font-medium text-muted-foreground">평가손익</p>
          <p className="text-xl font-bold">
            <ChangeBadge value={pl} format="krw" />
          </p>
          <ChangeBadge value={isNaN(rt) ? null : rt} format="percent" className="text-xs" />
        </CardContent>
      </Card>
      <Card className="bg-card border-border">
        <CardContent className="flex flex-col gap-1 py-2">
          <p className="text-sm font-medium text-muted-foreground">보유종목</p>
          <p className="text-xl font-bold tabular-nums">{holdings}종목</p>
          <Link href="/trading" className="text-xs text-primary hover:underline">트레이딩 →</Link>
        </CardContent>
      </Card>
    </div>
  );
}
