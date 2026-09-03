from __future__ import annotations

import csv
import statistics
from pathlib import Path

import duckdb

ETL_ROOT = Path(__file__).resolve().parents[1]
INPUT = ETL_ROOT / "runs" / "analysis" / "listing_market_cap_trade_enriched_20260826.csv"
OUTPUT = ETL_ROOT / "runs" / "analysis" / "execution_risk_filter_impact_20260826.md"
KRX_DB = ETL_ROOT / "db" / "krx_ohlcv.duckdb"


def pct(value: float) -> str:
    return f"{value * 100:.3f}%"


def load() -> list[dict]:
    with INPUT.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for key in ("investment", "market_cap"):
            r[key] = int(r[key])
        r["gross_return"] = float(r["gross_return"])
    return rows


def enrich_liquidity(rows: list[dict]) -> None:
    con = duckdb.connect(str(KRX_DB), read_only=True)
    for r in rows:
        values = [x[0] for x in con.execute(
            """
            SELECT trading_value
            FROM ohlcv
            WHERE ticker=? AND date<? AND trading_value IS NOT NULL
            ORDER BY date DESC LIMIT 20
            """,
            [r["ticker"], r["entry_date"]],
        ).fetchall()]
        recent5 = values[:5]
        r["adtv20"] = statistics.median(values) if values else None
        r["adtv5"] = statistics.median(recent5) if recent5 else None
        r["position_to_adtv20"] = r["investment"] / r["adtv20"] if r["adtv20"] else None
        r["turnover20"] = r["adtv20"] / r["market_cap"] if r["adtv20"] else None
    con.close()


def metrics(rows: list[dict]) -> dict:
    returns = [r["gross_return"] for r in rows]
    return {
        "n": len(rows),
        "avg": statistics.mean(returns) if returns else 0,
        "median": statistics.median(returns) if returns else 0,
        "win": sum(x > 0 for x in returns) / len(returns) if returns else 0,
    }


def describe_filter(rows: list[dict], pred) -> tuple[dict, dict, float]:
    hit = [r for r in rows if pred(r)]
    kept = [r for r in rows if not pred(r)]
    base = metrics(rows)
    hm, km = metrics(hit), metrics(kept)
    return hm, km, km["avg"] - base["avg"]


def line(label: str, rows: list[dict], pred) -> str:
    hit, kept, delta = describe_filter(rows, pred)
    return (
        f"- {label}: 걸림 {hit['n']}건, 걸린 종목 평균 {pct(hit['avg'])} "
        f"(승률 {pct(hit['win'])}, 중앙값 {pct(hit['median'])}); "
        f"제외 후 {kept['n']}건 평균 {pct(kept['avg'])}, 변화 {pct(delta)}p"
    )


def main() -> None:
    rows = load()
    enrich_liquidity(rows)
    covered = [r for r in rows if r["adtv20"] is not None and r["adtv20"] > 0]
    live = [r for r in covered if r["account_mode"] == "live"]
    close = [r for r in covered if r["strategy"] == "close_bet"]
    pullback = [r for r in covered if r["strategy"] == "pullback"]

    filters = [
        ("20일 중위 거래대금 10억원 미만", lambda r: r["adtv20"] < 10 * 100_000_000),
        ("20일 중위 거래대금 30억원 미만", lambda r: r["adtv20"] < 30 * 100_000_000),
        ("20일 중위 거래대금 50억원 미만", lambda r: r["adtv20"] < 50 * 100_000_000),
        ("20일 중위 거래대금 100억원 미만", lambda r: r["adtv20"] < 100 * 100_000_000),
        ("주문금액/20일 중위 거래대금 0.1% 초과", lambda r: r["position_to_adtv20"] > 0.001),
        ("주문금액/20일 중위 거래대금 0.5% 초과", lambda r: r["position_to_adtv20"] > 0.005),
        ("20일 중위 회전율 0.5% 미만", lambda r: r["turnover20"] < 0.005),
        ("20일 중위 회전율 1.0% 미만", lambda r: r["turnover20"] < 0.01),
    ]

    lines = [
        "# 체결 위험 필터 영향 분석 (2026-08-26)", "",
        "- 주지표: 매수가·매도가 기반 거래별 gross 수익률의 동일가중 평균",
        "- 유동성 입력: 진입일을 제외한 직전 최대 20거래일의 KRX 거래대금 중위값(미래정보 방지)",
        f"- 전체 {len(rows)}건 중 유동성 결합 {len(covered)}건; 실전표시 {len(live)}건",
        "- 주문금액은 원장 매수가×매수수량. 필터 제외 후 차순위 종목 대체 효과는 미반영",
        "", "## 전체 거래", 
    ]
    lines.extend(line(label, covered, pred) for label, pred in filters)

    lines += ["", "## 종가베팅"]
    lines.extend(line(label, close, pred) for label, pred in filters[:4])
    lines += ["", "## 눌림목"]
    lines.extend(line(label, pullback, pred) for label, pred in filters[:4])
    lines += ["", "## KRX 실전표시 거래"]
    lines.extend(line(label, live, pred) for label, pred in filters)

    lines += ["", "## 20일 중위 거래대금 30억원 미만 종목"]
    for r in sorted((x for x in covered if x["adtv20"] < 30 * 100_000_000), key=lambda x: x["gross_return"]):
        lines.append(
            f"- {r['strategy']} {r['ticker']} ({r['account_mode']}): "
            f"gross {pct(r['gross_return'])}, ADTV20 {r['adtv20']/100_000_000:,.1f}억원, "
            f"주문/ADTV {pct(r['position_to_adtv20'])}"
        )

    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(OUTPUT)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
