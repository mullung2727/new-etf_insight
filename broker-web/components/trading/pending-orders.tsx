"use client";

import { useEffect, useState } from "react";
import { brokerClient, type UnfilledOrder } from "@/lib/broker-client";
import { useBrokerEvents } from "@/lib/use-broker-events";
import { DataTable, type Column } from "@/components/common/data-table";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

export function PendingOrders() {
  const [rows, setRows] = useState<UnfilledOrder[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 행별 정정 가격 입력값 (order_no → 문자열). 비어있으면 주문가 그대로.
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await brokerClient.listUnfilled("all");
      setRows(r);
      setEdit({});
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  // 접수/체결/취소/정정 통보(00)가 오면 목록이 바뀌므로 재조회
  useBrokerEvents((e) => { if (e.channel === "00") load(); });

  const act = async (fn: () => Promise<unknown>, orderNo: string) => {
    setBusy(orderNo);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(null);
    }
  };

  const modify = (row: UnfilledOrder) => {
    const price = parseInt((edit[row.order_no] ?? "").replace(/,/g, ""), 10) || row.ord_price;
    return act(() => brokerClient.modifyOrder(row.order_no, row.ticker, price), row.order_no);
  };

  const cancel = (row: UnfilledOrder) =>
    act(() => brokerClient.cancelOrder(row.order_no, row.ticker), row.order_no);

  const columns: Column<UnfilledOrder>[] = [
    { key: "stk_nm", header: "종목명" },
    { key: "order_no", header: "주문번호", className: "text-xs tabular-nums" },
    {
      key: "oso_qty",
      header: "미체결",
      className: "text-right tabular-nums",
      render: (row) => <span>{row.oso_qty.toLocaleString()}</span>,
    },
    {
      key: "ord_price",
      header: "정정가",
      className: "w-28",
      render: (row) => (
        <Input
          className="font-mono h-8 text-right text-xs"
          value={edit[row.order_no] ?? String(row.ord_price)}
          onChange={(e) => setEdit((m) => ({ ...m, [row.order_no]: e.target.value }))}
        />
      ),
    },
    { key: "ord_stt", header: "상태", className: "text-xs" },
    {
      key: "tm",
      header: "",
      className: "text-right whitespace-nowrap",
      render: (row) => (
        <div className="flex gap-1 justify-end">
          <Button variant="outline" size="sm" disabled={busy === row.order_no}
            onClick={() => modify(row)}>정정</Button>
          <Button variant="outline" size="sm" disabled={busy === row.order_no}
            className="text-status-loss border-status-loss/40 hover:bg-status-loss/10"
            onClick={() => cancel(row)}>취소</Button>
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">미체결</h2>
        <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
          {loading ? "로딩..." : "새로고침"}
        </Button>
      </div>
      {error && <p className="text-xs text-status-loss">{error}</p>}
      <DataTable columns={columns} rows={rows} emptyMessage="미체결 주문 없음" />
    </div>
  );
}
