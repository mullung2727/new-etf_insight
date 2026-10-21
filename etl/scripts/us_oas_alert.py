"""OAS spike alert — estimate today's HY OAS change, log it, print OAS lines.

Reads FRED HY OAS + HYG/IEI total returns, nowcasts today's OAS change
(PLAN_US_MACRO_OAS.md section 8), alerts on >= 20bp or top 2% of the
trailing year, and appends one row per date to oas_alert_log in
us_macro.duckdb. Prints lines starting with "OAS " for
run-us-daily.ps1 to pick up.

Usage (from etl/):
    uv run python scripts/us_oas_alert.py [--db-path db/us_macro.duckdb]
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401
ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from research.backtest_daily.data_us import load_etf_tr, load_fred  # noqa: E402
from research.us_oas import proxy  # noqa: E402
from notify import send_strategy_alert  # noqa: E402

import argparse
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from new_etf_insight.data_paths import db_dir

OAS_SERIES = "BAMLH0A0HYM2"
FIXED_BP = 20.0
PCT_Q = 0.02
WIN = 252
MIN_N = 126
CUM_K = 7
RISK_WEBHOOK_ENV = "US_RISK_WEBHOOK_URL"
# research/private/us_oas_alert/RESULTS_SPIKE_ALERT.md 5절 P 구간 분위 2% 14.49% vs 평소 6.94%
_RISK_PAST_SPIKE_PCT = 14
_RISK_PAST_BASE_PCT = 7
DEFAULT_DB_PATH = db_dir() / "us_macro.duckdb"

_CREATE_ALERT_LOG = """
CREATE TABLE IF NOT EXISTS oas_alert_log (
  date VARCHAR PRIMARY KEY, run_at TIMESTAMP, base_date VARCHAR, base DOUBLE, est DOUBLE,
  chg_bp DOUBLE, thr_bp DOUBLE, rank_pct DOUBLE, alert_fixed BOOLEAN, alert_pct BOOLEAN,
  miss_date VARCHAR, miss_bp DOUBLE, cum7_bp DOUBLE, reported BOOLEAN DEFAULT FALSE)
