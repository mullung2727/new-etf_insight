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
from research.backtest_daily.data_us import load_etf_tr, load_fred, load_index  # noqa: E402
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
REGIME_GAP = 0.14        # QQQ 200일선 이격 14% 이상 = 과열권
REGIME_V10 = 0.15        # VIX 10거래일 +15% 이상
REGIME_Y20_BP = 40.0     # 10년물 20거래일 +40bp 이상
REGIME_ICY = 0.10        # 실업수당 4주평균 전년비 +10% 이상
ICNSA_LAG_DAYS = 6       # 관측일(토) → 다음 목요일 발표
REGIME_HOLD = 6  # 조건이 6거래일 연속 꺼져야 "꺼짐"으로 봄 (리서치 묶음 기준: 5일 이하 끊김 병합)

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


def regime_frame(qqq_ret, vix, dgs10, icnsa) -> pd.DataFrame:
    """나스닥 국면 표지 프레임 (순수, no I/O). index는 QQQ 날짜 문자열 오름차순."""
    q = qqq_ret.sort_index()
    idx = q.index
    level = (1 + q.fillna(0)).cumprod()
    gap = level / level.rolling(200).mean() - 1
    q10 = level / level.shift(10) - 1
    vx = vix.reindex(idx).ffill()
    v10 = vx / vx.shift(10) - 1
    y = dgs10.reindex(idx).ffill().shift(1)
    y20 = (y - y.shift(20)) * 100
    ic = icnsa.sort_index()
    ic4 = ic.rolling(4).mean()
    icy_w = ic4 / ic4.shift(52) - 1
    w_dt = pd.to_datetime(icy_w.index, format="%Y%m%d") + pd.Timedelta(days=ICNSA_LAG_DAYS)
    icy_w2 = pd.Series(icy_w.values, index=w_dt).sort_index()
    d_dt = pd.to_datetime(idx, format="%Y%m%d")
    icy_daily = icy_w2.reindex(d_dt, method="ffill")
    icy_daily.index = idx
    icy = icy_daily
    overheat_vix = (gap >= REGIME_GAP) & (q10 > 0) & (v10 >= REGIME_V10)
    rec_rate = (gap >= REGIME_GAP) & (y20 >= REGIME_Y20_BP)
    rec_jobs = (gap >= REGIME_GAP) & (icy >= REGIME_ICY)
    recovery = rec_rate | rec_jobs
    frame = pd.DataFrame({
        "gap": gap,
        "q10": q10,
        "v10": v10,
        "y20": y20,
        "icy": icy,
        "overheat_vix": overheat_vix,
        "rec_rate": rec_rate,
        "rec_jobs": rec_jobs,
        "recovery": recovery,
    })
    return frame[["gap", "q10", "v10", "y20", "icy", "overheat_vix", "rec_rate", "rec_jobs", "recovery"]]


def regime_events(frame, asof) -> list[dict]:
    """묶음 상태(sm) 비교로 국면 이벤트 추출 (순수, no I/O)."""
    sub = frame[frame.index <= asof]
    if len(sub) < 2:
        return []
    today_idx = sub.index[-1]
    today = sub.iloc[-1]
    row = {k: today[k] for k in ["gap", "q10", "v10", "y20", "icy", "rec_rate", "rec_jobs"]}
    ov_sm = sub["overheat_vix"].astype(bool).astype(int).rolling(REGIME_HOLD, min_periods=1).max().astype(bool)
    rec_sm = sub["recovery"].astype(bool).astype(int).rolling(REGIME_HOLD, min_periods=1).max().astype(bool)
    ov_today, ov_yest = bool(ov_sm.iloc[-1]), bool(ov_sm.iloc[-2])
    rec_today, rec_yest = bool(rec_sm.iloc[-1]), bool(rec_sm.iloc[-2])
    events = []
    if ov_today and not ov_yest:
        events.append({"kind": "overheat_vix", "date": str(today_idx), "row": row})
    if rec_today and not rec_yest:
        events.append({"kind": "recovery_on", "date": str(today_idx), "row": row})
    if not rec_today and rec_yest:
        events.append({"kind": "recovery_off", "date": str(today_idx), "row": row})
    return events


