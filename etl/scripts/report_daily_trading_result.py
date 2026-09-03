"""눌림목·종가베팅의 지정일 실제 매도 체결 결과를 읽어 보고한다."""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  (cp949 가드 + path)

import argparse
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_WATCHLIST_DB = Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"


def _date_dash(value: str) -> str:
    text = value.strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError("date must be YYYYMMDD or YYYY-MM-DD")
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)


def load_filled_sells(db_path: Path, date_kst: str) -> list[dict]:
    """지정 KST 날짜에 실제 매도 체결된 두 전략 행을 공통 형태로 반환한다."""
    date_dash = _date_dash(date_kst)
    # 매수일 컬럼이 전략마다 다르다. 종가베팅은 당일 종가에 사므로 `date` 가 매수일이고,
    # 눌림목은 워치리스트 편입일과 매수일이 달라 `bought_at` 타임스탬프를 잘라 쓴다.
    specs = (
        ("close_bet_orders", "종가베팅", "cntr_price", "cntr_qty", "date", 1.0),
        ("pullback_orders", "눌림목", "buy_price", "buy_qty",
         "replace(substr(bought_at, 1, 10), '-', '')", 100.0),
    )
    rows: list[dict] = []
    with closing(_connect_ro(db_path)) as con:
        for table, strategy, buy_price_col, buy_qty_col, bought_date_expr, pnl_scale in specs:
            result = con.execute(
                f"""
                SELECT ticker, {buy_price_col}, {buy_qty_col}, sell_price, sell_qty,
                       sold_at, exit_reason, pnl_pct, sell_cmsn, sell_tax, sell_pl_won,
                       {bought_date_expr}
                FROM {table}
                WHERE sell_status='filled' AND substr(sold_at, 1, 10)=?
                ORDER BY sold_at, ticker
                """,
                (date_dash,),
            ).fetchall()
            for row in result:
                rows.append(
                    {
                        "strategy": strategy,
                        "ticker": row[0],
                        "buy_price": row[1],
                        "buy_qty": row[2],
                        "sell_price": row[3],
                        "sell_qty": row[4],
                        "sold_at": row[5],
                        "exit_reason": row[6],
                        "pnl_pct": None if row[7] is None else round(row[7] * pnl_scale, 10),
                        "sell_cmsn": row[8],
                        "sell_tax": row[9],
                        "sell_pl_won": row[10],
                        "bought_date": row[11],
                    }
                )
    return rows


def _empty_summary() -> dict:
    return {
        "count": 0,
        "invested_amount": 0,
        "sell_amount": 0,
        "sell_cmsn": 0,
        "sell_tax": 0,
        "sell_pl_won": 0,
        "unconfirmed_count": 0,
        "investment_unconfirmed_count": 0,
        "return_pct": None,
    }