"""


def decide(prior: list[float] | np.ndarray, chg: float) -> dict:
    """Alert decision from today's change vs trailing changes (pure, no I/O).

    prior: earlier chg values in time order, NaN already removed. Uses the
    last WIN only. Fewer than MIN_N -> no percentile decision (fixed only).
    """
    w = np.asarray(prior, dtype=float)[-WIN:]
    n = int(w.size)
    chg = float(chg)
    if n < MIN_N:
        thr, rank_pct, alert_pct = None, None, False
    else:
        thr = float(np.quantile(w, 1 - PCT_Q))
        rank_pct = float((w < chg).mean())
        alert_pct = bool(chg >= thr)
    alert_fixed = bool(chg >= FIXED_BP)
    return {
        "chg_bp": chg,
        "thr_bp": thr,
        "rank_pct": rank_pct,
        "alert_fixed": alert_fixed,
        "alert_pct": alert_pct,
        "alert": bool(alert_fixed or alert_pct),
        "n_prior": n,
    }


def compute(oas: pd.Series, etf_logret: pd.DataFrame) -> dict:
    """Nowcast today's OAS change and decide (pure, no I/O).

    T = last trading day with both HYG and IEI log returns. Only the last
    WIN + 60 eligible dates are nowcast (speed); per-date nowcast math does
    not depend on the date list, so the result is unchanged.
    """
    both = etf_logret[["HYG", "IEI"]].dropna().index
    T = both[-1]
    dates = [d for d in both if d > oas.index[0] and d <= T][-(WIN + 60):]
    nc = proxy.nowcast(oas, etf_logret, dates)
    t = nc.loc[T]
    if not bool(t["fitted"]):
        raise RuntimeError("nowcast not fitted for T")
    chg = (nc["oas"].astype(float) - nc["base"].astype(float)) * 100
    chg = chg[nc["fitted"]]
    chg_t = float(chg.loc[T])
    prior = [float(v) for d, v in chg.items() if d < T]
    out = decide(prior, chg_t)
    base_date = str(t["base_date"])
    if base_date in nc.index and bool(nc.loc[base_date, "fitted"]):
        miss_date = base_date
        miss_bp = float((float(oas.loc[base_date]) - float(nc.loc[base_date, "oas"])) * 100)
    else:
        miss_date, miss_bp = None, None
    try:
        i = list(oas.index).index(base_date)
    except ValueError:
        i = None
    if i is not None and i - (CUM_K - 1) >= 0:
        cum7_bp = (float(t["oas"]) - float(oas.iloc[i - (CUM_K - 1)])) * 100
    else:
        cum7_bp = None
    return {
        "date": str(T),
        "base_date": base_date,
        "base": float(t["base"]),
        "est": float(t["oas"]),
        **out,
        "miss_date": miss_date,
        "miss_bp": miss_bp,
        "cum7_bp": cum7_bp,
    }


def format_lines(r: dict, already: bool) -> list[str]:
    """Report lines, all starting with "OAS " (ASCII for the ps1 runner)."""
    if already:
        return [f"OAS {r['date']} already reported"]
    lines = []
    chg = r["chg_bp"]
    if r["alert"]:
        reasons = []
        if r["alert_fixed"]:
            reasons.append(">= 20bp")
        if r["alert_pct"]:
            reasons.append(f"top 2% (thr {r['thr_bp']:.1f}bp)")
        lines.append(f"OAS ALERT spike: chg {chg:+.1f}bp ({', '.join(reasons)})")
    rank = f"top {(1 - r['rank_pct']) * 100:.1f}%" if r["rank_pct"] is not None else "n/a"
    lines.append(
        f"OAS {r['date']} est {r['est']:.2f}% (actual {r['base_date']} {r['base']:.2f}%) "
        f"chg {chg:+.1f}bp, 1y rank {rank}"
        + (f", 7d {r['cum7_bp']:+.1f}bp" if r["cum7_bp"] is not None else "")
    )
    if r["miss_bp"] is not None:
        lines.append(f"OAS miss {r['miss_date']}: actual - est {r['miss_bp']:+.1f}bp")
    return lines


def qqq_ma_gap(qqq_ret: pd.Series, asof: str) -> float | None:
    """QQQ 200일선 괴리율 (순수, no I/O). asof 이하 일간 총수익만 사용."""
    # 200일선 = QQQ 총수익 수준의 200일 단순이동평균.
    s = qqq_ret[qqq_ret.index <= asof]
    if len(s) < 200:
        return None
    level = (1 + s.fillna(0)).cumprod()
    sma200 = level.rolling(200).mean()
    return float(level.iloc[-1] / sma200.iloc[-1] - 1)


def _fmt_ymd(d) -> str:
    s = str(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def format_risk_alert(r: dict, ma_gap: float | None) -> str:
    """🟡 주의(카나리) 전용 채널 문구 (순수, no I/O)."""
    date = _fmt_ymd(r["date"])
    base_date = _fmt_ymd(r["base_date"])
    lines = [f"🟡 [주의] 미국 신용위험 급등 — 카나리 ({date} 미국장 기준)"]
    lines.append(
        f"- 하이일드 스프레드(투기등급 회사채와 국채 금리차) 추정 {r['est']:.2f}%, "
        f"전날({base_date}) {r['base']:.2f}% 대비 {r['chg_bp'] / 100:+.2f}%p"
    )
    if r["rank_pct"] is None:
        lines.append(f"- 하루 상승폭 {r['chg_bp'] / 100:+.2f}%p (최근 1년 순위 계산 불가)")
    else:
        lines.append(
            f"- 하루 상승폭이 최근 1년 중 상위 {(1 - r['rank_pct']) * 100:.1f}%  "
            f"(알림 기준: 상위 2% 또는 +0.20%p 이상)"
        )
    if r["cum7_bp"] is not None:
        lines.append(f"- 최근 1주 누적 {r['cum7_bp'] / 100:+.2f}%p")
    if ma_gap is None:
        qqq = "QQQ 200일선 확인 실패"
    elif ma_gap >= 0:
        qqq = f"나스닥100(QQQ) 200일선 위 {ma_gap * 100:+.1f}% → 하락 추세 아님"
    else:
        qqq = f"⚠ 나스닥100(QQQ) 200일선 아래 {ma_gap * 100:+.1f}% → 하락 추세와 겹침, 특히 주의"
    lines.append(f"- {qqq}")
    lines.append(
        f"- 과거(2007~2023) 이런 날 뒤 20일 안 나스닥100 −10% 이상 하락 약 "
        f"{_RISK_PAST_SPIKE_PCT}% (평소 {_RISK_PAST_BASE_PCT}%) — 대부분은 지나감"
    )
    lines.append("- 오늘 값은 HYG·IEI 등락으로 추정, 공식값은 다음 날 발표")
    return "\n".join(lines)


def send_risk_channel(r: dict) -> bool:
    """전용 채널(RISK_WEBHOOK_ENV)로 카나리 알림 전송. 실패해도 False만 반환."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    try:
        gap = qqq_ma_gap(load_etf_tr(["QQQ"])["QQQ"], r["date"])
    except Exception as e:
        print(f"OAS qqq_ma_gap failed: {e!r}")
        gap = None
    return bool(send_strategy_alert(format_risk_alert(r, gap), RISK_WEBHOOK_ENV))