def format_regime_alert(ev) -> str:
    """국면 표지 알림 문구 (순수, no I/O)."""
    kind = ev["kind"]
    date = _fmt_ymd(ev["date"])
    row = ev["row"]

    def _pct(x):
        try:
            if x is None or bool(pd.isna(x)):
                return "n/a"
        except Exception:
            return "n/a"
        return f"{float(x) * 100:+.1f}"

    def _pp(x):
        try:
            if x is None or bool(pd.isna(x)):
                return "n/a"
        except Exception:
            return "n/a"
        return f"{float(x) / 100:+.2f}"

    if kind == "overheat_vix":
        gap = _pct(row.get("gap"))
        q10 = _pct(row.get("q10"))
        v10 = _pct(row.get("v10"))
        return "\n".join([
            f"🔴 [주의] 나스닥 과열 중 공포지수 동반 상승 — 카나리 ({date} 미국장 기준)",
            f"- 무슨 일: 나스닥100(QQQ)이 200일 평균보다 {gap}% 위(과열권 = +14% 이상)인데, "
            f"최근 10거래일 주가 {q10}%와 공포지수(VIX) {v10}%가 같이 올랐음",
            "- 왜 이상한가: 보통 주가가 오르면 VIX는 내려감. 같이 오른다 = 오르면서도 시장이 불안해함",
            "- 과거(2000~2026) 이런 시작 16번 중 6번이 60거래일 안에 −10% 이상 하락(약 36%, 과열권 평소 약 18%). "
            "예: 2000-02·2007-10·2010-04·2012-04·2020-01(코로나 직전)·2020-08",
            "- 단 120거래일 뒤엔 평균 +10% 안팎으로 회복 → 길게 피하라는 신호는 아님, 두 달쯤 흔들릴 수 있다는 경고",
            "- 매매 규칙(v38)은 바뀌지 않음. 검증 전 관찰용 (근거: research/private/rejected/LEADS.md 2026-11-22 절)",
        ])
    if kind == "recovery_on":
        gap = _pct(row.get("gap"))
        lines = [
            f"🟢 [참고] 나스닥 회복기 표지 켜짐 ({date} 미국장 기준)",
            f"- 무슨 일: 나스닥100(QQQ)이 200일 평균보다 {gap}% 위(과열권 = +14% 이상)이면서",
        ]
        if bool(row.get("rec_rate")):
            lines.append(f"  · 10년물 국채금리가 20거래일 새 {_pp(row.get('y20'))}%p 급등 (기준 +0.40%p)")
        if bool(row.get("rec_jobs")):
            lines.append(f"  · 실업수당 청구(4주 평균)가 1년 전보다 {_pct(row.get('icy'))}% 많음 (기준 +10%)")
        lines.extend([
            "- 뜻: 과거엔 대개 폭락 뒤 빠른 회복기(2003·2009·2020·2023)에 나온 조합. "
            "금리 급등·실업 증가가 나쁜 뉴스 같아도 이 국면에선 큰 하락이 드물었음",
            "- 과거(2000~2026) 이 조건인 날 60거래일 안 −10% 이상 하락: 금리 쪽 6%, 실업 쪽 3% (과열권 평소 약 20%)",
            "- 매매 규칙(v38)은 바뀌지 않음. 관찰용, 사례 적음(금리 9번·실업 5번). 꺼지면(6거래일 연속 조건 밖) 다시 알림",
        ])
        return "\n".join(lines)
    if kind == "recovery_off":
        gap_v = row.get("gap")
        try:
            gap_is_low = gap_v is not None and not bool(pd.isna(gap_v)) and float(gap_v) < REGIME_GAP
        except Exception:
            gap_is_low = False
        if gap_is_low:
            reason = f"나스닥100 200일선 이격이 {_pct(gap_v)}%로 과열권(+14%) 아래로 내려옴"
        else:
            reason = (
                f"금리 급등·실업 증가 조건이 풀림 (10년물 20거래일 {_pp(row.get('y20'))}%p, "
                f"실업수당 전년비 {_pct(row.get('icy'))}%)"
            )
        return "\n".join([
            f"⚪ [참고] 나스닥 회복기 표지 꺼짐 — 6거래일 연속 조건 밖 ({date} 미국장 기준)",
            f"- 이유: {reason}",
            "- 뜻: 회복기 특유의 '하락 드묾' 근거가 사라짐. 이제 평소 기준으로 보면 됨",
            "- 매매 규칙(v38)은 바뀌지 않음. 관찰용",
        ])
    raise ValueError(f"unknown regime kind: {kind}")


def send_regime_alerts(date) -> list[str]:
    """국면 표지 이벤트를 전용 채널로 전송. ASCII 상태 줄 리스트 반환."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    qqq_ret = load_etf_tr(["QQQ"])["QQQ"]
    vix = load_index("^VIX")
    dgs10 = load_fred("DGS10")
    icnsa = load_fred("ICNSA")
    frame = regime_frame(qqq_ret, vix, dgs10, icnsa)
    events = regime_events(frame, date)
    lines = []
    for ev in events:
        ok = send_strategy_alert(format_regime_alert(ev), RISK_WEBHOOK_ENV)
        lines.append(f"OAS regime {ev['kind']} sent={bool(ok)}")
    if not events:
        sub = frame[frame.index <= date]
        if len(sub) == 0:
            gap_s, ov, rec = "n/a", "off", "off"
        else:
            today = sub.iloc[-1]
            g = today["gap"]
            if g is None or bool(pd.isna(g)):
                gap_s = "n/a"
            else:
                gap_s = f"{float(g):+.1%}"
            ov = "on" if bool(today["overheat_vix"]) else "off"
            rec = "on" if bool(today["recovery"]) else "off"
        lines.append(f"OAS regime {date} gap {gap_s} overheat_vix={ov} recovery={rec}")
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
    parser.add_argument("--send-regime", default=None)
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
    if args.send_regime is not None:
        for line in send_regime_alerts(args.send_regime):
            print(line)
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
    if inserted:
        try:
            for line in send_regime_alerts(r["date"]):
                print(line)
        except Exception as e:
            print(f"OAS regime failed: {e!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