def summarize_trades(rows: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """전략별·전체 실제 금액을 합산하고 실제값 누락·전략 중복을 경고한다."""
    summary = {"종가베팅": _empty_summary(), "눌림목": _empty_summary(), "전체": _empty_summary()}
    warnings: list[str] = []
    strategies_by_ticker: dict[str, set[str]] = {}
    for row in rows:
        strategies_by_ticker.setdefault(row["ticker"], set()).add(row["strategy"])
        invested_amount = (
            row["buy_price"] * row["sell_qty"]
            if row["buy_price"] is not None and row["sell_qty"] is not None
            else None
        )
        sell_amount = (
            row["sell_price"] * row["sell_qty"]
            if row["sell_price"] is not None and row["sell_qty"] is not None
            else None
        )
        missing_actual = any(row[field] is None for field in ("sell_cmsn", "sell_tax", "sell_pl_won"))
        for key in (row["strategy"], "전체"):
            bucket = summary[key]
            bucket["count"] += 1
            if invested_amount is None:
                bucket["investment_unconfirmed_count"] += 1
            else:
                bucket["invested_amount"] += invested_amount
            if sell_amount is not None:
                bucket["sell_amount"] += sell_amount
            for field in ("sell_cmsn", "sell_tax", "sell_pl_won"):
                if row[field] is not None:
                    bucket[field] += row[field]
            if missing_actual:
                bucket["unconfirmed_count"] += 1
        if missing_actual:
            warnings.append(f"{row['strategy']} {row['ticker']}: 실제 비용/손익 미확정")
    for ticker, strategies in sorted(strategies_by_ticker.items()):
        if len(strategies) > 1:
            warnings.append(f"{ticker}: 두 전략에 동시에 포함되어 합계 중복 가능")
    for bucket in summary.values():
        if (
            bucket["invested_amount"] > 0
            and bucket["investment_unconfirmed_count"] == 0
            and bucket["unconfirmed_count"] == 0
        ):
            bucket["return_pct"] = bucket["sell_pl_won"] / bucket["invested_amount"] * 100
    return summary, warnings


def _won(value: int | None, *, signed: bool = False) -> str:
    if value is None:
        return "미확정"
    return f"{value:+,}원" if signed else f"{value:,}원"


def format_report(
    date_kst: str, rows: list[dict], summary: dict[str, dict], warnings: list[str]
) -> str:
    """최종 순손익과 투자원금을 먼저 보여주는 Discord용 요약을 만든다."""
    date_dash = _date_dash(date_kst)
    lines = [f"[오늘 매매 결과] {date_dash}"]
    if not rows:
        lines.append("오늘 실제 매도 체결 없음")
        return "\n".join(lines)

    total = summary["전체"]
    actual_unknown = total["unconfirmed_count"] > 0
    investment_unknown = total["investment_unconfirmed_count"] > 0
    pnl_text = "미확정" if actual_unknown else _won(total["sell_pl_won"], signed=True)
    invested_text = "미확정" if investment_unknown else _won(total["invested_amount"])
    return_text = "미확정" if total["return_pct"] is None else f"{total['return_pct']:+.2f}%"
    lines.extend(
        [
            f"최종 순실현손익: {pnl_text}",
            f"투자원금: {invested_text}",
            f"투자원금 대비 손익률: {return_text}",
        ]
    )

    if actual_unknown:
        lines.append(f"실제 비용/손익 미확정 {total['unconfirmed_count']}건")
    else:
        lines.append(f"실비용: 수수료 {_won(total['sell_cmsn'])} / 세금 {_won(total['sell_tax'])}")

    lines.append("")
    lines.append("전략별 요약")
    for key in ("종가베팅", "눌림목"):
        bucket = summary[key]
        if bucket["count"] == 0:
            continue
        bucket_invested = (
            "미확정"
            if bucket["investment_unconfirmed_count"]
            else _won(bucket["invested_amount"])
        )
        bucket_pnl = (
            "미확정"
            if bucket["unconfirmed_count"]
            else _won(bucket["sell_pl_won"], signed=True)
        )
        bucket_return = (
            "미확정"
            if bucket["return_pct"] is None
            else f"{bucket['return_pct']:+.2f}%"
        )
        lines.append(f"- {key}: 투자원금 {bucket_invested} / 순손익 {bucket_pnl} ({bucket_return})")

    lines.extend(f"- ⚠️ {warning}" for warning in warnings)
    return "\n".join(lines)


MATCH_LABEL = {
    "same_sustained": "매수근거 그대로",
    "same_plus_new": "매수근거 + 새 재료",
    "different": "매수근거와 다름",
}
GRADE_WARNING_PREFIXES = (
    "downgraded_", "hallucinated_ref", "grade_failed", "grade_unstable", "grade_partial",
)


def format_cause_section(evidence: dict) -> str:
    """등락 원인 블록.

    등급 분포를 항상 머리에 찍는다. E가 계속 대부분이면 이 분석은 쓸모가 없다는 뜻이고,
    A가 갑자기 늘면 LLM이 근거를 부풀린다는 뜻이다. 사람이 보고 판단할 감시 지표다.

    등급은 한 종목을 여러 번 판정한 중앙값이다. 회차 간 등급이 두 칸 이상 벌어진 종목은
    `grade_unstable` 경고로 따로 올라온다. 그런 종목이 흔해지면 프롬프트가 애매한 것이다.
    """
    counts = evidence.get("grade_counts") or {}
    grades = sorted(counts) or ["A", "B", "C", "D", "E"]
    header = " / ".join(f"{grade} {counts.get(grade, 0)}" for grade in grades)
    lines = ["", f"등락 원인 ({header})"]

    def sort_key(record: dict) -> float:
        pnl = record["trades"][0].get("pnl_pct") if record["trades"] else None
        return -(pnl if pnl is not None else 0.0)

    for record in sorted(evidence["tickers"], key=sort_key):
        label = record.get("name") or record["ticker"]
        pnl = record["trades"][0].get("pnl_pct") if record["trades"] else None
        pnl_text = "손익 미확정" if pnl is None else f"{pnl:+.2f}%"
        judgement = record.get("judgement")
        if not judgement:
            lines.append(f"- [–] {label} {pnl_text} · 판정 실패")
            continue
        parts = [f"- [{judgement['grade']}] {label} {pnl_text} · {judgement['cause']}"]
        match = MATCH_LABEL.get(judgement.get("buy_rationale_match"))
        if match:
            parts.append(f" ({match})")
        lines.append("".join(parts))

    lines.extend(
        f"- ⚠️ {warning}"
        for warning in evidence.get("warnings", [])
        if warning.startswith(GRADE_WARNING_PREFIXES)
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="당일 실제 매도 체결 결과 보고")
    parser.add_argument("--date", help="YYYYMMDD 또는 YYYY-MM-DD; 기본값은 오늘 KST")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--cause", action="store_true", help="등락 원인·등급 분석을 덧붙인다")
    parser.add_argument("--model", default=None, help="원인 판정 LLM 모델; 생략하면 기존 기본값")
    parser.add_argument(
        "--repeat", type=int, default=None,
        help="종목당 원인 판정 횟수. 중앙값을 쓴다. 생략하면 기본값",
    )
    args = parser.parse_args(argv)
    date_kst = args.date or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    rows = load_filled_sells(args.watchlist_db, date_kst)
    summary, warnings = summarize_trades(rows)
    report = format_report(date_kst, rows, summary, warnings)

    if args.cause and rows:
        # 원인 분석은 네트워크·LLM에 기대므로 통째로 실패할 수 있다. 정산 숫자는
        # 그와 무관하게 반드시 나가야 하므로 여기서 막고 경고 한 줄로 대체한다.
        try:
            from collect_trading_result_evidence import (
                collect_evidence,
                grade_evidence,
                save_causes,
            )

            repeat_kwargs = {} if args.repeat is None else {"repeat": args.repeat}
            evidence = grade_evidence(
                collect_evidence(date_kst, watchlist_db=args.watchlist_db),
                model=args.model,
                **repeat_kwargs,
            )
            # 보고문에만 찍고 버리면 이슈별 지속성 비교를 나중에 못 한다.
            save_causes(args.watchlist_db, evidence, model=args.model)
            report = f"{report}\n{format_cause_section(evidence)}"
        except Exception as exc:  # noqa: BLE001 — 정산 보고를 죽이지 않는다
            report = f"{report}\n- ⚠️ 등락 원인 분석 실패: {type(exc).__name__}"

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
