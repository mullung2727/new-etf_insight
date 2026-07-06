"use client";

import {
  Bar, BarChart, CartesianGrid, Cell, Legend,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import type { HoldingStat } from "@/lib/queries";
import { CHART_BAR_COLOR as BAR_COLOR, CHART_CATEGORICAL as PIE_COLORS } from "@/lib/tokens";

const THRESHOLD = 3;

const barConfig = { avg_weight: { label: "평균 비중", color: BAR_COLOR } } satisfies ChartConfig;

function buildPieData(data: HoldingStat[]) {
  const sig = data.filter((d) => d.avg_weight >= THRESHOLD);
  const others = data.filter((d) => d.avg_weight < THRESHOLD);
  const othersWeight = Math.round(others.reduce((s, d) => s + d.avg_weight, 0) * 100) / 100;
  return [...sig, ...(othersWeight > 0 ? [{ name: "기타", avg_weight: othersWeight, etf_count: others.length }] : [])];
}

const noData = <p className="py-12 text-center text-sm text-muted-foreground">구성종목 데이터가 없습니다.</p>;

export function HoldingsBarChart({ data }: { data: HoldingStat[] }) {
  if (data.length === 0) return noData;
  return (
    <ChartContainer config={barConfig} className="h-[520px] w-full" initialDimension={{ width: 600, height: 520 }}>
      <BarChart layout="vertical" data={data} margin={{ left: 8, right: 40 }}>
        <CartesianGrid horizontal={false} />
        <XAxis type="number" tickFormatter={(v) => `${v}%`} />
        <YAxis type="category" dataKey="name" width={180} tick={{ fontSize: 11 }} />
        <ChartTooltip content={<ChartTooltipContent formatter={(value, _name, item) => [`${value}% · ${item.payload.etf_count}개 ETF`, "평균 비중"]} />} />
        <Bar dataKey="avg_weight" fill={BAR_COLOR} radius={3} />
      </BarChart>
    </ChartContainer>
  );
}

export function HoldingsPieChart({ data }: { data: HoldingStat[] }) {
  if (data.length === 0) return noData;
  const pieData = buildPieData(data);
  return (
    <div className="h-[380px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={pieData} dataKey="avg_weight" nameKey="name" cx="50%" cy="50%" outerRadius={140}>
            {pieData.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
          </Pie>
          <Tooltip formatter={(value, name) => [`${value}%`, name]} />
          <Legend layout="vertical" align="right" verticalAlign="middle" formatter={(v) => <span style={{ fontSize: 11 }}>{v}</span>} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
