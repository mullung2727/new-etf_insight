"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

// 재무 탭 연간/분기 토글. 데이터 fetch 안 함 — URL ?fin_mode= 만 바꿔 서버 재렌더.
export default function FinancialModeToggle({ mode }: { mode: "annual" | "quarterly" }) {
  const pathname = usePathname();
  const sp = useSearchParams();

  function href(m: "annual" | "quarterly"): string {
    const p = new URLSearchParams(sp.toString());
    p.set("tab", "financial");
    p.set("fin_mode", m);
    return `${pathname}?${p.toString()}`;
  }

  return (
    <div data-testid="fin-mode-toggle" className="mb-6 flex items-center gap-2">
      {(["annual", "quarterly"] as const).map((m) => (
        <Link
          key={m}
          data-testid={`mode-${m}`}
          href={href(m)}
          aria-pressed={mode === m}
          className={[
            "px-4 py-[6px] text-fin-xs tracking-[0.15em] uppercase rounded-[2px] border transition-colors",
            mode === m
              ? "border-fin-gold/60 text-fin-gold bg-fin-gold/[0.08]"
              : "border-fin-gold/15 text-fin-muted hover:text-fin-label hover:border-fin-gold/30",
          ].join(" ")}
        >
          {m === "annual" ? "연간 5년" : "분기 8개"}
        </Link>
      ))}
    </div>
  );
}
