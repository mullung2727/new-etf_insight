"use client";

import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import type { MetricInfo, PeriodInfo } from "@/lib/queries";

interface Props {
  metrics: MetricInfo[];
  periods: PeriodInfo[];
  current: {
    metric: string; year: string; reprt: string;
    exclude: boolean; order: string;
  };
}

export function RankingControls({ metrics, periods, current }: Props) {
  const router = useRouter();

  function push(patch: Partial<typeof current>) {
    const next = { ...current, ...patch };
    const p = new URLSearchParams({
      metric: next.metric,
      year: next.year,
      reprt: next.reprt,
      exclude: next.exclude ? "1" : "0",
      order: next.order,
    });
    router.push(`?${p.toString()}`);
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground">기간</label>
        <Select
          value={`${current.year}_${current.reprt}`}
          onValueChange={(v) => {
            if (!v) return;
            const [year, reprt] = v.split("_");
            push({ year, reprt });
          }}
        >
          <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            {periods.map((p) => (
              <SelectItem key={`${p.year}_${p.reprt}`} value={`${p.year}_${p.reprt}`}>
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-xs text-muted-foreground">지표</label>
        <Select
          value={current.metric}
          onValueChange={(v) => {
            if (!v) return;
            const m = metrics.find((x) => x.key === v);
            // 지표 바꾸면 그 지표 기본 정렬로 리셋
            push({ metric: v, order: m?.default_order ?? "desc" });
          }}
        >
          <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            {metrics.map((m) => (
              <SelectItem key={m.key} value={m.key}>{m.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Button
        type="button"
        variant={current.exclude ? "default" : "outline"}
        onClick={() => push({ exclude: !current.exclude })}
      >
        자본잠식 {current.exclude ? "제외" : "포함"}
      </Button>

      <Button
        type="button"
        variant="outline"
        onClick={() => push({ order: current.order === "asc" ? "desc" : "asc" })}
      >
        {current.order === "asc" ? "오름차순 ↑" : "내림차순 ↓"}
      </Button>
    </div>
  );
}
