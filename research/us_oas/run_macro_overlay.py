"""합성 TQQQ 1999~ + 200일선·달러·금리차 오버레이 백테스트 (D19).

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_macro_overlay.py
stdout 마크다운 표 + RESULTS_MACRO_OVERLAY.md 저장.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import (
    load_etf_tr, load_fred, load_index, synth_cash, synth_leveraged,
)
from research.us_oas import macro_signals as ms
from research.us_oas import rules
from research.us_oas import run_hold as _hold
from research.us_oas import run_tqqq_oas as _base
from research.us_oas import run_trend_grid as _trend

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
MA_WINDOW = _hold.MA_WINDOW
MA_TQQQ = 0.4
DXY_N = 63
DXY_TH = 0.05  # 사전 고정 (분위 보기 전 합의값)
RESULTS = Path(__file__).resolve().parent / "RESULTS_MACRO_OVERLAY.md"
NAMES = ["TQQQ", "QQQ", "MA40", "MA40_ALLBIL", "MA40+DXY", "MA40+CURVE",
         "MA40+BOTH", "MA40+DXY_BIL", "MA40+CURVE_BIL", "MA40+BOTH_BIL"]
YBASE = ["TQQQ", "MA40", "MA40_ALLBIL"]
DRAWDOWNS = [("닷컴", "20000327", "20021009"),
             ("금융위기", "20071031", "20090309"),
             ("2020", "20200219", "20200320"),
             ("2022", "20211119", "20221228"),
             ("2025", "20241216", "20250408")]


def _weights_3(tqqq: pd.Series, bil: pd.Series) -> pd.DataFrame:
    """TQQQ·BIL Series → columns ["TQQQ", "QQQ", "BIL"] 목표비중표 (행 합 1)."""
    t = tqqq.astype(float)
    b = bil.astype(float)
    return pd.DataFrame({"TQQQ": t, "QQQ": 1.0 - t - b, "BIL": b}, index=t.index)


def _build_weights(below: pd.Series, dxy_flag: pd.Series,
                   curve_flag: pd.Series) -> dict[str, pd.DataFrame]:
    """10개 전략 목표비중. MA40 값=min 상한, 오버레이 cap=0.4, 나머지는 QQQ/BIL."""
    idx = below.index
    z = pd.Series(0.0, index=idx)
    o = pd.Series(1.0, index=idx)
    b = below.fillna(False).astype(bool)
    d = dxy_flag.fillna(False).astype(bool)
    c = curve_flag.fillna(False).astype(bool)
    both = (d | c)
    ma_cap = pd.Series(np.where(b.to_numpy(dtype=bool), MA_TQQQ, 1.0), index=idx)

    def _cap(flag: pd.Series) -> pd.Series:
        return pd.Series(np.where(flag.to_numpy(dtype=bool), 0.4, 1.0), index=idx)

    def _min(a: pd.Series, e: pd.Series) -> pd.Series:
        return pd.concat([a, e], axis=1).min(axis=1)

    t_dxy = _min(ma_cap, _cap(d))
    t_cur = _min(ma_cap, _cap(c))
    t_both = _min(ma_cap, _cap(both))

    def _rest_bil(t: pd.Series, mask: pd.Series) -> pd.DataFrame:
        m = mask.fillna(False).astype(bool).to_numpy(dtype=bool)
        rest = 1.0 - t.to_numpy(dtype=float)
        return _weights_3(t, pd.Series(np.where(m, rest, 0.0), index=idx))

    return {
        "TQQQ": pd.DataFrame({"TQQQ": o, "QQQ": z, "BIL": z}, index=idx),
        "QQQ": pd.DataFrame({"TQQQ": z, "QQQ": o, "BIL": z}, index=idx),
        "MA40": rules.combine_weights(o, b, 0.4, "QQQ"),
        "MA40_ALLBIL": rules.combine_weights(o, b, 0.4, "BIL"),
        "MA40+DXY": _weights_3(t_dxy, z),
        "MA40+CURVE": _weights_3(t_cur, z),
        "MA40+BOTH": _weights_3(t_both, z),
        "MA40+DXY_BIL": _rest_bil(t_dxy, b & d),
        "MA40+CURVE_BIL": _rest_bil(t_cur, b & c),
        "MA40+BOTH_BIL": _rest_bil(t_both, b & both),
    }


def _summary_lines(names, runs, weights) -> list[str]:
    lines = ["| 전략 | CAGR | MDD | Sharpe | Calmar | 평균 TQQQ | 평균 BIL | 연 매매 | 신호 일수 |",
             "|" + "---|" * 9]
    for n in names:
        s = perf.summary(runs[n]["ret"])
        avg_t = float(weights[n]["TQQQ"].mean())
        avg_b = float(weights[n]["BIL"].mean())
        tpy = perf.trades_per_year(runs[n]["turnover"])
        sig = int((weights[n]["TQQQ"] < 1 - 1e-9).sum())
        lines.append(
            f"| {n} | {_base._pct(s['cagr'])} | {_base._pct(s['mdd'])}"
            f" | {_base._num(s['sharpe'])} | {_base._num(s['calmar'])}"
            f" | {_base._pct(avg_t)} | {_base._pct(avg_b)}"
            f" | {_base._num(tpy, 1)} | {sig} |")
    return lines


def main() -> None:
    px = load_etf_tr(["QQQ", "TQQQ", "BIL"])
    dtb3 = load_fred("DTB3")
    dgs10 = load_fred("DGS10")
    dxy = load_index("DX-Y.NYB")

    qqq_full = px["QQQ"].dropna()
    tqqq_actual = px["TQQQ"]
    bil_actual = px["BIL"]
    tqqq_first = str(tqqq_actual.dropna().index[0])
    bil_first = str(bil_actual.dropna().index[0])

    # ---- 합성 이음 (실제 첫 수익일 전만 합성) ----
    syn_t = synth_leveraged(px["QQQ"], dtb3)
    syn_b = synth_cash(dtb3, px.index.tolist())
    tqqq = tqqq_actual.copy()
    tqqq.loc[px.index < tqqq_first] = syn_t.loc[syn_t.index < tqqq_first]
    bil = bil_actual.copy()
    bil.loc[px.index < bil_first] = syn_b.loc[syn_b.index < bil_first]

    # ---- 합성 검증 (실제 TQQQ 구간, 합성으로만 만든 시리즈와 비교) ----
    ov = tqqq_actual.dropna().index.tolist()
    syn_o = syn_t.reindex(ov)
    act_o = tqqq_actual.reindex(ov)
    ok = syn_o.notna() & act_o.notna()
    syn_o, act_o = syn_o[ok], act_o[ok]
    corr = float(syn_o.corr(act_o))
    cagr_s, cagr_a = perf.cagr(syn_o), perf.cagr(act_o)
    mult_s = float((1 + syn_o).prod())
    mult_a = float((1 + act_o).prod())

    # ---- 백테스트 기간·신호 ----
    qdates = qqq_full.index.tolist()
    start = qdates[200]  # QQQ 수익 첫날 + 200거래일 (200일선 유효)
    dates = [d for d in qdates if d >= start]
    qqq_level_full = (1 + qqq_full).cumprod()
    below = _hold._below_on(dates, rules.below_ma(qqq_level_full, MA_WINDOW))
    dxy_flag = (ms.pct_change_n(dxy, dates, DXY_N) >= DXY_TH).astype(bool)
    curve_flag = ms.curve(dgs10, dtb3, dates)["disinvert"].astype(bool)

    rets = pd.DataFrame({"TQQQ": tqqq.reindex(dates),
                         "QQQ": qqq_full.reindex(dates),
                         "BIL": bil.reindex(dates)})
    if bool(rets.isna().any().any()):
        raise ValueError("NaN return in period")
    weights = _build_weights(below, dxy_flag, curve_flag)
    runs = {n: portfolio.run(weights[n], rets, COST) for n in NAMES}

    sub1 = [d for d in dates if "199912" <= d[:6] <= "200912"]
    sub2 = [d for d in dates if "201002" <= d[:6] <= "202610"]
    if not sub1 or not sub2:
        raise ValueError("empty subperiod")
    runs_s1 = {n: portfolio.run(weights[n].loc[sub1], rets.loc[sub1], COST) for n in NAMES}
    runs_s2 = {n: portfolio.run(weights[n].loc[sub2], rets.loc[sub2], COST) for n in NAMES}

    def _sh(n: str) -> float:
        v = perf.summary(runs[n]["ret"])["sharpe"]
        return v if pd.notna(v) else float("-inf")

    top3 = sorted(NAMES, key=_sh, reverse=True)[:3]
    ynames = list(dict.fromkeys(YBASE + top3))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L: list[str] = [
        "# 합성 TQQQ + 매크로 오버레이 백테스트",
        "",
        f"- 생성: {now}",
        f"- 기간: {dates[0]}~{dates[-1]} (거래일 {len(dates)}일,"
        f" QQQ 수익 첫날 {qdates[0]} + 200거래일부터)",
        "- 합성: 3배 일간 리밸런싱 근사, 보수 연 0.95%, 차입비용 DTB3(연율 %),"
        f" 실제 TQQQ 상장 전만 합성(실제 첫 수익일 {tqqq_first}부터 실제);"
        f" BIL도 첫 수익일 {bil_first} 전 synth_cash(DTB3 일간 rate/100/252)",
        "- 신호 시점: below=당일 QQQ 종가 200일선(이전 실험들과 동일);"
        " dxy·curve는 관측일<T 값만(macro_signals, ≤T−1)",
        "- 임계 사전 고정(DXY +5%·금리차 역전해소), 비용 0.1%, 세전",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, DTB3 {dtb3.index[-1]},"
        f" DGS10 {dgs10.index[-1]}, DXY {dxy.index[-1]}",
        "- 전략: TQQQ 비중 + 나머지 QQQ 또는 BIL; MA40=below 시 TQQQ 0.4;"
        " 오버레이 cap=min(MA40 값, 플래그 시 0.4); _BIL=below&플래그일에 나머지 전부 BIL",
        "",
        "## 0. 합성 검증 (실제 TQQQ 구간)",
        "",
        "| 항목 | 값 |",
        "|---|---|",
        f"| 실제 TQQQ 구간 | {ov[0]}~{ov[-1]} (비교 {len(syn_o)}일) |",
        f"| 일간 수익 상관계수 | {_base._num(corr, 4)} |",
        f"| 연환산 수익 합성 | {_base._pct(cagr_s)} |",
        f"| 연환산 수익 실제 | {_base._pct(cagr_a)} |",
        f"| 차이(합성−실제) | {_base._pct(cagr_s - cagr_a)} |",
        f"| 누적 배수 합성 | {_base._num(mult_s)} |",
        f"| 누적 배수 실제 | {_base._num(mult_a)} |",
        "",
        "## 1. 전체 기간 요약",
        "신호 일수=TQQQ 목표비중 100% 미만 일수(QQQ는 항상 0%라 전 기간).",
        *_summary_lines(NAMES, runs, weights),
        "",
        "## 2. 구간별 요약 (CAGR·MDD·Sharpe)",
        f"합성구간: {sub1[0]}~{sub1[-1]} ({len(sub1)}일, TQQQ 합성),"
        f" 실제구간: {sub2[0]}~{sub2[-1]} ({len(sub2)}일, 실제 TQQQ)."
        " 각 구간 첫날 목표비중 그대로 portfolio.run 재실행.",
        "| 전략 | 합성 CAGR | 합성 MDD | 합성 Sharpe | 실제 CAGR | 실제 MDD | 실제 Sharpe |",
        "|" + "---|" * 7,
    ]
    for n in NAMES:
        a, b = perf.summary(runs_s1[n]["ret"]), perf.summary(runs_s2[n]["ret"])
        L.append(f"| {n} | {_base._pct(a['cagr'])} | {_base._pct(a['mdd'])}"
                 f" | {_base._num(a['sharpe'])} | {_base._pct(b['cagr'])}"
                 f" | {_base._pct(b['mdd'])} | {_base._num(b['sharpe'])} |")
    L.append("")

    L += ["## 3. 큰 낙폭 구간 수익",
          ", ".join(f"{label}={s}~{e}" for label, s, e in DRAWDOWNS) + ".",
          "| 전략 | " + " | ".join(label for label, _, _ in DRAWDOWNS) + " |",
          "|" + "---|" * (len(DRAWDOWNS) + 1)]
    for n in NAMES:
        cells = [_base._pct(_trend._window_ret(runs[n]["ret"], dates, s, e))
                 for _, s, e in DRAWDOWNS]
        L.append(f"| {n} | {' | '.join(cells)} |")
    L.append("")

    L += ["## 4. 연도별 수익",
          f"전체 Sharpe 상위 3개: {', '.join(top3)}.",
          "| 연도 | " + " | ".join(ynames) + " |",
          "|" + "---|" * (len(ynames) + 1)]
    y = pd.concat({n: perf.yearly(runs[n]["ret"]) for n in ynames}, axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | "
                 + " | ".join(_base._pct(y.loc[yr, n]) for n in ynames) + " |")
    L.append("")

    L += ["## 5. 신호 발동 연도별 일수",
          "| 연도 | dxy_flag | curve_flag |",
          "|---|---|---|"]
    for yr in sorted({d[:4] for d in dates}):
        dy = [d for d in dates if d[:4] == yr]
        L.append(f"| {yr} | {int(dxy_flag.loc[dy].sum())}"
                 f" | {int(curve_flag.loc[dy].sum())} |")
    L.append(f"| 합계 | {int(dxy_flag.sum())} | {int(curve_flag.sum())} |")
    L.append("")

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
