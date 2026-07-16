"use client";

import { useState } from "react";
import { CompareResponse } from "@/types/dart";

/**
 * RIM(잔여이익모델) 적정가치 패널.
 *
 * 기본식(S-RIM, 초과이익 지속계수 w):
 *   적정가치 = B0 + B0·(ROE−r)·w / (1+r−w)
 * 상세식(5년 순이익 직접입력 → rollforward):
 *   B_t = B_(t-1) + NI_t·(1−배당성향),  RI_t = NI_t − r·B_(t-1)
 *   적정가치 = B0 + Σ RI_t/(1+r)^t + [RI_5·w/(1+r−w)]/(1+r)^5
 *
 * 단위 주의: 재무제표 값(equity/netIncome)은 raw 원, 시가총액(mac)은 억원 → mac×1e8.
 */

const EOK = 1e8; // 억원 → 원

/** 기본 S-RIM 적정가치(원). denom≤0(발산)이면 null. */
export function rimBasic(
  equityWon: number,
  roePct: number,
  rPct: number,
  w: number
): number | null {
  const rf = rPct / 100;
  const denom = 1 + rf - w;
  if (denom <= 0) return null;
  const excess = equityWon * (roePct / 100 - rf);
  return equityWon + (excess * w) / denom;
}

/** 상세 RIM — 5년 순이익(원) rollforward + 지속가치(원). 입력 이상시 null. */
export function rimDetail(
  equityWon: number,
  futureNiWon: number[],
  rPct: number,
  w: number,
  payoutPct: number
): number | null {
  const rf = rPct / 100;
  const denom = 1 + rf - w;
  if (denom <= 0 || futureNiWon.length !== 5) return null;
  const ret = 1 - payoutPct / 100;
  let B = equityWon;
  let pv = 0;
  let ri5 = 0;
  for (let t = 1; t <= 5; t++) {
    const ni = futureNiWon[t - 1];
    if (!Number.isFinite(ni)) return null;
    const ri = ni - rf * B;
    pv += ri / Math.pow(1 + rf, t);
    B = B + ni * ret;
    if (t === 5) ri5 = ri;
  }
  const terminal = (ri5 * w) / denom / Math.pow(1 + rf, 5);
  return equityWon + pv + terminal;
}

function latest(vals: (number | null)[]): number | null {
  for (let i = vals.length - 1; i >= 0; i--) if (vals[i] !== null) return vals[i];
  return null;
}

// 최근 n개 non-null 평균
function avgRecent(vals: (number | null)[], n: number): number | null {
  const got = vals.filter((v): v is number => v !== null).slice(-n);
  return got.length ? got.reduce((a, b) => a + b, 0) / got.length : null;
}

/** 순이익 시계열 평균 성장률(직전값>0 구간만, 적자 왜곡 배제). 데이터 부족시 0. */
export function avgGrowth(vals: (number | null)[]): number {
  const nums = vals.filter((v): v is number => v !== null);
  const rs: number[] = [];
  for (let i = 1; i < nums.length; i++) {
    if (nums[i - 1] > 0) rs.push(nums[i] / nums[i - 1] - 1);
  }
  return rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : 0;
}

function fmtEok(won: number): string {
  const eok = won / EOK;
  if (Math.abs(eok) >= 1e4) return `${(eok / 1e4).toFixed(2)}조`;
  return `${Math.round(eok).toLocaleString()}억`;
}

