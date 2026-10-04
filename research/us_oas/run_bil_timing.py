"""BIL 타이밍 후보 비교 (D17) — stdout 마크다운 표 + RESULTS_BIL_TIMING.md 저장.

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_bil_timing.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import load_etf_tr, load_fred, load_index
from research.us_oas import defense, rules
from research.us_oas import run_hold as _hold
from research.us_oas import run_tqqq_oas as _base
from research.us_oas import run_trend_grid as _trend

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
OAS_SERIES = _hold.OAS_SERIES
BAA_SERIES = "BAA10Y"
DFII_SERIES = "DFII10"
MA_TQQQ = 0.4
MA_WINDOW = _hold.MA_WINDOW
BAA_WINDOW = 1260
BAA_PCT = 0.9
RESULTS = Path(__file__).resolve().parent / "RESULTS_BIL_TIMING.md"
VARIANTS = ["A50", "A100", "B50", "B100", "C50", "C100"]
BENCH = ["TQQQ", "QQQ", "MA40", "MA40_ALLBIL", "MA20"]
NAMES = BENCH + VARIANTS


def _spct(x) -> str:
    return "-" if pd.isna(x) else f"{x * 100:+.1f}%"


def _baa_pct_on(baa: pd.Series, dates) -> pd.Series:
    """판단일별 BAA10Y 백분위. 관측 j의 백분위 = 직전 1260관측(자기 제외) 중
    자기보다 작은 비율. 판단일 T에는 관측일 < T 마지막 관측의 값.
    1260개 미만이면 NaN."""
    baa = baa.dropna().sort_index()
    obs = baa.index.to_numpy()
    vals = baa.to_numpy(dtype=float)
    pct = np.full(len(vals), np.nan)
    for j in range(BAA_WINDOW, len(vals)):
        pct[j] = float((vals[j - BAA_WINDOW:j] < vals[j]).mean())
    out = []
    for t in dates:
        j = int(np.searchsorted(obs, t, side="left") - 1)
        out.append(pct[j] if j >= 0 else np.nan)
    return pd.Series(out, index=pd.Index(dates), dtype=float)


def _flags(ind: pd.DataFrame, baa_pct: pd.Series) -> dict[str, pd.Series]:
    """BIL 조건 3개 (200일선 아래와의 AND는 bil_overlay가 처리)."""
    return {
        "A": (baa_pct >= BAA_PCT).fillna(False).astype(bool),
        "B": ((ind["mom20"] <= defense.MOM_SEVERE)
              & (ind["rv20"] >= defense.RV20_SEVERE)).fillna(False).astype(bool),
        "C": (ind["mom20"] < 0).fillna(False).astype(bool),
    }


def _build_weights(below: pd.Series, flags: dict[str, pd.Series]) -> dict[str, pd.DataFrame]:
    """기준 5개 + BIL 타이밍 6개 목표비중."""
    idx = below.index
    z = pd.Series(0.0, index=idx)
    o = pd.Series(1.0, index=idx)
    w = {
        "TQQQ": pd.DataFrame({"TQQQ": o, "QQQ": z, "BIL": z}, index=idx),
        "QQQ": pd.DataFrame({"TQQQ": z, "QQQ": o, "BIL": z}, index=idx),
        "MA40": rules.combine_weights(o, below, 0.4, "QQQ"),
        "MA40_ALLBIL": rules.combine_weights(o, below, 0.4, "BIL"),
        "MA20": rules.combine_weights(o, below, 0.2, "QQQ"),
    }
    for cond, label in (("A", "A"), ("B", "B"), ("C", "C")):
        for share, tag in ((0.5, "50"), (1.0, "100")):
            w[f"{label}{tag}"] = rules.bil_overlay(below, flags[cond], MA_TQQQ, share)
    ref = rules.bil_overlay(below, pd.Series(True, index=idx), 0.4, 1.0)
    if not np.allclose(w["MA40_ALLBIL"].to_numpy(dtype=float),
                       ref.to_numpy(dtype=float), atol=1e-12):
        raise ValueError("MA40_ALLBIL mismatch vs bil_overlay (must equal D13 MA40_BIL_noOAS)")
    return w


def _bil_days(w: pd.DataFrame) -> int:
    return int((w["BIL"] > 0).sum())


def _table16(names, runs, weights, dates) -> list[str]:
    stress = _trend.STRESS2
    head = "| 전략 | CAGR | MDD | Sharpe | Calmar | 평균 TQQQ | 평균 BIL | BIL 일수 | 연 매매 |"
    for label, _, _ in stress:
        head += f" {label} |"
    lines = [head, "|" + "---|" * (9 + len(stress))]
    for n in names:
        s = perf.summary(runs[n]["ret"])
        avg_t = float(weights[n]["TQQQ"].mean())
        avg_b = float(weights[n]["BIL"].mean())
        tpy = perf.trades_per_year(runs[n]["turnover"])
        row = (f"| {n} | {_base._pct(s['cagr'])} | {_base._pct(s['mdd'])}"
               f" | {_base._num(s['sharpe'])} | {_base._num(s['calmar'])}"
               f" | {_base._pct(avg_t)} | {_base._pct(avg_b)} | {_bil_days(weights[n])}"
               f" | {_base._num(tpy, 1)} |")
        for _, st, en in stress:
            row += f" {_base._pct(_trend._window_ret(runs[n]['ret'], dates, st, en))} |"
        lines.append(row)
    return lines


def _table3(names, runs, weights) -> list[str]:
    lines = ["| 전략 | CAGR | MDD | Sharpe | Calmar | 평균 BIL | BIL 일수 | 연 매매 |",
             "|" + "---|" * 8]
    for n in names:
        s = perf.summary(runs[n]["ret"])
        avg_b = float(weights[n]["BIL"].mean())
        tpy = perf.trades_per_year(runs[n]["turnover"])
        lines.append(
            f"| {n} | {_base._pct(s['cagr'])} | {_base._pct(s['mdd'])}"
            f" | {_base._num(s['sharpe'])} | {_base._num(s['calmar'])}"
            f" | {_base._pct(avg_b)} | {_bil_days(weights[n])} | {_base._num(tpy, 1)} |")
    return lines


def _episodes(mask: pd.Series) -> list[tuple[str, str]]:
    """연속 BIL>0 구간 (start, end) 목록."""
    m = mask.fillna(False).astype(bool)
    eps: list[tuple[str, str]] = []
    s: str | None = None
    prev = ""
    for d, v in m.items():
        if v and s is None:
            s = d
        if not v and s is not None:
            eps.append((s, prev))
            s = None
        prev = d
    if s is not None:
        eps.append((s, prev))
    return eps


def _ep_qqq_ret(qqq: pd.Series, s: str, e: str) -> float:
    """에피소드 s~e의 QQQ 수익 = s 다음 거래일~e 다음 거래일 누적 (w[T]는 T+1 수익에 적용)."""
    i_s = int(qqq.index.get_loc(s))
    i_e = int(qqq.index.get_loc(e))
    win = qqq.iloc[i_s + 1:i_e + 2]
    if len(win) == 0:
        return 0.0
    return float((1 + win).prod() - 1)


def _bil_contrib(w: pd.DataFrame, rets: pd.DataFrame) -> float:
    """BIL 보유일에 Σ(BIL 비중 × (다음날 BIL 수익 − 다음날 QQQ 수익)). w[T]는 T+1 수익에 적용."""
    mask = w["BIL"] > 0
    if not bool(mask.any()):
        return 0.0
    nxt = rets.shift(-1)
    diff = (nxt["BIL"] - nxt["QQQ"])[mask]
    return float((w["BIL"][mask] * diff).sum())  # 마지막 날 NaN 제외


def _ep_lines(name: str, w: pd.DataFrame, rets: pd.DataFrame) -> list[str]:
    eps = _episodes(w["BIL"] > 0)
    if not eps:
        return [f"### {name}", "에피소드 없음.", ""]
    qqq = rets["QQQ"]
    rows = [(s, e, len(rets.loc[s:e]), _ep_qqq_ret(qqq, s, e)) for s, e in eps]
    loss = sorted(rows, key=lambda r: r[3], reverse=True)[:3]
    gain = sorted(rows, key=lambda r: r[3])[:3]
    lines = [f"### {name} (에피소드 {len(eps)}개)",
             "| 구분 | 기간 | 일수 | QQQ 수익 |",
             "|---|---|---|---|"]
    for i, (s, e, n, r) in enumerate(loss, 1):
        lines.append(f"| 손실 {i} (놓친 반등) | {s}~{e} | {n} | {_spct(r)} |")
    for i, (s, e, n, r) in enumerate(gain, 1):
        lines.append(f"| 이득 {i} (피한 하락) | {s}~{e} | {n} | {_spct(r)} |")
    lines.append("")
    return lines


def main() -> None:
    oas = load_fred(OAS_SERIES)  # 3년 기간 앵커용 (신호 미사용)
    baa = load_fred(BAA_SERIES)
    dfii = load_fred(DFII_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ", "BIL", "HYG", "IEI"])
    vix = load_index("^VIX")
    vix3m = load_index("^VIX3M")
    cal = px[["TQQQ", "QQQ", "HYG", "IEI"]]  # 날짜는 run_trend_grid와 동일 입력으로

    qqq_ret_full = load_etf_tr(["QQQ"])["QQQ"].dropna()
    qqq_level_full = (1 + qqq_ret_full).cumprod()
    below_full = rules.below_ma(qqq_level_full, MA_WINDOW)

    # ---- 16년 기간 (run_trend_grid와 같은 기간) ----
    valid = cal.notna().all(axis=1)
    first = valid[valid].index[0]
    dates2 = [d for d in cal.index if d >= first]
    rets2 = px.loc[dates2][["TQQQ", "QQQ", "BIL"]]
    if bool(rets2.isna().any().any()):
        raise ValueError("NaN return in period")
    ind2 = defense.indicators(qqq_level_full, qqq_ret_full, vix, vix3m, dfii, dates2)
    below2 = _hold._below_on(dates2, below_full)
    weights2 = _build_weights(below2, _flags(ind2, _baa_pct_on(baa, dates2)))
    runs2 = {n: portfolio.run(weights2[n], rets2, COST) for n in NAMES}

    # ---- 3년 기간 (run_nowcast와 같은 기간) ----
    start = oas.index[0]
    dates1 = [d for d in cal.index if d > start]
    rets1 = px.loc[dates1][["TQQQ", "QQQ", "BIL"]]
    if bool(rets1.isna().any().any()):
        raise ValueError("NaN return in period")
    ind1 = defense.indicators(qqq_level_full, qqq_ret_full, vix, vix3m, dfii, dates1)
    below1 = _hold._below_on(dates1, below_full)
    weights1 = _build_weights(below1, _flags(ind1, _baa_pct_on(baa, dates1)))
    runs1 = {n: portfolio.run(weights1[n], rets1, COST) for n in NAMES}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last1, last2 = dates1[-1], dates2[-1]
    L: list[str] = [
        "# BIL 타이밍 후보 비교",
        "",
        f"- 생성: {now}",
        f"- 16년 기간: {dates2[0]}~{last2} (거래일 {len(dates2)}일, run_trend_grid와 같은 기간)",
        f"- 3년 기간: {dates1[0]}~{last1} (거래일 {len(dates1)}일, run_nowcast와 같은 기간)",
        "- ma_tqqq 0.4 고정, A는 BAA10Y(검증용, 실매매 사용 미정),"
        " B 임계는 외부 문서 값, 비용 0.1%, 세전",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, BAA10Y {baa.index[-1]},"
        f" VIX {vix.index[-1]}",
        f"- 3년 앵커: OAS {OAS_SERIES} 첫 관측 {start} (기간 정의용, 신호 미사용)",
        f"- QQQ 200일선: {qqq_ret_full.index[0]}~ 누적수준으로 계산 후 절단, 당일 종가 기준",
        "- BAA10Y 백분위: 각 관측일에 직전 1260관측 중 자기보다 작은 비율,"
        " 판단일 T에는 관측일<T 마지막 값, 1260개 미만이면 NaN→조건 거짓",
        "- BIL 조건(200일선 아래와 AND): A=BAA10Y 백분위≥0.9,"
        " B=mom20≤-10%·rv20≥30%(외부 문서 severe 임계), C=mom20<0;"
        " mom20·rv20은 전일 기준",
        "- MA40_ALLBIL=D13 MA40_BIL_noOAS와 같은 구성 (아래면 rest 전부 BIL)",
        "",
    ]

    L += ["## 1. 16년 요약",
          "2020=20200219~20200320(코로나), 2022=20211119~20221228,"
          " 2025=20241216~20250408 (run_trend_grid와 같은 구간).",
          *_table16(NAMES, runs2, weights2, dates2),
          ""]

    L += ["## 2. 3년 요약",
          *_table3(NAMES, runs1, weights1),
          ""]

    L += ["## 3. BIL 손익 분해 (16년)",
          "BIL 보유일에 Σ(비중 w[T] × 다음날(T+1) 수익(BIL−QQQ)) — 목표비중 기준 gross 기여,"
          " 비용 제외. 양수면 BIL로 피한 게 이득.",
          "에피소드 6개 미만 변형은 손실·이득 목록이 겹칠 수 있다.",
          "",
          "| 변형 | BIL 보유일 | 에피소드 수 | BIL 손익 기여 |",
          "|---|---|---|---|"]
    for n in VARIANTS:
        n_ep = len(_episodes(weights2[n]["BIL"] > 0))
        L.append(f"| {n} | {_bil_days(weights2[n])} | {n_ep}"
                 f" | {_spct(_bil_contrib(weights2[n], rets2))} |")
    L.append("")
    for n in VARIANTS:
        L += _ep_lines(n, weights2[n], rets2)

    def _sharpe2(n: str) -> float:
        v = perf.summary(runs2[n]["ret"])["sharpe"]
        return v if pd.notna(v) else float("-inf")

    top2 = sorted(VARIANTS, key=_sharpe2, reverse=True)[:2]
    ynames = ["TQQQ", "MA40", "MA40_ALLBIL"] + top2
    L += ["## 4. 연도별 수익 (16년)",
          f"Sharpe 상위 2개: {', '.join(top2)}.",
          "| 연도 | " + " | ".join(ynames) + " |",
          "|" + "---|" * (len(ynames) + 1)]
    y = pd.concat({n: perf.yearly(runs2[n]["ret"]) for n in ynames}, axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | "
                 + " | ".join(_base._pct(y.loc[yr, n]) for n in ynames) + " |")
    L.append("")

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
