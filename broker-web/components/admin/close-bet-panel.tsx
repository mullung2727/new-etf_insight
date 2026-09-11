"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

// lib/close-bet-config 의 isHms 와 같은 도메인. 그 모듈은 node:fs 를 import 해서
// 클라이언트 번들에 넣으면 빌드가 깨지므로 여기 따로 둔다.
function isHms(v: string): boolean {
  if (!/^\d{2}:\d{2}:\d{2}$/.test(v)) return false;
  const [h, m, s] = v.split(":").map(Number);
  return h <= 23 && m <= 59 && s <= 59;
}

// 저장 포맷은 소수(tp/sl 0~1)·원(cap_max/turnover_min). 화면은 %·억으로 입력/표시하고
// 저장 직전 변환한다. tp/sl 빈칸은 null(장중 익절·손절 안 씀)로 저장된다.
type Form = {
  score_threshold: string; // 점
  tp: string; // %, 빈칸 = 사용 안 함
  sl: string; // %, 빈칸 = 사용 안 함
  cap: string; // 억
  cap_min: string; // %, 빈칸 = 사용 안 함(0)
  tv: string; // 억
  exit_time: string; // HH:MM:SS
  b1: string; // 원
  b2: string;
  b3: string;
};

const EMPTY: Form = {
  score_threshold: "", tp: "", sl: "", cap: "", cap_min: "", tv: "", exit_time: "", b1: "", b2: "", b3: "",
};
const EOK = 100_000_000;

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
    if (v.trim() === "") continue; // 빈칸 = 사용 안 함
    const p = Number(v);
    if (!Number.isFinite(p) || p < 0 || p > 100) return `${label}은 0~100% 사이 값이거나 비어 있어야 합니다`;
  }
  for (const [v, label] of [[f.cap, "시가총액 상한"], [f.tv, "거래대금 하한"]] as const) {
    const amt = digits(v);
    if (!Number.isInteger(amt) || amt <= 0) return `${label}은 0보다 큰 금액이어야 합니다`;
  }
  if (f.cap_min.trim() !== "") {
    const p = Number(f.cap_min);
    if (!Number.isFinite(p) || p < 0 || p >= 100) return "시가총액 하한(하위 %)은 0 이상 100 미만이어야 합니다";
  }
  if (!isHms(f.exit_time)) return "청산 시각은 HH:MM:SS 형식이어야 합니다 (예: 09:01:00)";
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
        tp: c.tp === null ? "" : String(Math.round(c.tp * 100 * 100) / 100), // 0.05 → 5
        sl: c.sl === null ? "" : String(Math.round(c.sl * 100 * 100) / 100),
        cap: comma(String(Math.round(c.cap_max / EOK))), // 원 → 억
        cap_min: c.cap_min_pct ? String(Math.round(c.cap_min_pct * 100 * 100) / 100) : "", // 0.05 → 5
        tv: comma(String(Math.round(c.turnover_min / EOK))),
        exit_time: c.exit_time,
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
      tp: form.tp.trim() === "" ? null : Number(form.tp) / 100,
      sl: form.sl.trim() === "" ? null : Number(form.sl) / 100,
      cap_max: digits(form.cap) * EOK,
      cap_min_pct: form.cap_min.trim() === "" ? 0 : Number(form.cap_min) / 100,
      turnover_min: digits(form.tv) * EOK,
      exit_time: form.exit_time.trim(),
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

      <Field
        label="시가총액 상한"
        help="이 금액 미만인 종목만 매수합니다. 이보다 큰 종목은 후보에서 제외됩니다."
      >
        <Suffixed suffix="억원 미만">
          <Input
            inputMode="numeric"
            value={form.cap}
            onChange={(e) => set("cap", comma(e.target.value))}
            className="h-8 w-32 text-right"
          />
        </Suffixed>
      </Field>

      <Field
        label="시가총액 하한 (전 종목 하위 %)"
        help="전일 전 종목 시가총액 하위 이 비율에 드는 종목은 15시 점수 매기기에서 제외합니다. 금액이 아니라 비율이라 시장 규모를 따라갑니다. 비우면 쓰지 않습니다."
      >
        <Suffixed suffix="% 제외">
          <Input
            type="number"
            value={form.cap_min}
            onChange={(e) => set("cap_min", e.target.value)}
            placeholder="사용 안 함"
            className="h-8 w-32"
          />
        </Suffixed>
      </Field>

      <Field
        label="거래대금 하한 (전일 기준)"
        help="전일 거래대금이 이 금액 이상인 종목만 매수합니다. 매도할 때 못 팔리는 것을 막습니다."
      >
        <Suffixed suffix="억원 이상">
          <Input
            inputMode="numeric"
            value={form.tv}
            onChange={(e) => set("tv", comma(e.target.value))}
            className="h-8 w-32 text-right"
          />
        </Suffixed>
      </Field>

      <Field
        label="청산 시각 (다음 날)"
        help="매수 다음 거래일 이 시각에 보유분을 전량 매도합니다. 형식 HH:MM:SS."
      >
        <Suffixed suffix="(KST)">
          <Input
            value={form.exit_time}
            onChange={(e) => set("exit_time", e.target.value)}
            placeholder="09:01:00"
            className="h-8 w-32"
          />
        </Suffixed>
      </Field>

      <Field label="익절 (수익 실현)" help="이 수익률에 도달하면 장중에 먼저 매도합니다. 비우면 쓰지 않습니다.">
        <Suffixed suffix="%">
          <Input
            type="number"
            value={form.tp}
            onChange={(e) => set("tp", e.target.value)}
            placeholder="사용 안 함"
            className="h-8 w-32"
          />
        </Suffixed>
      </Field>

      <Field label="손절 (손실 제한)" help="이 손실률에 도달하면 장중에 먼저 매도합니다. 비우면 쓰지 않습니다.">
        <Suffixed suffix="%">
          <Input
            type="number"
            value={form.sl}
            onChange={(e) => set("sl", e.target.value)}
            placeholder="사용 안 함"
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
