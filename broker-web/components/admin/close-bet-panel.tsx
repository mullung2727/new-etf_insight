"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

// 저장 포맷은 소수(tp/sl 0~1). 화면은 %로 입력/표시하고 저장 직전 ÷100 변환한다.
type Form = {
  score_threshold: string; // 점
  tp: string; // %
  sl: string; // %
  b1: string; // 원
  b2: string;
  b3: string;
};

const EMPTY: Form = { score_threshold: "", tp: "", sl: "", b1: "", b2: "", b3: "" };

// 숫자만 남겨 천단위 콤마. 빈값이면 "".
function comma(s: string): string {
  const d = s.replace(/[^\d]/g, "");
  return d ? Number(d).toLocaleString("en-US") : "";
}
const digits = (s: string) => Number(s.replace(/[^\d]/g, ""));

// 클라 범위검증(서버 lib.validate 와 동일 도메인). 통과 시 null, 실패 시 사용자 문구.
function clientError(f: Form): string | null {
  const st = Number(f.score_threshold);
  if (!Number.isInteger(st) || st < 0 || st > 100) return "매수 기준점수는 0~100 사이 정수여야 합니다";
  for (const [v, label] of [[f.tp, "익절"], [f.sl, "손절"]] as const) {
    const p = Number(v);
    if (!Number.isFinite(p) || v.trim() === "" || p < 0 || p > 100) return `${label}은 0~100% 사이 값이어야 합니다`;
  }
  for (const [v, n] of [[f.b1, "1"], [f.b2, "2"], [f.b3, "3"]] as const) {
    const amt = digits(v);
    if (!Number.isInteger(amt) || amt <= 0) return `${n}종목일 때 예산은 0보다 큰 금액이어야 합니다`;
  }
  return null;
}

export function CloseBetPanel() {
  const [form, setForm] = useState<Form>(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    (async () => {
      const res = await fetch("/api/close-bet-config");
      const c = await res.json();
      setForm({
        score_threshold: String(c.score_threshold),
        tp: String(Math.round(c.tp * 100 * 100) / 100), // 0.05 → 5
        sl: String(Math.round(c.sl * 100 * 100) / 100),
        b1: comma(String(c.budget_by_count["1"])),
        b2: comma(String(c.budget_by_count["2"])),
        b3: comma(String(c.budget_by_count["3"])),
      });
      setLoaded(true);
    })();
  }, []);

  const set = (k: keyof Form, v: string) => {
    setForm((f) => ({ ...f, [k]: v }));
    setSaved(false);
  };

  const save = async () => {
    setError(null);
    setSaved(false);
    const clientErr = clientError(form);
    if (clientErr) {
      setError(clientErr);
      return;
    }
    const payload = {
      score_threshold: Number(form.score_threshold),
      tp: Number(form.tp) / 100,
      sl: Number(form.sl) / 100,
      budget_by_count: { "1": digits(form.b1), "2": digits(form.b2), "3": digits(form.b3) },
    };
    const res = await fetch("/api/close-bet-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      setError((await res.json()).error ?? `오류 ${res.status}`);
      return;
    }
    setSaved(true);
  };

  if (!loaded) return <p className="text-sm text-muted-foreground">불러오는 중…</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-base font-semibold">종가베팅 전략값</h1>
          <p className="text-sm text-muted-foreground">
            저장하면 다음 배치 실행부터 반영됩니다(실행 중 배치엔 영향 없음).
          </p>
        </div>
        <Button onClick={save}>저장</Button>
      </div>

      {error && (
        <p className="rounded-md border border-destructive bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}
      {saved && (
        <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
          저장됨.
        </p>
      )}

      <Field label="매수 기준점수" help="이 점수 이상인 종목만 매수 후보에 올립니다.">
        <Suffixed suffix="점">
          <Input
            type="number"
            value={form.score_threshold}
            onChange={(e) => set("score_threshold", e.target.value)}
            className="h-8 w-32"
          />
        </Suffixed>
      </Field>

      <Field label="익절 (수익 실현)" help="이 수익률에 도달하면 자동으로 매도합니다.">
        <Suffixed suffix="%">
          <Input
            type="number"
            value={form.tp}
            onChange={(e) => set("tp", e.target.value)}
            className="h-8 w-32"
          />
        </Suffixed>
      </Field>

      <Field label="손절 (손실 제한)" help="이 손실률에 도달하면 자동으로 매도합니다.">
        <Suffixed suffix="%">
          <Input
            type="number"
            value={form.sl}
            onChange={(e) => set("sl", e.target.value)}
            className="h-8 w-32"
          />
        </Suffixed>
      </Field>

      <Field
        label="종목당 예산 (매수 종목 수별)"
        help="매수 종목 수에 따라 한 종목에 배분되는 금액입니다."
      >
        <div className="space-y-2">
          {([["b1", "1종목일 때"], ["b2", "2종목일 때"], ["b3", "3종목일 때"]] as const).map(
            ([k, lbl]) => (
              <div key={k} className="flex items-center gap-2">
                <span className="w-24 text-sm text-muted-foreground">{lbl}</span>
                <Suffixed suffix="원">
                  <Input
                    inputMode="numeric"
                    value={form[k]}
                    onChange={(e) => set(k, comma(e.target.value))}
                    className="h-8 w-40 text-right"
                  />
                </Suffixed>
              </div>
            )
          )}
        </div>
      </Field>
    </div>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label className="text-sm font-medium">{label}</label>
      <p className="text-xs text-muted-foreground">{help}</p>
      <div className="pt-1">{children}</div>
    </div>
  );
}

function Suffixed({ suffix, children }: { suffix: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {children}
      <span className="text-sm text-muted-foreground">{suffix}</span>
    </span>
  );
}
