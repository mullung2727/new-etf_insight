"use client";

import dynamic from "next/dynamic";
import { FinancialItem } from "@/types/dart";
import { extractKeyMetrics, buildChartData } from "@/lib/parser";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

// ECharts color constants — mirror CSS variables (ECharts doesn't support CSS vars)
const C_PRIMARY = "#F0B429"; // var(--primary)
const C_UP = "#ef4444";      // var(--up)
const C_DOWN = "#3B82F6";    // var(--down)
const C_GREEN = "#22C55E";

interface FinancialChartProps {
  items: FinancialItem[];
  year: string;
  corpName: string;
}

function toEok(val: number | null): number | null {
  if (val === null) return null;
  return Math.round(val / 1_0000_0000);
}

export default function FinancialChart({ items, year, corpName }: FinancialChartProps) {
  if (items.length === 0) {
    return (
      <div className="bg-[#0A1628]/80 border border-primary/[0.12] rounded-[2px] h-[400px] flex items-center justify-center font-terminal">
        <div className="text-center">
          <div
            className="w-60 h-20 bg-white/[0.03] rounded-[2px] mb-3 mx-auto"
            style={{ animation: "pulse 1.5s ease-in-out infinite" }}
          />
          <style>{`@keyframes pulse { 0%,100%{opacity:0.4} 50%{opacity:0.8} }`}</style>
          <p className="text-white/20 text-[10px] tracking-[0.15em]">AWAITING DATA</p>
        </div>
      </div>
    );
  }

  const metrics = extractKeyMetrics(items);
  const chartData = buildChartData([{ year, metrics }]);

  const revenueEok = toEok(chartData.revenue[0]);
  const opProfitEok = toEok(chartData.operatingProfit[0]);
  const netIncomeEok = toEok(chartData.netIncome[0]);
  const opMargin = chartData.operatingMargin[0];

  const barOption = {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      backgroundColor: "#0D1B35",
      borderColor: "rgba(240,180,41,0.3)",
      textStyle: { color: "rgba(255,255,255,0.85)", fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
      formatter: (params: { name: string; value: number; seriesName: string }[]) => {
        return params
          .map(
            (p) =>
              `<span style="color:rgba(240,180,41,0.7);font-size:9px;letter-spacing:0.1em">${p.seriesName}</span><br/>` +
              `<span style="font-size:13px;font-weight:700">${p.value !== null ? p.value.toLocaleString("ko-KR") + " 억원" : "—"}</span>`
          )
          .join("<br/><br/>");
      },
    },
    legend: {
      top: 8,
      right: 16,
      textStyle: { color: "rgba(255,255,255,0.5)", fontSize: 10, fontFamily: "IBM Plex Mono, monospace" },
      itemWidth: 12,
      itemHeight: 2,
    },
    grid: { left: 16, right: 16, top: 48, bottom: 24, containLabel: true },
    xAxis: {
      type: "category",
      data: [corpName || year],
      axisLine: { lineStyle: { color: "rgba(240,180,41,0.2)" } },
      axisTick: { show: false },
      axisLabel: { color: "rgba(255,255,255,0.4)", fontSize: 10, fontFamily: "IBM Plex Mono, monospace" },
    },
    yAxis: {
      type: "value",
      axisLabel: {
        color: "rgba(255,255,255,0.3)",
        fontSize: 9,
        fontFamily: "IBM Plex Mono, monospace",
        formatter: (v: number) => `${v.toLocaleString("ko-KR")}억`,
      },
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
      axisLine: { show: false },
    },
    series: [
      {
        name: "매출액",
        type: "bar",
        data: [revenueEok],
        itemStyle: { color: C_PRIMARY, borderRadius: [2, 2, 0, 0] },
        barWidth: "20%",
      },
      {
        name: "영업이익",
        type: "bar",
        data: [opProfitEok],
        itemStyle: {
          color: opProfitEok !== null && opProfitEok < 0 ? C_UP : C_DOWN,
          borderRadius: [2, 2, 0, 0],
        },
        barWidth: "20%",
      },
      {
        name: "당기순이익",
        type: "bar",
        data: [netIncomeEok],
        itemStyle: {
          color: netIncomeEok !== null && netIncomeEok < 0 ? C_UP : C_GREEN,
          borderRadius: [2, 2, 0, 0],
        },
        barWidth: "20%",
      },
    ],
  };

  const gaugeOption = {
    backgroundColor: "transparent",
    series: [
      {
        type: "gauge",
        radius: "85%",
        startAngle: 200,
        endAngle: -20,
        min: -50,
        max: 50,
        splitNumber: 5,
        axisLine: {
          lineStyle: {
            width: 8,
            color: [
              [0.3, C_UP],
              [0.5, "rgba(255,255,255,0.1)"],
              [0.7, "rgba(255,255,255,0.1)"],
              [1, C_PRIMARY],
            ],
          },
        },
        pointer: {
          icon: "path://M12.8,0.7l12,40.1H0.7L12.8,0.7z",
          length: "55%",
          width: 6,
          offsetCenter: [0, "-55%"],
          itemStyle: { color: C_PRIMARY },
        },
        axisTick: { distance: -14, length: 4, lineStyle: { color: "rgba(255,255,255,0.2)", width: 1 } },
        splitLine: { distance: -18, length: 10, lineStyle: { color: "rgba(255,255,255,0.15)", width: 2 } },
        axisLabel: { distance: 4, color: "rgba(255,255,255,0.3)", fontSize: 9, fontFamily: "IBM Plex Mono, monospace" },
        detail: {
          valueAnimation: true,
          color: opMargin !== null && opMargin < 0 ? C_UP : C_PRIMARY,
          fontSize: 22,
          fontWeight: 700,
          fontFamily: "IBM Plex Mono, monospace",
          formatter: (v: number) => `${v.toFixed(1)}%`,
          offsetCenter: [0, "25%"],
        },
        title: {
          offsetCenter: [0, "48%"],
          color: "rgba(255,255,255,0.3)",
          fontSize: 9,
          fontFamily: "IBM Plex Mono, monospace",
        },
        data: [{ value: opMargin ?? 0, name: "영업이익률" }],
      },
    ],
  };

  return (
    <div
      className="border border-primary/20 rounded-[2px] overflow-hidden font-terminal"
      style={{ background: "linear-gradient(135deg, #0A1628 0%, #0f1f3d 100%)" }}
    >
      {/* 상단 골드 라인 */}
      <div
        className="h-[2px]"
        style={{
          background: `linear-gradient(90deg, transparent, ${C_PRIMARY} 30%, ${C_PRIMARY} 70%, transparent)`,
        }}
      />

      {/* 헤더 */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-primary/10">
        <div className="flex items-center gap-3">
          <div
            className="w-[6px] h-[6px] rounded-full bg-primary"
            style={{ boxShadow: `0 0 8px ${C_PRIMARY}` }}
          />
          <span className="text-primary text-[10px] tracking-[0.2em] font-semibold">
            FINANCIAL METRICS — {year}
          </span>
        </div>
        <span className="text-white/20 text-[9px] tracking-[0.1em]">단위: 억원</span>
      </div>

      {/* 차트 2열 레이아웃 */}
      <div className="grid grid-cols-1 lg:grid-cols-3">
        <div className="lg:col-span-2 border-r border-primary/[0.08]">
          <div className="py-2">
            <ReactECharts option={barOption} style={{ width: "100%", height: "320px" }} />
          </div>
        </div>
        <div className="flex flex-col items-center justify-center">
          <ReactECharts option={gaugeOption} style={{ width: "100%", height: "280px" }} />
        </div>
      </div>
    </div>
  );
}
