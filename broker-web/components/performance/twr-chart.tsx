"use client";

import {
  CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis,
} from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { CHART_BAR_COLOR as LINE_COLOR, CHART_CATEGORICAL } from "@/lib/tokens";

const BT_COLOR = CHART_CATEGORICAL[1];

const lineConfig = {
  livePct: { label: "실매매", color: LINE_COLOR },
  btPct: { label: "백테스트", color: BT_COLOR },
} satisfies ChartConfig;

const noData = <p className="py-12 text-center text-sm text-muted-foreground">스냅샷이 없습니다.</p>;

function fmtDay(v: string) {
  return `${v.slice(4, 6)}-${v.slice(6, 8)}`;
}

export function TwrChart({ data }: { data: { date: string; livePct: number; btPct: number | null; flowNote: string | null }[] }) {
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
        <Line type="monotone" dataKey="livePct" stroke={LINE_COLOR} strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="btPct" stroke={BT_COLOR} strokeWidth={2} dot={false} strokeDasharray="5 3" connectNulls={false} />
      </LineChart>
    </ChartContainer>
  );
}
