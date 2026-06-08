"use client";

import dynamic from "next/dynamic";

// ECharts는 canvas/DOM API를 사용하므로 SSR에서 실행 불가
const CandleChart = dynamic(() => import("./CandleChart"), { ssr: false });

export default CandleChart;
