"use client";

import { useEffect, useState } from "react";
import { brokerClient, type BalanceData, type Holding } from "@/lib/broker-client";
import { StatCard } from "@/components/common/stat-card";
import { DataTable, type Column } from "@/components/common/data-table";
import { ChangeBadge } from "@/components/common/change-badge";
import { formatKrw, parseKrwString } from "@/lib/formatters";
import { Button } from "@/components/ui/button";

const holdingColumns: Column<Holding>[] = [
  { key: "stk_nm", header: "종목명" },
  {
    key: "cur_prc",
    header: "현재가",
    className: "text-right tabular-nums",
    render: (row) => formatKrw(parseKrwString(row.cur_prc as string)),
  },
  {
    key: "evlt_pl",
    header: "평가손익",
    className: "text-right",
    render: (row) => (
      <ChangeBadge value={parseKrwString(row.evlt_pl as string)} format="krw" />
    ),
  },
  {
    key: "prft_rt",
    header: "수익률",
    className: "text-right",
    render: (row) => {
      const v = parseFloat((row.prft_rt as string ?? "").replace(/[^0-9.-]/g, ""));
      return <ChangeBadge value={isNaN(v) ? null : v} format="percent" />;
    },
  },
];

export function AccountPanel() {
  const [data, setData] = useState<BalanceData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await brokerClient.getBalance());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const deposit = parseKrwString(data?.prsm_dpst_aset_amt);
  const evalAmt = parseKrwString(data?.tot_evlt_amt);
  const pl = parseKrwString(data?.tot_evlt_pl);
  const holdings: Holding[] = data?.acnt_evlt_remn_indv_tot ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">계좌</h2>
        <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
          {loading ? "로딩..." : "새로고침"}
        </Button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <StatCard label="예수금" value={deposit != null ? formatKrw(deposit) : "-"} />
      <StatCard
        label="총평가금액"
        value={evalAmt != null ? formatKrw(evalAmt) : "-"}
        sub={pl != null ? `손익 ${formatKrw(pl)}` : undefined}
      />

      <div>
        <p className="text-sm font-medium text-muted-foreground mb-2">보유종목</p>
        <DataTable
          columns={holdingColumns}
          rows={holdings}
          emptyMessage="보유종목 없음"
        />
      </div>
    </div>
  );
}
