"use client";

import { useEffect, useState } from "react";
import { brokerClient, type ConditionItem } from "@/lib/broker-client";
import { DataTable, type Column } from "@/components/common/data-table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";

interface ResultRow {
  stk_cd: string;
  stk_nm: string;
  [key: string]: unknown;
}

const resultColumns: Column<ResultRow>[] = [
  { key: "stk_cd", header: "종목코드", className: "font-mono w-24" },
  { key: "stk_nm", header: "종목명" },
];

interface ConditionPanelProps {
  onSymbolSelect?: (symbol: string) => void;
}

export function ConditionPanel({ onSymbolSelect }: ConditionPanelProps) {
  const [conditions, setConditions] = useState<ConditionItem[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [results, setResults] = useState<ResultRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    brokerClient.listConditions().then(setConditions).catch(() => {});
  }, []);

  const run = async () => {
    if (!selected) return;
    setLoading(true);
    setError(null);
    try {
      const raw = await brokerClient.runCondition(selected);
      const rows: ResultRow[] = (raw.data as ResultRow[] | undefined) ?? [];
      setResults(rows);
      if (rows.length === 0) setError("조건 충족 종목 없음");
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">조건검색</h2>

      <div className="flex gap-2">
        <Select value={selected} onValueChange={(v) => setSelected(v ?? "")}>
          <SelectTrigger className="flex-1">
            <SelectValue placeholder="조건식 선택..." />
          </SelectTrigger>
          <SelectContent>
            {conditions.map((c) => (
              <SelectItem key={c.seq} value={c.seq}>
                {c.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button onClick={run} disabled={loading || !selected}>
          {loading ? "실행중..." : "검색"}
        </Button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      {results.length > 0 && (
        <DataTable
          columns={resultColumns}
          rows={results}
          onRowClick={(row) => onSymbolSelect?.(row.stk_cd)}
          emptyMessage="결과 없음"
        />
      )}
    </div>
  );
}
