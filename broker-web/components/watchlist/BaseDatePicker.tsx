"use client";

import { useRouter } from "next/navigation";
import { stockHubHref } from "@/lib/stock-hub";

interface Props {
  code: string;
  baseDate: string; // YYYYMMDD
  name?: string;
}

/** 기준일 선택. 변경 시 ?date=YYYYMMDD 로 이동(차트+스코어 동시 갱신). */
export default function BaseDatePicker({ code, baseDate, name }: Props) {
  const router = useRouter();
  const today = new Date().toISOString().slice(0, 10);
  const value = `${baseDate.slice(0, 4)}-${baseDate.slice(4, 6)}-${baseDate.slice(6, 8)}`;

  return (
    <input
      type="date"
      value={value}
      max={today}
      onChange={(e) => {
        const next = e.target.value.replace(/-/g, "");
        if (!next) return;
        router.push(stockHubHref(code, { tab: "chart", date: next, name }));
      }}
      style={{ colorScheme: "dark" }}
      className="bg-transparent border border-primary/30 rounded px-2 py-1 text-[13px] text-white/70 font-terminal cursor-pointer hover:border-primary/50"
    />
  );
}
