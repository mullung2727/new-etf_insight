"use client";

import { useEffect, useRef, useState } from "react";
import { brokerClient, type BalanceData, type DepositData, type Holding, type Settings } from "@/lib/broker-client";
import { useBrokerEvents, isFillEvent } from "@/lib/use-broker-events";
import { StatCard } from "@/components/common/stat-card";
import { DataTable, type Column } from "@/components/common/data-table";
import { ChangeBadge } from "@/components/common/change-badge";
import { formatKrw, parseKrwString } from "@/lib/formatters";
import { Button } from "@/components/ui/button";

const holdingColumns: Column<Holding>[] = [
  { key: "stk_nm", header: "종목명" },
  {
    key: "cur_prc",
    header: "현재가/매입가",
    className: "text-right tabular-nums text-xs",
    render: (row) => (
      <div className="flex flex-col gap-0.5">
        <span>{formatKrw(parseKrwString(row.cur_prc as string))}</span>
        <span className="text-muted-foreground">{formatKrw(parseKrwString(row.pur_pric as string))}</span>
      </div>
    ),
  },
  {
    key: "rmnd_qty",
    header: "수량",
    className: "text-right tabular-nums",
    render: (row) => {
      const v = parseKrwString(row.rmnd_qty as string);
      return <span>{v != null ? v.toLocaleString() : "-"}</span>;
    },
  },
  {
    key: "evltv_prft",
    header: "평가손익(수익률)",
    className: "text-right text-xs",
    render: (row) => {
      const rt = parseFloat((row.prft_rt as string ?? "").replace(/[^0-9.-]/g, ""));
      return (
        <div className="flex flex-col gap-0.5 items-end">
          <ChangeBadge value={parseKrwString(row.evltv_prft as string)} format="krw" />
          <ChangeBadge value={isNaN(rt) ? null : rt} format="percent" />
        </div>
      );
    },
  },
  {
    key: "pur_amt",
    header: "매입/평가금액",
    className: "text-right tabular-nums text-xs",
    render: (row) => (
      <div className="flex flex-col gap-0.5">
        <span>{formatKrw(parseKrwString(row.pur_amt as string))}</span>
        <span className="text-muted-foreground">{formatKrw(parseKrwString(row.evlt_amt as string))}</span>
      </div>
    ),
  },
  {
    key: "poss_rt",
    header: "비중",
    className: "text-right tabular-nums",
    render: (row) => {
      const v = parseFloat((row.poss_rt as string ?? "").replace(/[^0-9.-]/g, ""));
      return <span>{isNaN(v) ? "-" : `${v.toFixed(2)}%`}</span>;
    },
  },
];

export function AccountPanel() {
  const [data, setData] = useState<BalanceData | null>(null);
  const [depositData, setDepositData] = useState<DepositData | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [envSwitching, setEnvSwitching] = useState(false);
  // 체결 연타 시 재조회가 겹칠 수 있음 → 최신 호출만 state에 반영 (stale 응답 무시)
  const reqSeq = useRef(0);

  // 잔고+예수금만 재조회 (체결 이벤트 경로 — settings 토글과 무관)
  const loadAccount = async () => {
    const seq = ++reqSeq.current;
    setLoading(true);
    setError(null);
    try {
      const [balance, deposit] = await Promise.all([
        brokerClient.getBalance(),
        brokerClient.getDeposit(),
      ]);
      if (seq !== reqSeq.current) return; // 더 최신 재조회가 이미 진행됨
      setData(balance);
      setDepositData(deposit);
    } catch (e) {
      if (seq !== reqSeq.current) return;
      setError(String(e));
    } finally {
      if (seq === reqSeq.current) setLoading(false);
    }
  };

  // settings(모의/실전) 포함 전체 로드 (초기·새로고침)
  const load = async () => {
    try {
      setSettings(await brokerClient.getSettings());
    } catch (e) {
      setError(String(e));
    }
    await loadAccount();
  };

  const toggleEnv = async () => {
    if (!settings || envSwitching) return;
    const next = settings.env === "paper" ? "real" : "paper";
    setEnvSwitching(true);
    try {
      const s = await brokerClient.updateSettings(next);
      setSettings(s);
      await loadAccount();
    } catch (e) {
      setError(String(e));
    } finally {
      setEnvSwitching(false);
    }
  };

  useEffect(() => { load(); }, []);

  // 체결 통보(913=체결) 오면 주문가능/총평가금액 즉시 재조회
  useBrokerEvents((e) => { if (isFillEvent(e)) loadAccount(); });

  const deposit = parseKrwString(depositData?.entr);
  const ordAlow = parseKrwString(depositData?.ord_alow_amt);
  const evalAmt = parseKrwString(data?.tot_evlt_amt);
  const pl = parseKrwString(data?.tot_evlt_pl);
  const holdings: Holding[] = data?.acnt_evlt_remn_indv_tot ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">계좌</h2>
          {settings && (
            <button
              onClick={toggleEnv}
              disabled={envSwitching}
              className={`text-xs px-2 py-0.5 rounded border font-medium transition-colors ${
                settings.env === "paper"
                  ? "border-status-partial/50 text-status-partial hover:bg-status-partial/10"
                  : "border-status-profit/50 text-status-profit hover:bg-status-profit/10"
              }`}
            >
              {envSwitching ? "..." : settings.env === "paper" ? "모의" : "실전"}
            </button>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
          {loading ? "로딩..." : "새로고침"}
        </Button>
      </div>

      {error && <p className="text-xs text-status-loss">{error}</p>}

      <StatCard
        label="주문가능"
        value={ordAlow != null ? formatKrw(ordAlow) : "-"}
        sub={deposit != null ? `예수금 ${formatKrw(deposit)}` : undefined}
      />
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