export default function RimPanel({
  data,
  marketCapWon,
}: {
  data: CompareResponse;
  marketCapWon: number | null;
}) {
  const byKey = new Map(data.rows.map((r) => [r.key, r.values]));
  const equity = latest(byKey.get("totalEquity") ?? []); // B0, 원
  const netIncomes = byKey.get("netIncome") ?? [];
  const roeRow = byKey.get("roe") ?? [];

  // 기본 ROE = 공식 ROE 최근값, 없으면 순이익3년평균/자기자본
  const defaultRoe =
    latest(roeRow) ??
    (equity && avgRecent(netIncomes, 3)
      ? (avgRecent(netIncomes, 3)! / equity) * 100
      : 8);

  // B0(자기자본)도 편집 대상. 기본=재무제표 최근값(억원 단위로 표시/입력).
  const [equityEok, setEquityEok] = useState(
    equity != null ? Math.round(equity / EOK) : 0
  );
  const [roe, setRoe] = useState(Number(defaultRoe.toFixed(1)));
  const [r, setR] = useState(8); // 요구수익률 %
  const [w, setW] = useState(0.9); // 초과이익 지속계수
  const [detail, setDetail] = useState(false);
  const [payout, setPayout] = useState(20); // 배당성향 % (상세)
  // 상세: 5년 미래 순이익(억원). 기본값 = 최근 순이익 유지가정.
  const lastNi = latest(netIncomes);
  const [futureNi, setFutureNi] = useState<string[]>(() =>
    Array.from({ length: 5 }, () =>
      lastNi != null ? String(Math.round(lastNi / EOK)) : ""
    )
  );
  // 성장률(%) — 재무제표 순이익 평균 성장률. 수정/초기화 가능, 반영 시 5년치 자동입력.
  // ponytail: 시계열 단위(연/분기)를 그대로 연 성장률로 씀 — 연간 모드 기준.
  const defaultGrowth = Number((avgGrowth(netIncomes) * 100).toFixed(1));
  const [growth, setGrowth] = useState(defaultGrowth);

  // 최근 순이익 × (1+g)^t 로 5년치 채움.
  function applyGrowth() {
    if (lastNi == null) return;
    const g = growth / 100;
    setFutureNi(
      Array.from({ length: 5 }, (_, t) =>
        String(Math.round((lastNi / EOK) * Math.pow(1 + g, t + 1)))
      )
    );
  }

  if (equity == null) {
    return (
      <div className="mb-6 px-6 py-4 bg-card border border-fin-gold/25 rounded-[2px] text-fin-muted text-fin-sm">
        RIM 계산 불가 — 자기자본 데이터 없음
      </div>
    );
  }

  const b0 = equityEok * EOK; // 편집된 자기자본(원)
  const rf = r / 100;
  const denom = 1 + rf - w; // 지속계수 분모, ≤0이면 발산

  // 근거용 중간값 — rimBasic/rimDetail(테스트 대상)과 동일식. ponytail: 전개 표시 위해 인라인 재계산.
  const excess = b0 * (roe / 100 - rf); // 초과이익
  const excessValue = denom > 0 ? (excess * w) / denom : null; // 초과이익 지속가치
  const fairBasic = excessValue != null ? b0 + excessValue : null;

  // 상세 연도별 전개
  const ret = 1 - payout / 100;
  const detailRows: { t: number; bPrev: number; ni: number; ri: number; pv: number }[] = [];
  let detTerminal: number | null = null;
  let fairDetail: number | null = null;
  {
    let B = b0;
    let pvSum = 0;
    let ri5 = 0;
    let ok = denom > 0;
    for (let t = 1; t <= 5; t++) {
      const ni = Number(futureNi[t - 1]) * EOK;
      if (!Number.isFinite(ni)) { ok = false; break; }
      const ri = ni - rf * B;
      const pv = ri / Math.pow(1 + rf, t);
      detailRows.push({ t, bPrev: B, ni, ri, pv });
      pvSum += pv;
      B = B + ni * ret;
      if (t === 5) ri5 = ri;
    }
    if (ok && detailRows.length === 5) {
      detTerminal = (ri5 * w) / denom / Math.pow(1 + rf, 5);
      fairDetail = b0 + pvSum + detTerminal;
    }
  }

  const fairWon = detail ? fairDetail : fairBasic;
  const gap = fairWon != null && marketCapWon ? fairWon / marketCapWon - 1 : null;

  return (
    <div className="mb-6 bg-card border border-fin-gold/25 rounded-[2px] overflow-hidden font-terminal">
      {/* 헤더 + 모드 토글 */}
      <div className="flex items-center justify-between px-6 pt-4 pb-3 border-b border-fin-gold/[0.12]">
        <div className="flex items-center gap-2">
          <div
            className="w-[6px] h-[6px] rounded-full bg-fin-gold"
            style={{ boxShadow: "0 0 8px var(--color-fin-gold)" }}
          />
          <span className="text-fin-gold text-fin-sm tracking-[0.15em] font-semibold">
            RIM 적정가치 산정
          </span>
        </div>
        <label className="flex items-center gap-1.5 text-fin-xs text-fin-muted cursor-pointer select-none">
          <input
            type="checkbox"
            checked={detail}
            onChange={(e) => setDetail(e.target.checked)}
            className="accent-fin-gold"
          />
          상세(5년 순이익)
        </label>
      </div>

      {/* 산정 근거 전개 */}
      <div className="px-6 py-4 flex flex-col">
        {!detail ? (
          <>
            <Row label="자기자본 B0">
              <NumInput value={equityEok} onChange={setEquityEok} step={100} suffix="억" />
            </Row>
            <Row label="예상 ROE">
              <NumInput value={roe} onChange={setRoe} step={0.1} suffix="%" />
            </Row>
            <Row label="요구수익률 r">
              <NumInput value={r} onChange={setR} step={0.5} suffix="%" />
            </Row>
            <Row label="초과이익 = B0×(ROE−r)" derived>
              <span className="tabular-nums text-fin-text">{fmtEok(excess)}</span>
            </Row>
            <Row label="지속계수 w">
              <NumInput value={w} onChange={setW} step={0.1} min={0} max={1} />
            </Row>
            <Row label="초과이익 지속가치 = 초과이익×w/(1+r−w)" derived>
              <span className="tabular-nums text-fin-text">
                {excessValue != null ? fmtEok(excessValue) : "—"}
              </span>
            </Row>
          </>
        ) : (
          <>
            <Row label="자기자본 B0">
              <NumInput value={equityEok} onChange={setEquityEok} step={100} suffix="억" />
            </Row>
            <Row label="요구수익률 r">
              <NumInput value={r} onChange={setR} step={0.5} suffix="%" />
            </Row>
            <Row label="지속계수 w">
              <NumInput value={w} onChange={setW} step={0.1} min={0} max={1} />
            </Row>
            <Row label="배당성향(유보 rollforward)">
              <NumInput value={payout} onChange={setPayout} step={5} min={0} max={100} suffix="%" />
            </Row>

            {/* 순이익 성장률 → 5년치 자동입력 */}
            <div className="flex items-center justify-between py-1.5 border-b border-white/[0.04]">
              <span className="text-fin-sm tracking-[0.03em] text-fin-label">
                순이익 성장률(평균)
              </span>
              <div className="flex items-center gap-2">
                <NumInput value={growth} onChange={setGrowth} step={1} suffix="%" />
                <button
                  type="button"
                  onClick={() => setGrowth(defaultGrowth)}
                  className="px-2 py-1 text-fin-xs text-fin-muted border border-white/10 rounded-[2px] hover:text-fin-text hover:border-fin-gold/30"
                >
                  초기화
                </button>
                <button
                  type="button"
                  onClick={applyGrowth}
                  disabled={lastNi == null}
                  className="px-3 py-1 text-fin-xs text-fin-gold border border-fin-gold/40 rounded-[2px] hover:bg-fin-gold/10 disabled:opacity-30"
                >
                  반영
                </button>
              </div>
            </div>

            {/* 연도별 순이익 입력 + RI 전개 */}
            <div className="mt-2 -mx-2 overflow-x-auto">
              <table className="w-full text-fin-sm">
                <thead>
                  <tr className="text-fin-muted text-fin-xs tracking-[0.1em]">
                    <th className="text-left px-2 py-1 font-normal">연차</th>
                    <th className="text-right px-2 py-1 font-normal">순이익(억)</th>
                    <th className="text-right px-2 py-1 font-normal">기초자본</th>
                    <th className="text-right px-2 py-1 font-normal">잔여이익 RI</th>
                    <th className="text-right px-2 py-1 font-normal">현재가치</th>
                  </tr>
                </thead>
                <tbody>
                  {[0, 1, 2, 3, 4].map((i) => {
                    const dr = detailRows[i];
                    return (
                      <tr key={i} className="border-t border-white/[0.05]">
                        <td className="px-2 py-1 text-fin-label">{i + 1}년차</td>
                        <td className="px-2 py-1 text-right">
                          <input
                            type="number"
                            value={futureNi[i]}
                            onChange={(e) => {
                              const next = [...futureNi];
                              next[i] = e.target.value;
                              setFutureNi(next);
                            }}
                            className="w-20 bg-black/40 border border-fin-gold/20 rounded-[2px] px-2 py-0.5 text-fin-text text-fin-sm tabular-nums text-right outline-none focus:border-fin-gold/50"
                          />
                        </td>
                        <td className="px-2 py-1 text-right tabular-nums text-fin-ghost">
                          {dr ? fmtEok(dr.bPrev) : "—"}
                        </td>
                        <td className="px-2 py-1 text-right tabular-nums text-fin-text">
                          {dr ? fmtEok(dr.ri) : "—"}
                        </td>
                        <td className="px-2 py-1 text-right tabular-nums text-fin-text">
                          {dr ? fmtEok(dr.pv) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-fin-ghost text-fin-xs leading-relaxed tracking-[0.02em]">
              RI = 순이익 − r×기초자본 · 기초자본ₜ = 기초자본₍ₜ₋₁₎ + 순이익×(1−배당성향) · 현재가치 = RI ÷ (1+r)ᵗ
            </p>
            <Row label="6년차 이후 지속가치 = RI₅×w/(1+r−w) ÷ (1+r)⁵" derived>
              <span className="tabular-nums text-fin-text">
                {detTerminal != null ? fmtEok(detTerminal) : "—"}
              </span>
            </Row>
          </>
        )}

        {/* 적정가치 */}
        <div className="flex items-center justify-between mt-3 pt-3 border-t border-fin-gold/[0.2]">
          <span className="text-fin-gold text-fin-sm tracking-[0.1em] font-semibold">
            {detail ? "적정가치 = B0 + Σ현재가치 + 지속가치" : "적정가치 = B0 + 지속가치"}
          </span>
          <span className="text-fin-gold text-fin-lg tabular-nums font-bold">
            {fairWon != null ? fmtEok(fairWon) : "—"}
          </span>
        </div>
      </div>

      {denom <= 0 && (
        <div className="px-6 pb-3 text-down text-fin-xs">
          지속계수 w가 (1+r)보다 큼 → 발산. w를 낮추거나 r을 높여줘.
        </div>
      )}

      {/* 시총 대비 괴리율 */}
      <div className="flex flex-wrap items-center gap-x-8 gap-y-2 px-6 py-3 border-t border-fin-gold/[0.12] bg-black/[0.15]">
        <div className="flex items-center gap-2">
          <span className="text-fin-muted text-fin-xs tracking-[0.1em]">시가총액</span>
          <span className="text-fin-text text-fin-sm tabular-nums">
            {marketCapWon ? fmtEok(marketCapWon) : "—"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-fin-muted text-fin-xs tracking-[0.1em]">괴리율</span>
          <span
            className={[
              "text-fin-sm tabular-nums font-semibold",
              gap == null ? "text-fin-ghost" : gap > 0 ? "text-up" : "text-down",
            ].join(" ")}
          >
            {gap == null ? "—" : `${gap > 0 ? "+" : ""}${(gap * 100).toFixed(1)}%`}
          </span>
          {gap != null && (
            <span className="text-fin-xs tracking-[0.1em] text-fin-muted">
              {gap > 0 ? "저평가" : "고평가"}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/** 근거 전개 한 줄: 좌 라벨 · 우 값(입력 또는 파생). */
function Row({
  label,
  derived,
  children,
}: {
  label: string;
  derived?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-white/[0.04]">
      <span
        className={[
          "text-fin-sm tracking-[0.03em]",
          derived ? "text-fin-muted" : "text-fin-label",
        ].join(" ")}
      >
        {label}
      </span>
      <div className="text-fin-sm">{children}</div>
    </div>
  );
}

function NumInput({
  value,
  onChange,
  step = 1,
  min,
  max,
  suffix,
}: {
  value: number;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
  suffix?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        max={max}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
        className="w-28 bg-black/40 border border-fin-gold/20 rounded-[2px] px-2 py-1 text-fin-text text-fin-sm tabular-nums text-right outline-none focus:border-fin-gold/50"
      />
      {suffix && <span className="text-fin-ghost text-fin-xs w-4">{suffix}</span>}
    </span>
  );
}
