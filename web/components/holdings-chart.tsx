"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import type { HoldingStat } from "@/lib/queries";

const BAR_COLOR = "#6366f1";

const PIE_COLORS = [
  "#6366f1", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6",
  "#ec4899", "#14b8a6", "#f97316", "#84cc16", "#06b6d4",
  "#3b82f6", "#a855f7", "#22c55e", "#f43f5e", "#0ea5e9",
];

const THRESHOLD = 3;

const barConfig = {
  avg_weight: {
    label: "평균 비중",
    color: BAR_COLOR,
  },
} satisfies ChartConfig;

interface PieEntry {
  name: string;
  avg_weight: number;
  etf_count: number;
}

function buildPieData(data: HoldingStat[]): PieEntry[] {
  const significant = data.filter((d) => d.avg_weight >= THRESHOLD);
  const others = data.filter((d) => d.avg_weight < THRESHOLD);
  const othersWeight =
    Math.round(others.reduce((s, d) => s + d.avg_weight, 0) * 100) / 100;
  return [
    ...significant,
    ...(othersWeight > 0
      ? [{ name: "기타", avg_weight: othersWeight, etf_count: others.length }]
      : []),
  ];
}

interface HoldingsChartProps {
  data: HoldingStat[];
}

const noData = (
  <p className="py-12 text-center text-sm text-muted-foreground">
    구성종목 데이터가 없습니다.
  </p>
);

export function HoldingsBarChart({ data }: HoldingsChartProps) {
  if (data.length === 0) return noData;

  return (
    <ChartContainer
      config={barConfig}
      className="h-[520px] w-full"
      initialDimension={{ width: 600, height: 520 }}
    >
      <BarChart layout="vertical" data={data} margin={{ left: 8, right: 40 }}>
        <CartesianGrid horizontal={false} />
        <XAxis type="number" tickFormatter={(v) => `${v}%`} />
        <YAxis
          type="category"
          dataKey="name"
          width={180}
          tick={{ fontSize: 11 }}
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              formatter={(value, _name, item) => [
                `${value}% · ${item.payload.etf_count}개 ETF`,
                "평균 비중",
              ]}
            />
          }
        />
        <Bar dataKey="avg_weight" fill={BAR_COLOR} radius={3} />
      </BarChart>
    </ChartContainer>
  );
}

export function HoldingsPieChart({ data }: HoldingsChartProps) {
  if (data.length === 0) return noData;

  const pieData = buildPieData(data);

  return (
    <div className="h-[380px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={pieData}
            dataKey="avg_weight"
            nameKey="name"
            cx="50%"
            cy="50%"
            outerRadius={140}
          >
            {pieData.map((_, i) => (
              <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            formatter={(value, name) => [`${value}%`, name]}
          />
          <Legend
            layout="vertical"
            align="right"
            verticalAlign="middle"
            formatter={(value) => (
              <span style={{ fontSize: 11 }}>{value}</span>
            )}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
