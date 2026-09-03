from __future__ import annotations

import csv
import random
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import duckdb

ETL_ROOT = Path(__file__).resolve().parents[1]
SQLITE_DB = ETL_ROOT / "db" / "watchlist.sqlite3"
KRX_DB = ETL_ROOT / "db" / "krx_ohlcv.duckdb"
OUT_DIR = ETL_ROOT / "runs" / "analysis"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def ymd(value: str) -> str:
    return "".join(ch for ch in value[:10] if ch.isdigit())


def pct(x: float) -> str:
    return f"{x * 100:.3f}%"


def load_trades() -> list[dict]:
    con = sqlite3.connect(f"file:{SQLITE_DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows: list[dict] = []
    queries = [
        (
            "close_bet",
            """
            SELECT ticker, COALESCE(created_at, date) entry_at, sold_at,
                   cntr_price buy_price, COALESCE(cntr_qty, qty) buy_qty,
                   sell_price, sell_qty, sell_pl_won,
                   exit_reason, raw, message
            FROM close_bet_orders
            WHERE sell_status='filled' AND sell_pl_won IS NOT NULL
              AND cntr_price > 0 AND sell_qty > 0
            """,
        ),
        (
            "pullback",
            """
            SELECT ticker, COALESCE(bought_at, signal_date) entry_at, sold_at,
                   buy_price, buy_qty, sell_price, sell_qty, sell_pl_won, exit_reason,
                   raw, message
            FROM pullback_orders
            WHERE sell_status='filled' AND sell_pl_won IS NOT NULL
              AND buy_price > 0 AND sell_qty > 0
            """,
        ),
    ]
    for strategy, sql in queries:
        for r in con.execute(sql):
            d = dict(r)
            d["strategy"] = strategy
            marker = f"{d.pop('raw', '') or ''} {d.pop('message', '') or ''}"
            d["account_mode"] = "mock" if "모의투자" in marker else ("live" if "KRX" in marker else "unknown")
            d["entry_date"] = ymd(str(d.pop("entry_at")))
            d["investment"] = int(d["buy_price"]) * int(d["buy_qty"])
            # sell_pl_won은 같은 날 같은 종목의 계좌 실현손익 합산값이 여러
            # lifecycle 행에 복제될 수 있다. 분류 영향은 체결가 기반 gross 수익률을
            # 주지표로 삼아 그 귀속 오류를 피한다.
            d["gross_return"] = (int(d["sell_price"]) - int(d["buy_price"])) / int(d["buy_price"])
            rows.append(d)
    con.close()
    return rows


def enrich(rows: list[dict]) -> None:
    con = duckdb.connect(str(KRX_DB), read_only=True)
    global_start = con.execute("SELECT MIN(date) FROM ohlcv").fetchone()[0]
    for r in rows:
        meta = con.execute(
            """
            WITH first_seen AS (
              SELECT MIN(date) first_date FROM ohlcv WHERE ticker=?
            ), at_entry AS (
              SELECT date cap_date, market_cap, market
              FROM ohlcv WHERE ticker=? AND date<=?
              ORDER BY date DESC LIMIT 1
            )
            SELECT first_date, cap_date, market_cap, market FROM first_seen, at_entry
            """,
            [r["ticker"], r["ticker"], r["entry_date"]],
        ).fetchone()
        if not meta:
            r.update(first_seen=None, cap_date=None, market_cap=None, market=None, listing_age_days=None, listing_age_censored=None)
            continue
        first_date, cap_date, cap, market = meta
        age = (datetime.strptime(r["entry_date"], "%Y%m%d") - datetime.strptime(first_date, "%Y%m%d")).days
        r.update(
            first_seen=first_date,
            cap_date=cap_date,
            market_cap=cap,
            market=market,
            listing_age_days=age,
            listing_age_censored=(first_date == global_start),
        )
    con.close()


def metrics(rows: list[dict]) -> dict:
    inv = sum(r["investment"] for r in rows)
    net = sum(r["sell_pl_won"] for r in rows)
    returns = [r["gross_return"] for r in rows]
    gross_pnl = sum(r["gross_return"] * r["investment"] for r in rows)
    return {
        "n": len(rows),
        "wins": sum(r["gross_return"] > 0 for r in rows),
        "win_rate": sum(r["gross_return"] > 0 for r in rows) / len(rows) if rows else 0,
        "investment": inv,
        "net": net,
        "weighted_return": gross_pnl / inv if inv else 0,
        "mean_return": statistics.mean(returns) if returns else 0,
        "median_return": statistics.median(returns) if returns else 0,
    }


def bootstrap_delta(rows: list[dict], predicate, rounds: int = 20000) -> tuple[float, float, float]:
    # 거래 단위 비모수 bootstrap: 필터 적용 후 평균 gross 수익률 - 전체 평균
    rng = random.Random(20260826)
    vals = []
    n = len(rows)
    if not n:
        return 0.0, 0.0, 0.0
    for _ in range(rounds):
        sample = [rows[rng.randrange(n)] for _ in range(n)]
        kept = [r for r in sample if not predicate(r)]
        if not kept:
            continue
        vals.append(metrics(kept)["mean_return"] - metrics(sample)["mean_return"])
    # predicate 가 모든 회차의 전 표본을 걸러내면 vals 가 빈다(넓은 임계값 + 작은 표본).
    # statistics.mean([]) 는 예외라 보고 생성 전체가 죽는다.
    if not vals:
        return 0.0, 0.0, 0.0
    vals.sort()
    return statistics.mean(vals), vals[int(len(vals) * 0.025)], vals[int(len(vals) * 0.975)]


def main() -> None:
    rows = load_trades()
    enrich(rows)
    fields = [
        "strategy", "account_mode", "ticker", "entry_date", "sold_at", "buy_price", "buy_qty", "sell_price", "sell_qty",
        "investment", "sell_pl_won", "gross_return", "exit_reason", "market", "market_cap", "cap_date",
        "first_seen", "listing_age_days", "listing_age_censored",
    ]
    csv_path = OUT_DIR / "listing_market_cap_trade_enriched_20260826.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows({k: r.get(k) for k in fields} for r in rows)

    covered = [r for r in rows if r["market_cap"] is not None and r["listing_age_days"] is not None]
    missing = [r for r in rows if r not in covered]
    lines = [
        "# 신규상장·소형주 실거래 영향 분석 (2026-08-26)", "",
        "- 대상: 실제 청산 완료 + 키움 순실현손익(sell_pl_won) 확정 거래",
        "- 진입일: 종가베팅 created_at, 눌림목 bought_at",
        "- 시총: 진입일 이하 KRX 최신 거래일 시가총액",
        "- 상장기간: KRX OHLCV 캐시(2024-08-01 시작)의 최초 관측일~진입일 달력일",
        "- 캐시 첫날부터 존재한 종목은 최소 2년 이상인 좌측 검열 표본으로 취급",
        "- 주지표: (매도가-매수가)/매수가의 거래별 gross 수익률. 거래별 자금 규모 변화와 실현손익 일괄귀속 오류를 피하려고 동일가중 평균을 사용",
        "- 주의: 수수료·세금 전 성과이며, 필터로 빠진 자리를 차순위 후보가 대체하는 효과는 반영하지 않은 사후 제외 분석",
        "",
        f"## 데이터 품질\n- 전체 {len(rows)}건 / KRX 메타데이터 결합 {len(covered)}건 / 결측 {len(missing)}건",
    ]
    if missing:
        lines.append("- 결측: " + ", ".join(f"{r['strategy']}:{r['ticker']}" for r in missing))
    modes = {mode: sum(r["account_mode"] == mode for r in covered) for mode in ("live", "mock", "unknown")}
    lines.append(f"- 주문 응답 표식 기준: KRX 실전 {modes['live']}건 / 모의투자 {modes['mock']}건 / 불명 {modes['unknown']}건")

    lines += ["", "## 기준 성과"]
    for label, subset in [("전체(메타 결합)", covered)] + [(s, [r for r in covered if r["strategy"] == s]) for s in ("close_bet", "pullback")]:
        m = metrics(subset)
        lines.append(f"- {label}: n={m['n']}, 승률={pct(m['win_rate'])}, 동일가중 평균={pct(m['mean_return'])}, 중앙값={pct(m['median_return'])}, 진입자금 가중 gross={pct(m['weighted_return'])}")

    live = [r for r in covered if r["account_mode"] == "live"]
    lines += ["", "## KRX 실전표시 거래만 별도 확인"]
    for label, subset in [("실전 전체", live)] + [(s, [r for r in live if r["strategy"] == s]) for s in ("close_bet", "pullback")]:
        m = metrics(subset)
        lines.append(f"- {label}: n={m['n']}, 승률={pct(m['win_rate'])}, 동일가중 평균={pct(m['mean_return'])}, 중앙값={pct(m['median_return'])}")
    for label, pred in [
        ("상장 180일 미만", lambda r: (not r["listing_age_censored"]) and r["listing_age_days"] < 180),
        ("시총 500억원 미만", lambda r: r["market_cap"] < 500 * 100_000_000),
        ("시총 1,000억원 미만", lambda r: r["market_cap"] < 1000 * 100_000_000),
        ("시총 2,000억원 미만", lambda r: r["market_cap"] < 2000 * 100_000_000),
    ]:
        ex = [r for r in live if pred(r)]
        kept = [r for r in live if not pred(r)]
        a, b, base = metrics(ex), metrics(kept), metrics(live)
        lines.append(f"- {label}: 대상 {a['n']}건 평균 {pct(a['mean_return'])} / 제외 후 {pct(b['mean_return'])} (변화 {pct(b['mean_return']-base['mean_return'])}p)")

    lines += ["", "## 신규상장 제외 민감도"]
    for days in [30, 60, 90, 180, 365]:
        pred = lambda r, d=days: (not r["listing_age_censored"]) and r["listing_age_days"] < d
        ex = [r for r in covered if pred(r)]
        kept = [r for r in covered if not pred(r)]
        bm, lo, hi = bootstrap_delta(covered, pred)
        me, mk, base = metrics(ex), metrics(kept), metrics(covered)
        lines.append(f"- 상장 {days}일 미만 제외: 제외 {me['n']}건 평균 {pct(me['mean_return'])}, 잔존 {mk['n']}건 평균 {pct(mk['mean_return'])}, 전체 대비 {pct(mk['mean_return']-base['mean_return'])}p, bootstrap 95% CI [{pct(lo)}p, {pct(hi)}p]")

    lines += ["", "## 시가총액 하한 민감도"]
    for cap_eok in [500, 1000, 2000, 3000, 5000]:
        cap = cap_eok * 100_000_000
        pred = lambda r, c=cap: r["market_cap"] < c
        ex = [r for r in covered if pred(r)]
        kept = [r for r in covered if not pred(r)]
        bm, lo, hi = bootstrap_delta(covered, pred)
        me, mk, base = metrics(ex), metrics(kept), metrics(covered)
        lines.append(f"- 시총 {cap_eok:,}억원 미만 제외: 제외 {me['n']}건 평균 {pct(me['mean_return'])}, 잔존 {mk['n']}건 평균 {pct(mk['mean_return'])}, 전체 대비 {pct(mk['mean_return']-base['mean_return'])}p, bootstrap 95% CI [{pct(lo)}p, {pct(hi)}p]")

    lines += ["", "## 전략별 핵심 구간"]
    for strategy in ("close_bet", "pullback"):
        ss = [r for r in covered if r["strategy"] == strategy]
        lines.append(f"### {strategy}")
        for label, pred in [
            ("상장 180일 미만", lambda r: (not r["listing_age_censored"]) and r["listing_age_days"] < 180),
            ("시총 1,000억원 미만", lambda r: r["market_cap"] < 1000 * 100_000_000),
            ("시총 2,000억원 미만", lambda r: r["market_cap"] < 2000 * 100_000_000),
        ]:
            ex = [r for r in ss if pred(r)]
            kept = [r for r in ss if not pred(r)]
            a, b, base = metrics(ex), metrics(kept), metrics(ss)
            lines.append(f"- {label}: 대상 {a['n']}건 평균 {pct(a['mean_return'])} / 제외 후 {pct(b['mean_return'])} (변화 {pct(b['mean_return']-base['mean_return'])}p)")

    lines += ["", "## 극단 손익 거래"]
    for r in sorted(covered, key=lambda x: x["gross_return"])[:10]:
        age = f"{r['listing_age_days']}일" + ("+" if r["listing_age_censored"] else "")
        lines.append(f"- {r['strategy']} {r['ticker']}: gross {pct(r['gross_return'])}, 시총 {r['market_cap']/100_000_000:,.0f}억원, 상장관측 {age}")

    report_path = OUT_DIR / "listing_market_cap_impact_20260826.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report_path)
    print(csv_path)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
