"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

const SESSIONS: { value: string; label: string }[] = [
  { value: "", label: "전체" },
  { value: "morning", label: "장전" },
  { value: "close", label: "종가" },
  { value: "evening", label: "장마감후" },
];

/** 세션 필터. 현재 쿼리 보존 + ?tg_session= 만 교체(빈값이면 제거). */
export default function TelegramFilter({ session }: { session: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  return (
    <div className="flex gap-1 mb-6">
      {SESSIONS.map((s) => (
        <button
          key={s.value}
          onClick={() => {
            const q = new URLSearchParams(params.toString());
            if (s.value) q.set("tg_session", s.value);
            else q.delete("tg_session");
            router.push(`${pathname}?${q.toString()}`);
          }}
          className={
            "px-2.5 py-1 text-[12px] tracking-[0.1em] rounded border transition-colors " +
            (s.value === session
              ? "border-primary text-primary"
              : "border-primary/20 text-white/40 hover:text-white/70")
          }
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}
