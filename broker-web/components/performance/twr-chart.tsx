"use client";

import {
  CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis,
} from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { CHART_BAR_COLOR as LINE_COLOR } from "@/lib/tokens";

const lineConfig = { twrPct: { label: "누적 TWR", color: LINE_COLOR } } satisfies ChartConfig;

const noData = <p className="py-12 text-center text-sm text-muted-foreground">스냅샷이 없습니다.</p>;

function fmtDay(v: string) {
  return `${v.slice(4, 6)}-${v.slice(6, 8)}`;
}

export function TwrChart({ data }: { data: { date: string; twrPct: number; flowNote: string | null }[] }) {
  if (data.length === 0) return noData;
  return (
    <ChartContainer config={lineConfig} className="h-[320px] w-full" initialDimension={{ width: 600, height: 320 }}>
      <LineChart data={data} margin={{ left: 8, right: 16 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="date" tickFormatter={fmtDay} tick={{ fontSize: 11 }} />
        <YAxis tickFormatter={(v) => `${v}%`} width={56} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ReferenceLine y={0} strokeDasharray="4 4" />
        {data.filter((d) => d.flowNote).map((d) => (
          <ReferenceLine key={d.date} x={d.date} strokeDasharray="4 4" />
        ))}
        <Line type="monotone" dataKey="twrPct" stroke={LINE_COLOR} strokeWidth={2} dot={false} />
      </LineChart>
    </ChartContainer>
  );
}
