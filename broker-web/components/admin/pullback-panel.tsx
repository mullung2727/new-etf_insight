"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Form = {
  budget: string; maxPositions: string; tp: string; sl: string; waitDays: string; holdDays: string;
};

const EMPTY: Form = { budget: "", maxPositions: "", tp: "", sl: "", waitDays: "", holdDays: "" };
const number = (value: string) => Number(value.replace(/,/g, ""));

export function PullbackPanel() {
  const [form, setForm] = useState(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    fetch("/api/pullback-config").then(async (response) => {
      const config = await response.json();
      if (!response.ok) { setMessage(config.error ?? "설정을 불러오지 못했습니다"); return; }
      setForm({
        budget: Number(config.budget_per_stock).toLocaleString("en-US"),
        maxPositions: String(config.max_new_positions), tp: String(config.tp * 100),
        sl: String(config.sl * 100), waitDays: String(config.max_wait_days),
        holdDays: String(config.max_hold_days),
      });
      setLoaded(true);
    });
  }, []);

  const set = (key: keyof Form, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
    setMessage("");
  };

  const save = async () => {
    const response = await fetch("/api/pullback-config", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        budget_per_stock: number(form.budget), max_new_positions: number(form.maxPositions),
        tp: number(form.tp) / 100, sl: number(form.sl) / 100,
        max_wait_days: number(form.waitDays), max_hold_days: number(form.holdDays),
      }),
    });
    const result = await response.json();
    setMessage(response.ok ? "저장됨." : result.error ?? `오류 ${response.status}`);
  };

  if (!loaded) return <p className="text-sm text-muted-foreground">{message || "불러오는 중…"}</p>;
  const fields: [keyof Form, string, string][] = [
    ["budget", "종목당 예산", "원"], ["maxPositions", "하루 최대 신규 매수", "종목"],
    ["tp", "익절", "%"], ["sl", "손절", "%"],
    ["waitDays", "watchlist 관찰 기간", "거래일"], ["holdDays", "최대 보유 기간", "거래일"],
  ];
  return <div className="space-y-6">
    <div className="flex items-start justify-between gap-4">
      <div><h1 className="text-base font-semibold">눌림목 매매 전략값</h1>
        <p className="text-sm text-muted-foreground">저장하면 다음 배치 실행부터 반영됩니다.</p></div>
      <Button onClick={save}>저장</Button>
    </div>
    {message && <p className="rounded-md border border-border px-3 py-2 text-sm">{message}</p>}
    {fields.map(([key, label, suffix]) => <div key={key} className="space-y-1">
      <label className="text-sm font-medium">{label}</label>
      <div className="flex items-center gap-2"><Input value={form[key]} onChange={(event) => set(key, event.target.value)} className="h-8 w-40" />
        <span className="text-sm text-muted-foreground">{suffix}</span></div>
    </div>)}
  </div>;
}
