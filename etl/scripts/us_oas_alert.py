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

import argparse
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

OAS_SERIES = "BAMLH0A0HYM2"
FIXED_BP = 20.0
PCT_Q = 0.02
WIN = 252
MIN_N = 126
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "us_macro.duckdb"

_CREATE_ALERT_LOG = """
CREATE TABLE IF NOT EXISTS oas_alert_log (
  date VARCHAR PRIMARY KEY, run_at TIMESTAMP, base_date VARCHAR, base DOUBLE, est DOUBLE,
  chg_bp DOUBLE, thr_bp DOUBLE, rank_pct DOUBLE, alert_fixed BOOLEAN, alert_pct BOOLEAN,
  miss_date VARCHAR, miss_bp DOUBLE, reported BOOLEAN DEFAULT FALSE)
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
    return {
        "date": str(T),
        "base_date": base_date,
        "base": float(t["base"]),
        "est": float(t["oas"]),
        **out,
        "miss_date": miss_date,
        "miss_bp": miss_bp,
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
    )
    if r["miss_bp"] is not None:
        lines.append(f"OAS miss {r['miss_date']}: actual - est {r['miss_bp']:+.1f}bp")
    return lines


def ensure_alert_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_CREATE_ALERT_LOG)


def record(con: duckdb.DuckDBPyConnection, r: dict) -> bool:
    """Return True if this run should report r["date"].

    First run's values stay (no row update on rerun); report completion
    is marked with mark_reported.
    """
    con.execute(
        "INSERT INTO oas_alert_log (date, run_at, base_date, base, est, chg_bp, thr_bp,"
        " rank_pct, alert_fixed, alert_pct, miss_date, miss_bp)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (date) DO NOTHING",
        [
            r["date"], datetime.now(timezone.utc).replace(tzinfo=None),
            r["base_date"], r["base"], r["est"], r["chg_bp"], r["thr_bp"], r["rank_pct"],
            r["alert_fixed"], r["alert_pct"], r["miss_date"], r["miss_bp"],
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
