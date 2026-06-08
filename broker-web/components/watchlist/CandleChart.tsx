"use client";

import ReactECharts from "echarts-for-react";

// ECharts color constants — mirror CSS variables (ECharts doesn't support CSS vars)
const C_PRIMARY = "#F0B429"; // var(--primary)
const C_UP = "#ef4444";      // var(--up)
const C_DOWN = "#3b82f6";    // var(--down)

interface CandleData {
  dt: string;
  open_pric: string;
  high_pric: string;
  low_pric: string;
  cur_prc: string;
  trde_qty: string;
  pred_pre: string;
}

interface Props {
  data: CandleData[];
  baseDate: string; // YYYYMMDD
}

export default function CandleChart({ data, baseDate }: Props) {
  const dates = data.map(
    (d) => `${d.dt.slice(0, 4)}-${d.dt.slice(4, 6)}-${d.dt.slice(6, 8)}`
  );
  const baseFormatted = `${baseDate.slice(0, 4)}-${baseDate.slice(4, 6)}-${baseDate.slice(6, 8)}`;
  const baseDateIndex = dates.indexOf(baseFormatted);

  // ECharts candlestick: [open, close, low, high]
  // 단, tooltip params.data는 내부적으로 [xIndex, open, close, low, high]로 반환됨
  const ohlc = data.map((d) => [
    parseFloat(d.open_pric),
    parseFloat(d.cur_prc),
    parseFloat(d.low_pric),
    parseFloat(d.high_pric),
  ]);

  const volumes = data.map((d) => ({
    value: parseFloat(d.trde_qty),
    itemStyle: {
      color:
        parseFloat(d.cur_prc) >= parseFloat(d.open_pric)
          ? `${C_UP}66`
          : `${C_DOWN}66`,
    },
  }));

  const markLineData =
    baseDateIndex >= 0
      ? [
          {
            xAxis: baseDateIndex,
            lineStyle: { color: C_PRIMARY, type: "dashed" as const, width: 1.5 },
            label: {
              formatter: "기준일",
              color: C_PRIMARY,
              fontSize: 10,
              fontFamily: "'IBM Plex Mono', monospace",
            },
          },
        ]
      : [];

  const option = {
    backgroundColor: "transparent",
    textStyle: {
      fontFamily: "'IBM Plex Mono', monospace",
      color: "rgba(255,255,255,0.4)",
    },
    grid: [
      { left: 70, right: 20, top: 20, bottom: "32%" },
      { left: 70, right: 20, top: "73%", bottom: 20 },
    ],
    axisPointer: {
      link: [{ xAxisIndex: "all" }],
      lineStyle: { color: "rgba(255,255,255,0.15)" },
    },
    xAxis: [
      {
        type: "category",
        data: dates,
        gridIndex: 0,
        axisLine: { lineStyle: { color: "rgba(255,255,255,0.08)" } },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
      {
        type: "category",
        data: dates,
        gridIndex: 1,
        axisLine: { lineStyle: { color: "rgba(255,255,255,0.08)" } },
        axisLabel: {
          color: "rgba(255,255,255,0.3)",
          fontSize: 10,
          rotate: 30,
        },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
        axisLabel: {
          color: "rgba(255,255,255,0.3)",
          fontSize: 10,
          formatter: (v: number) => v.toLocaleString(),
        },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        type: "candlestick",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: ohlc,
        itemStyle: {
          color: C_UP,
          color0: C_DOWN,
          borderColor: C_UP,
          borderColor0: C_DOWN,
        },
        markLine: {
          symbol: "none",
          data: markLineData,
        },
      },
      {
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
        barMaxWidth: 12,
      },
    ],
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      backgroundColor: "rgba(6,14,28,0.95)",
      borderColor: "rgba(240,180,41,0.2)",
      textStyle: {
        color: "rgba(255,255,255,0.7)",
        fontSize: 11,
        fontFamily: "'IBM Plex Mono', monospace",
      },
      formatter: (params: unknown[]) => {
        type Param = { seriesType: string; name: string; data: number[]; dataIndex: number };
        const ps = params as Param[];
        const candle = ps.find((p) => p.seriesType === "candlestick");
        if (!candle) return "";
        const [, open, , low, high] = candle.data;
        const raw = data[candle.dataIndex];
        const close = parseFloat(raw.cur_prc);
        const predPre = parseFloat(raw.pred_pre);
        const prevClose = close - predPre;
        const pct = prevClose !== 0 ? (predPre / prevClose) * 100 : 0;
        const volume = parseFloat(raw.trde_qty).toLocaleString();
        const isUp = predPre >= 0;
        const changeStr = `${isUp ? "+" : ""}${pct.toFixed(2)}%`;
        return `
          <div style="font-size:11px;line-height:1.8">
            <div style="color:rgba(255,255,255,0.4);margin-bottom:4px">${candle.name}</div>
            <div>시가 <span style="color:#fff">${open.toLocaleString()}</span></div>
            <div>고가 <span style="color:${C_UP}">${high.toLocaleString()}</span></div>
            <div>저가 <span style="color:${C_DOWN}">${low.toLocaleString()}</span></div>
            <div>종가 <span style="color:#fff">${close.toLocaleString()}</span></div>
            <div>전일대비 <span style="color:${isUp ? C_UP : C_DOWN}">${changeStr}</span></div>
            <div>거래량 <span style="color:rgba(255,255,255,0.6)">${volume}</span></div>
          </div>
        `;
      },
    },
  };

  return (
    <ReactECharts
      option={option}
      style={{ height: "520px", width: "100%" }}
      opts={{ renderer: "canvas" }}
    />
  );
}