def ensure_alert_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_CREATE_ALERT_LOG)


def record(con: duckdb.DuckDBPyConnection, r: dict) -> bool:
    """Return True if this run should report r["date"].

    First run's values stay (no row update on rerun); report completion
    is marked with mark_reported.
    """
    con.execute(
        "INSERT INTO oas_alert_log (date, run_at, base_date, base, est, chg_bp, thr_bp,"
        " rank_pct, alert_fixed, alert_pct, miss_date, miss_bp, cum7_bp)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (date) DO NOTHING",
        [
            r["date"], datetime.now(timezone.utc).replace(tzinfo=None),
            r["base_date"], r["base"], r["est"], r["chg_bp"], r["thr_bp"], r["rank_pct"],
            r["alert_fixed"], r["alert_pct"], r["miss_date"], r["miss_bp"], r["cum7_bp"],
        ],
    )
    row = con.execute("SELECT reported FROM oas_alert_log WHERE date = ?", [r["date"]]).fetchone()
    return not bool(row[0])


def mark_reported(con: duckdb.DuckDBPyConnection, date: str) -> bool:
    """Mark date as reported; return whether a row exists for that date."""
    con.execute("UPDATE oas_alert_log SET reported = TRUE WHERE date = ?", [date])
    return con.execute("SELECT 1 FROM oas_alert_log WHERE date = ?", [date]).fetchone() is not None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OAS spike alert: estimate, log, print OAS lines")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--mark-reported", default=None)
    parser.add_argument("--send-risk", default=None)
    args = parser.parse_args(argv)
    if args.mark_reported is not None:
        args.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(args.db_path))
        try:
            ensure_alert_table(con)
            ok = mark_reported(con, args.mark_reported)
        finally:
            con.close()
        if ok:
            print(f"OAS {args.mark_reported} marked reported")
            return 0
        print(f"OAS {args.mark_reported} not found")
        return 1
    if args.send_risk is not None:
        args.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(args.db_path))
        try:
            ensure_alert_table(con)
            row = con.execute(
                "SELECT date, base_date, base, est, chg_bp, thr_bp, rank_pct,"
                " alert_fixed, alert_pct, cum7_bp FROM oas_alert_log WHERE date = ?",
                [args.send_risk],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            print(f"OAS {args.send_risk} not found")
            return 1
        keys = ["date", "base_date", "base", "est", "chg_bp", "thr_bp", "rank_pct",
                "alert_fixed", "alert_pct", "cum7_bp"]
        r = dict(zip(keys, row))
        print(f"OAS risk channel sent={send_risk_channel(r)}")
        return 0
    oas = load_fred(OAS_SERIES)
    px = load_etf_tr(["HYG", "IEI"])
    logret = np.log1p(px[["HYG", "IEI"]])
    r = compute(oas, logret)
    args.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.db_path))
    try:
        ensure_alert_table(con)
        inserted = record(con, r)
    finally:
        con.close()
    for line in format_lines(r, already=not inserted):
        print(line)
    if inserted and r["alert"]:
        print(f"OAS risk channel sent={send_risk_channel(r)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
