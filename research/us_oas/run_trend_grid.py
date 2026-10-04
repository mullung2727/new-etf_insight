"""200일선 + OAS 조합 격자 비교 (D13) — stdout 마크다운 표 + RESULTS_TREND_GRID.md 저장.

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_trend_grid.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import load_etf_tr, load_fred, load_index
from research.us_oas import proxy, rules
from research.us_oas import run_hold as _hold
from research.us_oas import run_tqqq_oas as _base

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = _hold.COST
OAS_SERIES = _hold.OAS_SERIES
RESULTS = Path(__file__).resolve().parent / "RESULTS_TREND_GRID.md"
COLS = _hold.COLS
MA_WINDOW = _hold.MA_WINDOW
MA_LEVELS = (0.7, 0.4, 0.2, 0.0)
DEFENSES = ("QQQ", "BIL")
BENCH = ["TQQQ", "QQQ", "OAS"]
STRESS2 = [("2020", "20200219", "20200320"),
           ("2022", "20211119", "20221228"),
           ("2025", "20241216", "20250408")]
STRESS1 = [("2025-03~04", "20250215", "20250531")]


def _grid_name(ma: float, defense: str, oas_on: bool) -> str:
    return f"MA{int(round(ma * 100)):02d}_{defense}_{'OAS' if oas_on else 'noOAS'}"


def _grid_names() -> list[str]:
    return [_grid_name(ma, d, on)
            for ma in MA_LEVELS for d in DEFENSES for on in (False, True)]


def _base_weights(w_oas: pd.Series) -> dict[str, pd.DataFrame]:
    """기준 3개 목표비중. OAS는 run_hold의 OAS와 같은 TQQQ 비중 + BIL 0."""
    idx = w_oas.index
    z = pd.Series(0.0, index=idx)
    o = pd.Series(1.0, index=idx)
    t = w_oas.astype(float)
    return {
        "TQQQ": pd.DataFrame({"TQQQ": o, "QQQ": z, "BIL": z}),
        "QQQ": pd.DataFrame({"TQQQ": z, "QQQ": o, "BIL": z}),
        "OAS": pd.DataFrame({"TQQQ": t, "QQQ": 1.0 - t, "BIL": z}),
    }


def _grid_weights(w_oas: pd.Series, below: pd.Series) -> dict[str, pd.DataFrame]:
    ones = pd.Series(1.0, index=w_oas.index)
    return {_grid_name(ma, d, on):
            rules.combine_weights(w_oas if on else ones, below, ma, d)
            for ma in MA_LEVELS for d in DEFENSES for on in (False, True)}


def _window_ret(ret: pd.Series, dates: list[str], s: str, e: str) -> float:
    win = [d for d in dates if s <= d <= e]
    if not win:
        return float("nan")
    return float((1 + ret.loc[win]).prod() - 1)


def _grid_table(names, runs, weights, dates, stress) -> list[str]:
    head = "| 전략 | CAGR | MDD | Sharpe | Calmar | 평균 TQQQ | 평균 BIL | 연 매매 |"
    for label, _, _ in stress:
        head += f" {label} |"
    lines = [head, "|" + "---|" * (8 + len(stress))]
    for n in names:
        s = perf.summary(runs[n]["ret"])
        avg_t = float(weights[n]["TQQQ"].mean())
        avg_b = float(weights[n]["BIL"].mean())
        tpy = perf.trades_per_year(runs[n]["turnover"])
        row = (f"| {n} | {_base._pct(s['cagr'])} | {_base._pct(s['mdd'])}"
               f" | {_base._num(s['sharpe'])} | {_base._num(s['calmar'])}"
               f" | {_base._pct(avg_t)} | {_base._pct(avg_b)} | {_base._num(tpy, 1)} |")
        for _, st, en in stress:
            row += f" {_base._pct(_window_ret(runs[n]['ret'], dates, st, en))} |"
        lines.append(row)
    return lines


def _pivot_lines(value) -> list[str]:
    """value(이름)->문자열을 ma_tqqq(행) × defense·oas(열 4개) 피벗으로."""
    cols = [("QQQ", False), ("QQQ", True), ("BIL", False), ("BIL", True)]
    labels = ["QQQ_noOAS", "QQQ_OAS", "BIL_noOAS", "BIL_OAS"]
    lines = ["| ma_tqqq | " + " | ".join(labels) + " |",
             "|" + "---|" * (len(labels) + 1)]
    for ma in MA_LEVELS:
        cells = [value(_grid_name(ma, d, on)) for d, on in cols]
        lines.append(f"| {ma:.1f} | " + " | ".join(cells) + " |")
    return lines


def main() -> None:
    oas = load_fred(OAS_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ", "BIL", "HYG", "IEI"])
    vix = load_index("^VIX")
    cal = px[["TQQQ", "QQQ", "HYG", "IEI"]]  # 날짜·신호는 run_hold와 동일 입력으로
    lr = np.log1p(cal[["HYG", "IEI"]])
    features = proxy.make_features(lr, vix)

    start, end = oas.index[0], oas.index[-1]
    coef = proxy.fit_fixed(oas, features, start, end, COLS)
    pairs = proxy.daily_pairs(oas, features, COLS)
    n_pairs = int(((pairs.index >= start) & (pairs.index <= end)).sum())

    # 200일선: QQQ 수익 전체 기간(1999~) 누적수준으로 계산 뒤 백테스트 날짜로 절단
    qqq_ret_full = load_etf_tr(["QQQ"])["QQQ"].dropna()
    qqq_level_full = (1 + qqq_ret_full).cumprod()
    below_full = rules.below_ma(qqq_level_full, MA_WINDOW)

    # ---- 평가 2 기간·신호 (run_hold와 동일 날짜·OAS) ----
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
    a2["oas"] = 4.0  # 수준 조건 끔, 10일 변화 단계 A~D만
    st2 = rules.spec_states(a2)
    st2[a2["d10_bp"].isna()] = "A"
    w_oas2 = st2.map(rules.SPEC_WEIGHTS).astype(float)
    below2 = _hold._below_on(dates2, below_full)
    weights2 = {**_grid_weights(w_oas2, below2), **_base_weights(w_oas2)}
    grid = _grid_names()
    names = grid + BENCH
    runs2 = {n: portfolio.run(weights2[n], rets2, COST) for n in names}

    # ---- 평가 1 기간·신호 (run_hold와 동일 날짜·OAS, 명세 규칙 전체) ----
    dates1 = [d for d in cal.index if d > start]
    rets1 = px.loc[dates1][["TQQQ", "QQQ", "BIL"]]
    if bool(rets1.isna().any().any()):
        raise ValueError("NaN return in period")
    nc1 = proxy.nowcast(oas, features, dates1, COLS)
    st1 = rules.spec_states(nc1)
    w_oas1 = st1.map(rules.SPEC_WEIGHTS).astype(float)
    below1 = _hold._below_on(dates1, below_full)
    weights1 = {**_grid_weights(w_oas1, below1), **_base_weights(w_oas1)}
    runs1 = {n: portfolio.run(weights1[n], rets1, COST) for n in names}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last1, last2 = dates1[-1], dates2[-1]
    coef_s = ", ".join(f"b_{c}={coef[i + 1]:.4f}" for i, c in enumerate(COLS))
    L: list[str] = [
        "# 200일선 + OAS 격자 비교",
        "",
        f"- 생성: {now}",
        f"- 평가 1 기간: {dates1[0]}~{last1} (거래일 {len(dates1)}일)",
        f"- 평가 2 기간: {dates2[0]}~{last2} (거래일 {len(dates2)}일)",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, OAS {oas.index[-1]}, VIX {vix.index[-1]}",
        f"- 추정식 cols: {', '.join(COLS)}; 계수: b0={coef[0]:.4f}, {coef_s}"
        f" (학습 쌍 {n_pairs}개, {start}~{end})",
        f"- 앵커: {anchor_date} OAS {anchor_value:.2f} (평가 2, 수준 조건 끔, Δ10만 사용)",
        f"- QQQ 200일선: {qqq_ret_full.index[0]}~ 누적수준으로 계산 후 백테스트 날짜로 절단",
        "- 16개 조합 전부 표시, 1등 선택 아님, 200일선 길이 고정, 비용 0.1%, 세전",
        "",
    ]

    L += ["## 1. 평가 2 격자표 (16년)",
          "2020=20200219~20200320(코로나), 2022=20211119~20221228, 2025=20241216~20250408.",
          *_grid_table(names, runs2, weights2, dates2, STRESS2),
          ""]

    L += ["## 2. 평가 1 격자표 (3년)",
          "2025-03~04=20250215~20250531.",
          *_grid_table(names, runs1, weights1, dates1, STRESS1),
          ""]

    L += ["## 3. 한눈 요약",
          "### 16년 Sharpe (행 ma_tqqq × 열 defense·oas)",
          *_pivot_lines(lambda n: _base._num(perf.summary(runs2[n]["ret"])["sharpe"])),
          "",
          "### 16년 MDD",
          *_pivot_lines(lambda n: _base._pct(perf.summary(runs2[n]["ret"])["mdd"])),
          "",
          "### 3년 Sharpe",
          *_pivot_lines(lambda n: _base._num(perf.summary(runs1[n]["ret"])["sharpe"])),
          ""]

    def _sharpe2(n: str) -> float:
        v = perf.summary(runs2[n]["ret"])["sharpe"]
        return v if pd.notna(v) else float("-inf")

    top4 = sorted(grid, key=_sharpe2, reverse=True)[:4]
    names4 = BENCH + top4
    L += ["## 4. 연도별 수익 (16년, 기준 3개 + Sharpe 상위 4개)",
          f"상위 4개: {', '.join(top4)}.",
          "| 연도 | " + " | ".join(names4) + " |",
          "|" + "---|" * (len(names4) + 1)]
    y = pd.concat({n: perf.yearly(runs2[n]["ret"]) for n in names4}, axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | " + " | ".join(_base._pct(y.loc[yr, n]) for n in names4) + " |")
    L.append("")

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
