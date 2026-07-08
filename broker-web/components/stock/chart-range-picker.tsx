"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CHART_RANGES, type ChartRange } from "@/lib/chart-range";

/** 1M/3M/6M/1Y 선택. 현재 쿼리 보존 + ?range= 만 교체. */
export default function ChartRangePicker({ active }: { active: ChartRange }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  return (
    <div className="flex gap-1">
      {CHART_RANGES.map((r) => (
        <button
          key={r}
          onClick={() => {
            const q = new URLSearchParams(params.toString());
            q.set("range", r);
            router.push(`${pathname}?${q.toString()}`);
          }}
          className={
            "px-2.5 py-1 text-[12px] tracking-[0.1em] rounded border transition-colors " +
            (r === active
              ? "border-primary text-primary"
              : "border-primary/20 text-white/40 hover:text-white/70")
          }
        >
          {r}
        </button>
      ))}
    </div>
  );
}
