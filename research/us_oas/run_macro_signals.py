"""리스크 후보 지표 — 시점 보장 신호 + 이후 QQQ 성과 측정 (D18-2).

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_macro_signals.py
stdout 마크다운 표 + RESULTS_MACRO_SIGNALS.md 저장.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily import perf
from research.backtest_daily.data_us import (
    load_etf_tr, load_first_release, load_fred, load_index,
)
from research.us_oas import macro_signals as ms

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

FWD_SHORT = 63
FWD_LONG = 126
SAHM_FLAG = 0.5
RESULTS = Path(__file__).resolve().parent / "RESULTS_MACRO_SIGNALS.md"

QHEAD = ("| 구분 | n | 지표 중앙값 | 이후 3개월 평균 | 이후 6개월 평균 |"
         " 6개월 마이너스 비율 | 6개월 MDD 평균 | 6개월 MDD 최악 |")
QSEP = "|" + "---|" * 8


def _pct(x, d: int = 1) -> str:
    return "-" if pd.isna(x) else f"{x * 100:+.{d}f}%"


def _num(x, d: int = 2) -> str:
    return "-" if pd.isna(x) else f"{x:.{d}f}"


def _shr(x) -> str:
    return "-" if pd.isna(x) else f"{x * 100:.1f}%"


def _month_ends(trading_days) -> list[str]:
    last: dict[str, str] = {}
    for d in trading_days:
        last[d[:6]] = d
    return sorted(last.values())


def _forward(qqq_level: pd.Series, qqq_ret: pd.Series, t: str):
    """T 이후 63·126거래일 QQQ 수익과 이후 126거래일 MDD. forward 부족이면 None."""
    k = int(qqq_level.index.get_loc(t))
    if k + FWD_LONG >= len(qqq_level):
        return None
    lv = qqq_level.to_numpy(dtype=float)
    f63 = float(lv[k + FWD_SHORT] / lv[k] - 1)
    f126 = float(lv[k + FWD_LONG] / lv[k] - 1)
    mdd = perf.max_drawdown(qqq_ret.iloc[k + 1:k + FWD_LONG + 1])
    return f63, f126, mdd


def _quintiles(s: pd.Series, k: int = 5) -> pd.Series:
    codes = pd.qcut(s.to_numpy(dtype=float), k, duplicates="drop").codes
    return pd.Series([f"Q{int(c) + 1}" for c in codes], index=s.index)


def _cells(g: pd.DataFrame, med: str) -> str:
    return (f"| {med} | {_pct(g['fwd63'].mean())} | {_pct(g['fwd126'].mean())}"
            f" | {_shr((g['fwd126'] < 0).mean())}"
            f" | {_pct(g['mdd126'].mean())} | {_pct(g['mdd126'].min())} |")


def _cont_table(frame: pd.DataFrame, col: str, med_fmt) -> list[str]:
    """연속값 5분위표 + 전체 기준선. Q1=지표 하위, Q5=상위."""
    valid = frame.dropna(subset=[col])
    if len(valid) < 5:
        return [f"유효 표본 {len(valid)}개로 5분위 불가."]
    q = _quintiles(valid[col])
    lines = [QHEAD, QSEP]
    for label in sorted(q.unique(), key=lambda s: int(s[1:])):
        g = valid[q == label]
        lines.append(f"| {label} | {len(g)} "
                     + _cells(g, med_fmt(valid[col].loc[g.index].median())))
    lines.append(f"| 전체 | {len(valid)} " + _cells(valid, med_fmt(valid[col].median())))
    return lines


def _flag_table(frame: pd.DataFrame, flag: pd.Series, cont_col: str, med_fmt) -> list[str]:
    valid = frame.dropna(subset=[cont_col])
    f = flag.reindex(valid.index).fillna(False).astype(bool)
    lines = [QHEAD, QSEP]
    for val, label in ((True, "True"), (False, "False")):
        g = valid[f == val]
        med = med_fmt(valid[cont_col].loc[g.index].median()) if len(g) else "-"
        lines.append(f"| {label} | {len(g)} " + _cells(g, med))
    return lines


def _true_months(frame: pd.DataFrame, flag: pd.Series, cont_col: str) -> list[str]:
    valid = frame.dropna(subset=[cont_col])
    f = flag.reindex(valid.index).fillna(False).astype(bool)
    trues = [d for d in valid.index if f.loc[d]]
    if not trues:
        return ["True 표본 없음."]
    by_year: dict[str, list[str]] = {}
    for d in trues:
        by_year.setdefault(d[:4], []).append(d)
    return [f"- {y} ({len(v)}개): {', '.join(v)}" for y, v in sorted(by_year.items())]


def main() -> None:
    qqq_ret_all = load_etf_tr(["QQQ"])["QQQ"].dropna()
    qqq_level = (1 + qqq_ret_all).cumprod()
    qqq_ret = qqq_ret_all.reindex(qqq_level.index)
    dgs10 = load_fred("DGS10")
    dtb3 = load_fred("DTB3")
    unrate_first = load_first_release("UNRATE")
    icnsa_first = load_first_release("ICNSA")
    icnsa_obs = load_fred("ICNSA")
    dxy = load_index("DX-Y.NYB")
    hg = load_index("HG=F")
    gc = load_index("GC=F")

    samples: list[str] = []
    fwd: list[tuple[float, float, float]] = []
    for t in _month_ends(qqq_level.index):
        r = _forward(qqq_level, qqq_ret, t)
        if r is None:
            continue
        samples.append(t)
        fwd.append(r)

    curve_df = ms.curve(dgs10, dtb3, samples)
    sahm_s = ms.sahm(unrate_first, samples)
    claims_df = ms.claims_yoy(icnsa_first, icnsa_obs, samples)
    dxy_3m = ms.pct_change_n(dxy, samples, FWD_SHORT)
    cu_au_3m = ms.pct_change_n((hg / gc).dropna(), samples, FWD_SHORT)

    frame = pd.DataFrame({
        "curve_spread": curve_df["spread"],
        "curve_disinvert": curve_df["disinvert"],
        "sahm": sahm_s,
        "claims_yoy": claims_df["yoy"],
        "claims_approx": claims_df["approx"],
        "dxy_3m": dxy_3m,
        "cu_au_3m": cu_au_3m,
        "fwd63": [r[0] for r in fwd],
        "fwd126": [r[1] for r in fwd],
        "mdd126": [r[2] for r in fwd],
    }, index=pd.Index(samples))
    sahm_flag = (frame["sahm"] >= SAHM_FLAG)

    post = frame[np.array(samples) >= ms.ICNSA_CUTOFF]
    n_approx = int(frame["claims_approx"].sum())
    n_post_valid = int(post["claims_yoy"].notna().sum())

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L: list[str] = [
        "# 리스크 후보 지표 — 시점 보장 신호 + 이후 QQQ 성과",
        "",
        f"- 생성: {now}",
        f"- 표본: {samples[0]}~{samples[-1]} 월말 마지막 거래일 {len(samples)}개"
        " (이후 126거래일 확보분만, 매월 1개 비중첩, D16과 같은 방식)",
        "- 시점 규칙: 시장값(DGS10·DTB3·DX-Y.NYB·HG=F·GC=F) 관측일<T,"
        " UNRATE realtime_start<T 첫 공개값만,"
        f" ICNSA T≥{ms.ICNSA_CUTOFF} 첫 공개값·T<{ms.ICNSA_CUTOFF} 관측일+12일<T 사후값(근사 표시)",
        "- 월말 비중첩, 분위는 전체 표본 기준 묘사용(매매 아님)."
        " Q1=지표 하위, Q5=상위. 이후 3개월=63거래일, 6개월=126거래일,",
        " MDD=이후 126거래일 최대낙폭(perf.max_drawdown).",
        f"- 데이터 마지막 날짜: QQQ {qqq_ret_all.index[-1]}, DGS10 {dgs10.index[-1]},"
        f" DTB3 {dtb3.index[-1]}, UNRATE 첫공개 {unrate_first['date'].iloc[-1]},"
        f" ICNSA {icnsa_obs.index[-1]}, DXY {dxy.index[-1]}, HG {hg.index[-1]}, GC {gc.index[-1]}",
        "- 구리/금 비율은 공통 거래일만. HG·GC는 연결선물이라 롤오버 점프 가능.",
        "",
        "## 1. 표본 요약 (기준선)",
        "",
        f"- 전체 {len(frame)}개: 이후 3개월 평균 {_pct(frame['fwd63'].mean())},"
        f" 이후 6개월 평균 {_pct(frame['fwd126'].mean())},"
        f" 6개월 마이너스 비율 {_shr((frame['fwd126'] < 0).mean())},"
        f" 6개월 MDD 평균 {_pct(frame['mdd126'].mean())},"
        f" 최악 {_pct(frame['mdd126'].min())}",
        "",
        "## 2. curve_spread 5분위 (DGS10−DTB3, %p)",
        "",
        *_cont_table(frame, "curve_spread", _num),
        "",
        "## 3. curve_disinvert 플래그 (지표 중앙값=spread %p)",
        "",
        *_flag_table(frame, frame["curve_disinvert"], "curve_spread", _num),
        "",
        "True 월:",
        "",
        *_true_months(frame, frame["curve_disinvert"], "curve_spread"),
        "",
        "## 4. sahm 5분위",
        "",
        *_cont_table(frame, "sahm", _num),
        "",
        f"## 5. sahm≥{SAHM_FLAG} 플래그 (지표 중앙값=sahm)",
        "",
        *_flag_table(frame, sahm_flag, "sahm", _num),
        "",
        "True 월:",
        "",
        *_true_months(frame, sahm_flag, "sahm"),
        "",
        "## 6. claims_yoy 5분위 (전체 표본)",
        "",
        f"근사 표본 {n_approx}개 (T<{ms.ICNSA_CUTOFF}, 관측일+12일 규칙 사후값),"
        f" 첫 공개값 표본 {len(frame) - n_approx}개.",
        "",
        *_cont_table(frame, "claims_yoy", _pct),
        "",
        "## 7. claims_yoy 5분위 (2009-06 이후만)",
        "",
        f"유효 {n_post_valid}개, 분위·기준선 모두 이 구간 기준.",
        "",
        *_cont_table(post, "claims_yoy", _pct),
        "",
        "## 8. dxy_3m 5분위 (달러지수 63거래일 변화율)",
        "",
        *_cont_table(frame, "dxy_3m", _pct),
        "",
        "## 9. cu_au_3m 5분위 (구리/금 비율 63거래일 변화율)",
        "",
        *_cont_table(frame, "cu_au_3m", _pct),
        "",
    ]

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
