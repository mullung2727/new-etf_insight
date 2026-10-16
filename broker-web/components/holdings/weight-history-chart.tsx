"use client";

import {
  CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis,
} from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { CHART_BAR_COLOR as LINE_COLOR } from "@/lib/tokens";

const lineConfig = { weight_pct: { label: "비중", color: LINE_COLOR } } satisfies ChartConfig;

const noData = <p className="py-12 text-center text-sm text-muted-foreground">비중 이력이 없습니다.</p>;

export function WeightHistoryChart({ data }: { data: { asof: string; weight_pct: number }[] }) {
  if (data.length === 0) return noData;
  return (
    <ChartContainer config={lineConfig} className="h-[320px] w-full" initialDimension={{ width: 600, height: 320 }}>
      <LineChart data={data} margin={{ left: 8, right: 16 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="asof" tick={{ fontSize: 11 }} />
        <YAxis tickFormatter={(v) => `${v}%`} width={56} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ReferenceLine y={5} strokeDasharray="4 4" />
        <ReferenceLine y={3} strokeDasharray="4 4" />
        <Line type="monotone" dataKey="weight_pct" stroke={LINE_COLOR} strokeWidth={2} dot={false} />
      </LineChart>
    </ChartContainer>
  );
}
