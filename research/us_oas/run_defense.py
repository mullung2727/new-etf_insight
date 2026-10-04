"""외부 제안 "TQQQ 방어전략" 그대로 재현 + 블록별 제거 비교 (D14).

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_defense.py
stdout 마크다운 표 + RESULTS_DEFENSE.md 저장. 임계값은 제안 문서 그대로, 튜닝 없음.
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
from research.us_oas import run_hold as _hold
from research.us_oas import run_tqqq_oas as _base
from research.us_oas import run_trend_grid as _trend

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
COST_DOC = 0.0005
OAS_SERIES = _hold.OAS_SERIES
DFII_SERIES = "DFII10"
RESULTS = Path(__file__).resolve().parent / "RESULTS_DEFENSE.md"
COLS = _hold.COLS
MA_WINDOW = _hold.MA_WINDOW
LAGS_16Y = (0, 2)
NAMES_16Y = ["FULL", "-FAST", "-PERSIST", "-REALRATE", "-SEVERE", "-OAS",
             "TQQQ", "QQQ", "MA40_QQQ", "OAS"]
NAMES_3Y = ["FULL_lag1", "FULL_lag2", "FULL_nowcast", "-OAS",
            "TQQQ", "QQQ", "MA40_QQQ"]


def _w_oas_etf(a2: pd.DataFrame, lag: int) -> pd.Series:
    """16년 OAS 비중: ETF 추정 d10을 거래일 기준 lag칸 shift 후 명세 상태 (수준 끔)."""
    d = a2["d10_bp"].shift(lag)
    frame = pd.DataFrame({"oas": 4.0, "d10_bp": d}, index=a2.index)
    st = rules.spec_states(frame)
    st[d.isna()] = "A"
    return st.map(rules.SPEC_WEIGHTS).astype(float)


def _align_oas_lag(oas: pd.Series, dates, lag: int) -> pd.DataFrame:
    """3년 실OAS 정렬: T=dates[i]에 관측일 ≤ dates[i-lag] 마지막 관측. 명세 전체용."""
    oas = oas.sort_index()
    obs = oas.index.to_numpy()
    vals = oas.to_numpy(dtype=float)
    dates = list(dates)
    rows = []
    for i, _t in enumerate(dates):
        if i < lag:
            rows.append((None, np.nan, np.nan))
            continue
        j = int(np.searchsorted(obs, dates[i - lag], side="right") - 1)
        if j < 0:
            rows.append((None, np.nan, np.nan))
            continue
        d10 = (vals[j] - vals[j - 10]) * 100 if j >= 10 else np.nan
        rows.append((obs[j], vals[j], d10))
    return pd.DataFrame(rows, index=dates, columns=["oas_date", "oas", "d10_bp"])


def _defense_weights(w_oas: pd.Series, ind: pd.DataFrame) -> dict[str, pd.DataFrame]:
    ones = pd.Series(1.0, index=w_oas.index)
    return {
        "FULL": defense.final_weights(w_oas, ind),
        "-FAST": defense.final_weights(w_oas, ind, use_fast=False),
        "-PERSIST": defense.final_weights(w_oas, ind, use_persistent=False),
        "-REALRATE": defense.final_weights(w_oas, ind, use_real_rate=False),
        "-SEVERE": defense.final_weights(w_oas, ind, use_severe=False),
        "-OAS": defense.final_weights(ones, ind),
    }


def _bench_weights(w_oas: pd.Series, below: pd.Series) -> dict[str, pd.DataFrame]:
    idx = w_oas.index
    z = pd.Series(0.0, index=idx)
    o = pd.Series(1.0, index=idx)
    t = w_oas.astype(float)
    return {
        "TQQQ": pd.DataFrame({"TQQQ": o, "QQQ": z, "BIL": z}, index=idx),
        "QQQ": pd.DataFrame({"TQQQ": z, "QQQ": o, "BIL": z}, index=idx),
        "MA40_QQQ": rules.combine_weights(o, below, 0.4, "QQQ"),
        "OAS": pd.DataFrame({"TQQQ": t, "QQQ": 1.0 - t, "BIL": z}, index=idx),
    }


def _dpp(x) -> str:
    return "-" if pd.isna(x) else f"{x:+.1f}pp"


def _dnum(x) -> str:
    return "-" if pd.isna(x) else f"{x:+.2f}"


def main() -> None:
    oas = load_fred(OAS_SERIES)
    dfii = load_fred(DFII_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ", "BIL", "HYG", "IEI"])
    vix = load_index("^VIX")
    vix3m = load_index("^VIX3M")
    cal = px[["TQQQ", "QQQ", "HYG", "IEI"]]  # 날짜·신호는 run_hold와 동일 입력으로
    lr = np.log1p(cal[["HYG", "IEI"]])
    features = proxy.make_features(lr, vix)

    start, end = oas.index[0], oas.index[-1]
    coef = proxy.fit_fixed(oas, features, start, end, COLS)
    pairs = proxy.daily_pairs(oas, features, COLS)
    n_pairs = int(((pairs.index >= start) & (pairs.index <= end)).sum())

    qqq_ret_full = load_etf_tr(["QQQ"])["QQQ"].dropna()
    qqq_level_full = (1 + qqq_ret_full).cumprod()
    below_full = rules.below_ma(qqq_level_full, MA_WINDOW)

    # ---- 16년 기간·신호 (run_hold 평가 2와 동일 날짜·앵커) ----
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

    per_lag = {}
    for lag in LAGS_16Y:
        w_oas = _w_oas_etf(a2, lag)
        weights = {**_defense_weights(w_oas, ind2), **_bench_weights(w_oas, below2)}
        runs = {n: portfolio.run(weights[n], rets2, COST) for n in NAMES_16Y}
        per_lag[lag] = {"weights": weights, "runs": runs,
                        "run_doc": portfolio.run(weights["FULL"], rets2, COST_DOC)}

    # ---- 3년 기간·신호 (실OAS, 명세 전체) ----
    dates1 = [d for d in cal.index if d > start]
    rets1 = px.loc[dates1][["TQQQ", "QQQ", "BIL"]]
    if bool(rets1.isna().any().any()):
        raise ValueError("NaN return in period")
    ind1 = defense.indicators(qqq_level_full, qqq_ret_full, vix, vix3m, dfii, dates1)
    below1 = _hold._below_on(dates1, below_full)
    ones1 = pd.Series(1.0, index=pd.Index(dates1))
    w_lag1 = rules.spec_states(_align_oas_lag(oas, dates1, 1)).map(rules.SPEC_WEIGHTS).astype(float)
    w_lag2 = rules.spec_states(_align_oas_lag(oas, dates1, 2)).map(rules.SPEC_WEIGHTS).astype(float)
    w_now = rules.spec_states(proxy.nowcast(oas, features, dates1, COLS)).map(
        rules.SPEC_WEIGHTS).astype(float)
    b1 = _bench_weights(w_lag1, below1)
    weights1 = {
        "FULL_lag1": defense.final_weights(w_lag1, ind1),
        "FULL_lag2": defense.final_weights(w_lag2, ind1),
        "FULL_nowcast": defense.final_weights(w_now, ind1),
        "-OAS": defense.final_weights(ones1, ind1),
        "TQQQ": b1["TQQQ"], "QQQ": b1["QQQ"], "MA40_QQQ": b1["MA40_QQQ"],
    }
    runs1 = {n: portfolio.run(weights1[n], rets1, COST) for n in NAMES_3Y}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last1, last2 = dates1[-1], dates2[-1]
    coef_s = ", ".join(f"b_{c}={coef[i + 1]:.4f}" for i, c in enumerate(COLS))
    L: list[str] = [
        "# 외부 제안 TQQQ 방어전략 재현 + 블록 제거 비교",
        "",
        f"- 생성: {now}",
        "- 외부 제안 임계값 그대로·튜닝 없음",
        f"- 16년 기간: {dates2[0]}~{last2} (거래일 {len(dates2)}일)",
        f"- 3년 기간: {dates1[0]}~{last1} (거래일 {len(dates1)}일)",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, OAS {oas.index[-1]}, VIX {vix.index[-1]}",
        f"- VIX3M 첫 날짜: {vix3m.index[0]}, DFII10 첫 날짜: {dfii.index[0]}",
        f"- 추정식 cols: {', '.join(COLS)}; 계수: b0={coef[0]:.4f}, {coef_s}"
        f" (학습 쌍 {n_pairs}개, {start}~{end})",
        f"- 앵커: {anchor_date} OAS {anchor_value:.2f} (16년, 수준 조건 끔 oas=4.0, Δ10만)",
        f"- QQQ 200MA: {qqq_ret_full.index[0]}~ 누적수준으로 계산 후 절단, 방어지표는 T-1 shift",
        "- 비용 0.1% 기본·FULL만 문서 비용 0.05% 추가, 세전",
        "",
        "## 해석 가정",
        "- 판단일 T 종가에 리밸런싱, 방어지표는 T-1까지 값 (shift(1))",
        "- RV20 = QQQ 일간수익 20일 표준편차(ddof=1)×√252",
        "- 200MA·mom20 = QQQ 총수익 누적수준 기준, mom20 = 수준[t]/수준[t-20]−1",
        "- VIX/VIX3M 종가 비율, VIX3M 결측이면 (b) 거짓",
        "- DFII10 20일 변화 = (최근 관측−20관측 전)×100bp, T에는 관측일<T 마지막 값",
        "- 위험 슬리브 TQQQ = min(OAS, fast, persistent), severe면 절반 BIL",
        "- 16년 oas_lag: ETF 추정 d10을 거래일 기준 lag칸 shift (0=당일, 2=D+2)",
        "- 3년 oas_lag: T에 관측일 ≤ T−lag 거래일 마지막 실OAS, nowcast=당일 추정",
        "",
    ]

    L += ["## 1. 16년 요약 (비용 0.1%)",
          "2020=20200219~20200320(코로나), 2022=20211119~20221228,"
          " 2025=20241216~20250408 (run_trend_grid와 같은 구간).",
          "",
          "### oas_lag 0",
          *_trend._grid_table(NAMES_16Y, per_lag[0]["runs"], per_lag[0]["weights"],
                              dates2, _trend.STRESS2),
          "",
          "### oas_lag 2",
          *_trend._grid_table(NAMES_16Y, per_lag[2]["runs"], per_lag[2]["weights"],
                              dates2, _trend.STRESS2),
          ""]

    L += ["## 2. FULL 비용 비교 (0.05% 문서 vs 0.1% 기준)",
          "| lag | 비용 | CAGR | MDD | Sharpe | 최종자산 |",
          "|---|---|---|---|---|---|"]
    for lag in LAGS_16Y:
        for label, run in (("0.05%", per_lag[lag]["run_doc"]),
                           ("0.1%", per_lag[lag]["runs"]["FULL"])):
            s = perf.summary(run["ret"])
            L.append(f"| {lag} | {label} | {_base._pct(s['cagr'])} | {_base._pct(s['mdd'])}"
                     f" | {_base._num(s['sharpe'])} | {_base._num(s['final'])} |")
    L.append("")

    L += ["## 3. 블록 기여 (FULL 대비 제거 변형 차이, 비용 0.1%)",
          "Δ = 제거 변형 − FULL. CAGR·MDD는 %p, Sharpe는 절대차."
          " 음수 = FULL보다 나쁨 (MDD 음수 = 더 깊은 낙폭).",
          ""]
    for lag in LAGS_16Y:
        L += [f"### oas_lag {lag}",
              "| 제거 | ΔCAGR | ΔMDD | ΔSharpe |",
              "|---|---|---|---|"]
        sf = perf.summary(per_lag[lag]["runs"]["FULL"]["ret"])
        for n in ["-FAST", "-PERSIST", "-REALRATE", "-SEVERE", "-OAS"]:
            s = perf.summary(per_lag[lag]["runs"][n]["ret"])
            L.append(f"| {n} | {_dpp((s['cagr'] - sf['cagr']) * 100)}"
                     f" | {_dpp((s['mdd'] - sf['mdd']) * 100)}"
                     f" | {_dnum(s['sharpe'] - sf['sharpe'])} |")
        L.append("")

    L += ["## 4. 3년 요약 (실OAS, 비용 0.1%)",
          "-OAS는 OAS 미사용이라 lag/nowcast 불변 (한 줄)."
          " 2025-03~04=20250215~20250531.",
          *_trend._grid_table(NAMES_3Y, runs1, weights1, dates1, _trend.STRESS1),
          ""]

    L += ["## 5. 연도별 수익 (16년, oas_lag 2)",
          "| 연도 | TQQQ | MA40_QQQ | FULL |",
          "|---|---|---|---|"]
    ynames = ["TQQQ", "MA40_QQQ", "FULL"]
    y = pd.concat({n: perf.yearly(per_lag[2]["runs"][n]["ret"]) for n in ynames},
                  axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | "
                 + " | ".join(_base._pct(y.loc[yr, n]) for n in ynames) + " |")
    L.append("")

    fc2 = defense.fast_cap(ind2)
    pc2 = defense.persistent_cap(ind2)
    sv2 = defense.severe_flag(ind2)
    n1, n2 = int((fc2 == 0.7).sum()), int((fc2 == 0.5).sum())
    p5, p2 = int((pc2 == 0.5).sum()), int((pc2 == 0.2).sum())
    sv = int(sv2.sum())
    L += ["## 6. 신호 발동 일수 (16년, oas_lag 무관)",
          f"전체 {len(dates2)}일.",
          "| 신호 | 일수 | 비중 |",
          "|---|---|---|",
          f"| fast 1개 (상한 0.7) | {n1} | {_base._pct(n1 / len(dates2))} |",
          f"| fast 2개+ (상한 0.5) | {n2} | {_base._pct(n2 / len(dates2))} |",
          f"| persistent 0.5 | {p5} | {_base._pct(p5 / len(dates2))} |",
          f"| persistent 0.2 (실질금리) | {p2} | {_base._pct(p2 / len(dates2))} |",
          f"| severe (50% BIL) | {sv} | {_base._pct(sv / len(dates2))} |",
          ""]

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
