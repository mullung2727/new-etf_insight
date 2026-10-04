"""200일선 + Fast stress 조합 비교 (D15).

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_combo.py
stdout 마크다운 표 + RESULTS_COMBO.md 저장. 200일선 고정·fast 문서값 그대로, 튜닝 없음.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import load_etf_tr, load_fred, load_index
from research.us_oas import defense, proxy, rules
from research.us_oas import run_defense as _def
from research.us_oas import run_hold as _hold
from research.us_oas import run_tqqq_oas as _base
from research.us_oas import run_trend_grid as _trend

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
OAS_SERIES = _hold.OAS_SERIES
DFII_SERIES = "DFII10"
RESULTS = Path(__file__).resolve().parent / "RESULTS_COMBO.md"
COLS = _hold.COLS
MA_WINDOW = _hold.MA_WINDOW
NAMES = ["TQQQ", "QQQ", "MA40", "MA20", "MA40+FAST", "MA20+FAST",
         "MA40+FAST2", "MA20+FAST2", "FAST", "FAST2", "DOC_FULL"]
YNAMES = ["TQQQ", "MA40", "MA40+FAST", "MA40+FAST2", "DOC_FULL"]


def _combo_df(tqqq: pd.Series) -> pd.DataFrame:
    """TQQQ Series → BIL 없음 목표비중표 (QQQ=1-TQQQ, BIL=0)."""
    t = tqqq.astype(float)
    z = pd.Series(0.0, index=t.index)
    return pd.DataFrame({"TQQQ": t, "QQQ": 1.0 - t, "BIL": z}, index=t.index)


def _combo_table(names, runs, weights, dates, stress) -> list[str]:
    """요약표: CAGR·MDD·Sharpe·Calmar·평균TQQQ·연매매 + 구간수익. _trend._window_ret 재사용."""
    head = "| 전략 | CAGR | MDD | Sharpe | Calmar | 평균 TQQQ | 연 매매 |"
    for label, _, _ in stress:
        head += f" {label} |"
    lines = [head, "|" + "---|" * (7 + len(stress))]
    for n in names:
        s = perf.summary(runs[n]["ret"])
        avg_t = float(weights[n]["TQQQ"].mean())
        tpy = perf.trades_per_year(runs[n]["turnover"])
        row = (f"| {n} | {_base._pct(s['cagr'])} | {_base._pct(s['mdd'])}"
               f" | {_base._num(s['sharpe'])} | {_base._num(s['calmar'])}"
               f" | {_base._pct(avg_t)} | {_base._num(tpy, 1)} |")
        for _, st, en in stress:
            row += f" {_base._pct(_trend._window_ret(runs[n]['ret'], dates, st, en))} |"
        lines.append(row)
    return lines


def _build_weights(below: pd.Series, fast_doc: pd.Series, fast_2plus: pd.Series,
                   w_oas_full: pd.Series, ind: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """11개 전략 목표비중. MA 단독은 D13과 같은 combine_weights, 조합은 min(ma_cap, fast)."""
    idx = below.index
    z = pd.Series(0.0, index=idx)
    o = pd.Series(1.0, index=idx)
    ma40_cap = pd.Series(np.where(below.to_numpy(dtype=bool), 0.4, 1.0), index=idx)
    ma20_cap = pd.Series(np.where(below.to_numpy(dtype=bool), 0.2, 1.0), index=idx)

    def _min(a: pd.Series, b: pd.Series) -> pd.Series:
        return pd.concat([a, b], axis=1).min(axis=1)

    return {
        "TQQQ": pd.DataFrame({"TQQQ": o, "QQQ": z, "BIL": z}, index=idx),
        "QQQ": pd.DataFrame({"TQQQ": z, "QQQ": o, "BIL": z}, index=idx),
        "MA40": rules.combine_weights(o, below, 0.4, "QQQ"),
        "MA20": rules.combine_weights(o, below, 0.2, "QQQ"),
        "MA40+FAST": _combo_df(_min(ma40_cap, fast_doc)),
        "MA20+FAST": _combo_df(_min(ma20_cap, fast_doc)),
        "MA40+FAST2": _combo_df(_min(ma40_cap, fast_2plus)),
        "MA20+FAST2": _combo_df(_min(ma20_cap, fast_2plus)),
        "FAST": _combo_df(fast_doc),
        "FAST2": _combo_df(fast_2plus),
        "DOC_FULL": defense.final_weights(w_oas_full, ind),
    }


def _fast_2plus(fast_doc: pd.Series) -> pd.Series:
    """fast_doc에서 0.7만 1.0으로 (2개 이상 0.5 유지)."""
    out = fast_doc.astype(float).copy()
    out[fast_doc == 0.7] = 1.0
    return out


def main() -> None:
    oas = load_fred(OAS_SERIES)
    dfii = load_fred(DFII_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ", "BIL", "HYG", "IEI"])
    vix = load_index("^VIX")
    vix3m = load_index("^VIX3M")
    cal = px[["TQQQ", "QQQ", "HYG", "IEI"]]  # 날짜·신호는 run_trend_grid와 동일 입력으로
    lr = np.log1p(cal[["HYG", "IEI"]])
    features = proxy.make_features(lr, vix)

    start, end = oas.index[0], oas.index[-1]
    coef = proxy.fit_fixed(oas, features, start, end, COLS)
    pairs = proxy.daily_pairs(oas, features, COLS)
    n_pairs = int(((pairs.index >= start) & (pairs.index <= end)).sum())

    qqq_ret_full = load_etf_tr(["QQQ"])["QQQ"].dropna()
    qqq_level_full = (1 + qqq_ret_full).cumprod()
    below_full = rules.below_ma(qqq_level_full, MA_WINDOW)

    # ---- 16년 기간·신호 (run_trend_grid와 동일 날짜·앵커) ----
    valid = cal.notna().all(axis=1)
    first = valid[valid].index[0]
    dates2 = [d for d in cal.index if d >= first]
    rets2 = px.loc[dates2][["TQQQ", "QQQ", "BIL"]]
    if bool(rets2.isna().any().any()):
        raise ValueError("NaN return in period")
    obs_set = set(oas.index)
    if start in dates2 and start in obs_set:
        anchor_date = start
        anchor_value = float(oas.loc[start])
    else:
        cands = [d for d in dates2 if d >= start and d in obs_set]
        if not cands:
            raise ValueError("no anchor trading day with OAS obs")
        anchor_date = cands[0]
        anchor_value = float(oas.loc[anchor_date])
    a2 = proxy.etf_signal(features, coef, anchor_date, anchor_value,
                          dates2, window=10, cols=COLS)
    ind2 = defense.indicators(qqq_level_full, qqq_ret_full, vix, vix3m, dfii, dates2)
    below2 = _hold._below_on(dates2, below_full)
    fast_doc2 = defense.fast_cap(ind2)
    fast_2p2 = _fast_2plus(fast_doc2)
    w_oas2 = _def._w_oas_etf(a2, 2)
    weights2 = _build_weights(below2, fast_doc2, fast_2p2, w_oas2, ind2)
    runs2 = {n: portfolio.run(weights2[n], rets2, COST) for n in NAMES}

    # ---- 3년 기간·신호 (run_nowcast와 동일 날짜, 실OAS 구간) ----
    dates1 = [d for d in cal.index if d > start]
    rets1 = px.loc[dates1][["TQQQ", "QQQ", "BIL"]]
    if bool(rets1.isna().any().any()):
        raise ValueError("NaN return in period")
    ind1 = defense.indicators(qqq_level_full, qqq_ret_full, vix, vix3m, dfii, dates1)
    below1 = _hold._below_on(dates1, below_full)
    fast_doc1 = defense.fast_cap(ind1)
    fast_2p1 = _fast_2plus(fast_doc1)
    w_lag2 = rules.spec_states(_def._align_oas_lag(oas, dates1, 2)).map(
        rules.SPEC_WEIGHTS).astype(float)
    weights1 = _build_weights(below1, fast_doc1, fast_2p1, w_lag2, ind1)
    runs1 = {n: portfolio.run(weights1[n], rets1, COST) for n in NAMES}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last1, last2 = dates1[-1], dates2[-1]
    coef_s = ", ".join(f"b_{c}={coef[i + 1]:.4f}" for i, c in enumerate(COLS))
    L: list[str] = [
        "# 200일선 + Fast stress 조합 비교",
        "",
        f"- 생성: {now}",
        f"- 16년 기간: {dates2[0]}~{last2} (거래일 {len(dates2)}일)",
        f"- 3년 기간: {dates1[0]}~{last1} (거래일 {len(dates1)}일)",
        "- 임계값: 200일선 고정, fast 는 외부 문서 값 그대로, 비용 0.1%, 세전",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, OAS {oas.index[-1]}, VIX {vix.index[-1]}",
        f"- VIX3M 첫 날짜: {vix3m.index[0]}, DFII10 첫 날짜: {dfii.index[0]}",
        f"- 추정식 cols: {', '.join(COLS)}; 계수: b0={coef[0]:.4f}, {coef_s}"
        f" (학습 쌍 {n_pairs}개, {start}~{end})",
        f"- 앵커: {anchor_date} OAS {anchor_value:.2f} (16년 DOC_FULL, 수준 조건 끔 oas=4.0, Δ10만)",
        f"- QQQ 200MA: {qqq_ret_full.index[0]}~ 누적수준으로 계산 후 절단, 당일 종가 기준",
        "- fast: 방어지표 T-1 shift, 문서 임계값 그대로 (1개→0.7, 2개+→0.5)",
        "- DOC_FULL: run_defense FULL과 동일, oas_lag 2 (16년 ETF 추정·3년 실OAS)",
        "- 조합 TQQQ=min(ma_cap, fast), 나머지 QQQ, BIL 없음 (DOC_FULL만 severe BIL)",
        "",
    ]

    L += ["## 1. 16년 요약 (비용 0.1%)",
          "2020=20200219~20200320(코로나), 2022=20211119~20221228,"
          " 2025=20241216~20250408 (run_trend_grid와 같은 구간).",
          *_combo_table(NAMES, runs2, weights2, dates2, _trend.STRESS2),
          ""]

    L += ["## 2. 3년 요약 (실OAS 구간, 비용 0.1%)",
          "2025-03~04=20250215~20250531."
          " DOC_FULL은 실OAS lag 2 (run_defense FULL_lag2와 동일).",
          *_combo_table(NAMES, runs1, weights1, dates1, _trend.STRESS1),
          ""]

    L += ["## 3. 연도별 수익 (16년)",
          "| 연도 | " + " | ".join(YNAMES) + " |",
          "|" + "---|" * (len(YNAMES) + 1)]
    y = pd.concat({n: perf.yearly(runs2[n]["ret"]) for n in YNAMES}, axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | "
                 + " | ".join(_base._pct(y.loc[yr, n]) for n in YNAMES) + " |")
    L.append("")

    def _cnt(s: pd.Series, v: float) -> int:
        return int((s == v).sum())

    n2, n1 = len(dates2), len(dates1)
    L += ["## 4. fast 발동 일수",
          "| 구간 | 전체 | fast_doc 0.7 | fast_doc 0.5 | fast_2plus 0.5 |",
          "|---|---|---|---|---|",
          f"| 16년 | {n2} | {_cnt(fast_doc2, 0.7)} ({_base._pct(_cnt(fast_doc2, 0.7) / n2)})"
          f" | {_cnt(fast_doc2, 0.5)} ({_base._pct(_cnt(fast_doc2, 0.5) / n2)})"
          f" | {_cnt(fast_2p2, 0.5)} ({_base._pct(_cnt(fast_2p2, 0.5) / n2)}) |",
          f"| 3년 | {n1} | {_cnt(fast_doc1, 0.7)} ({_base._pct(_cnt(fast_doc1, 0.7) / n1)})"
          f" | {_cnt(fast_doc1, 0.5)} ({_base._pct(_cnt(fast_doc1, 0.5) / n1)})"
          f" | {_cnt(fast_2p1, 0.5)} ({_base._pct(_cnt(fast_2p1, 0.5) / n1)}) |",
          ""]

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
