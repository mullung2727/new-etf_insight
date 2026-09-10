"""증권사 리포트 PDF → report_metrics.sqlite3 적재 + 상승여력/목표가 변화율 출력.

리포트 1건 = PDF 1개를 독립 파싱한다. 직전 목표가는 먼저 적재된 결과를 DB 에서 찾으므로
날짜 오름차순으로 처리한다. 같은 parser_version 으로 이미 적재된 건은 건너뛴다(멱등, 신규분만).
배치 스케줄러에는 아직 등록하지 않았다(PLAN §2.6) — 다운로드 배치 뒤에 이 스크립트를 부르면 된다.

Usage (from etl/):
    uv run python scripts/run_report_metrics.py                     # 미적재 전체
    uv run python scripts/run_report_metrics.py --since 2026-09-01 --stock 095570
    uv run python scripts/run_report_metrics.py --limit 20 --db-path C:/tmp/t.sqlite3
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + path)

import argparse  # noqa: E402
from datetime import date  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Iterable, Optional  # noqa: E402

from download_naver_research import DEFAULT_EXPORT_BASE  # noqa: E402
from report_metrics import storage  # noqa: E402
from report_metrics.metrics import report_upside, resolve_price_now, target_revision  # noqa: E402
from report_metrics.models import (  # noqa: E402
    PARSER_VERSION,
    STATUS_IMAGE_PDF,
    STATUS_NO_TARGET,
    STATUS_OK,
    STATUS_PARSE_ERROR,
    ReportFacts,
)
from report_metrics.parse import parse_path, parse_report  # noqa: E402

_STATUS_BUCKET = {
    STATUS_OK: "ok",
    STATUS_NO_TARGET: "no_target",
    STATUS_IMAGE_PDF: "unsupported",
    STATUS_PARSE_ERROR: "errors",
}


def list_report_paths(
    reports_dir: Path, stock: Optional[str] = None,
    since: Optional[str] = None, until: Optional[str] = None,
) -> list[Path]:
    """`{종목}_{코드}/{날짜}_...pdf` 목록을 발간일 오름차순으로."""
    out = []
    for path in Path(reports_dir).glob("*/*.pdf"):
        if stock and not path.parent.name.endswith(f"_{stock}"):
            continue
        day = path.name[:10]
        if (since and day < since) or (until and day > until):
            continue
        out.append(path)
    return sorted(out, key=lambda p: (p.name[:10], p.name))


def run(
    paths: Iterable[Path],
    db_path: Path = storage.DEFAULT_DB,
    *,
    as_of: Optional[str] = None,
    krx_db_path: Optional[Path] = None,
    reparse: bool = False,
    limit: Optional[int] = None,
    parse_fn: Callable = parse_report,
    price_fn: Callable = resolve_price_now,
) -> dict[str, Any]:
    storage.init_db(db_path)
    stats: dict[str, Any] = {"scanned": 0, "parsed": 0, "skipped_existing": 0,
                             "ok": 0, "no_target": 0, "unsupported": 0, "errors": 0}
    metrics: list[dict[str, Any]] = []
    with storage.connect_rw(db_path) as con:
        done = set() if reparse else {
            row[0] for row in con.execute(
                "SELECT pdf_key FROM report_facts WHERE parser_version = ?", (PARSER_VERSION,))
        }
        for path in paths:
            if limit is not None and stats["parsed"] >= limit:
                break
            stats["scanned"] += 1
            try:
                meta = parse_path(path)
            except ValueError:
                stats["errors"] += 1      # 규칙 밖 파일명은 pdf_key 가 없어 저장 불가
                continue
            if meta["pdf_key"] in done:
                stats["skipped_existing"] += 1
                continue
            try:
                facts, estimates = parse_fn(path)
            except Exception as exc:      # 한 건 실패로 배치 전체를 멈추지 않는다
                facts = ReportFacts(pdf_path=str(path), parse_status=STATUS_PARSE_ERROR,
                                    parse_error=f"{type(exc).__name__}: {exc}"[:500], **meta)
                estimates = []
            storage.upsert_facts(con, facts)
            storage.replace_estimates(con, facts.pdf_key, estimates)
            con.commit()                  # 건별 커밋 — 중간에 끊겨도 처리분은 남는다
            stats["parsed"] += 1
            stats[_STATUS_BUCKET.get(facts.parse_status, "errors")] += 1

            if facts.parse_status != STATUS_OK:
                continue
            prev = storage.find_previous_report(con, facts.stock_code, facts.broker, facts.report_date)
            price_now = price_fn(facts.stock_code, as_of, krx_db_path) if as_of else None
            metrics.append({
                "pdf_key": facts.pdf_key,
                "stock_code": facts.stock_code,
                "stock_name": facts.stock_name,
                "broker": facts.broker,
                "report_date": facts.report_date,
                "target_price": facts.target_price,
                **report_upside(facts, price_now),
                **target_revision(facts, prev),
            })
    stats["metrics"] = metrics
    return stats


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{value * 100:+.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="증권사 리포트 목표가/추정치 파싱 적재")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_EXPORT_BASE)
    parser.add_argument("--db-path", type=Path, default=storage.DEFAULT_DB)
    parser.add_argument("--stock", help="종목코드 6자리")
    parser.add_argument("--since", help="발간일 YYYY-MM-DD 이후")
    parser.add_argument("--until", help="발간일 YYYY-MM-DD 이전")
    parser.add_argument("--limit", type=int, help="새로 파싱할 최대 건수")
    parser.add_argument("--as-of", default=date.today().isoformat(),
                        help="현재 기준 상승여력의 주가 기준일(기본 오늘, 휴장일이면 직전 거래일)")
    parser.add_argument("--reparse", action="store_true", help="이미 적재된 건도 다시 파싱")
    args = parser.parse_args()

    paths = list_report_paths(args.reports_dir, args.stock, args.since, args.until)
    stats = run(paths, args.db_path, as_of=args.as_of, reparse=args.reparse, limit=args.limit)
    for m in stats["metrics"]:
        print(f"{m['report_date']} {m['stock_name']}({m['stock_code']}) {m['broker']} "
              f"목표가={m['target_price']:,} 발간시점상승여력={_pct(m['upside_at_report'])} "
              f"현재상승여력={_pct(m['upside_now'])} 직전대비={m['direction']} {_pct(m['change_pct'])}")
    print(f"[report_metrics] scanned={stats['scanned']} parsed={stats['parsed']} "
          f"skipped={stats['skipped_existing']} ok={stats['ok']} no_target={stats['no_target']} "
          f"unsupported={stats['unsupported']} errors={stats['errors']}")


if __name__ == "__main__":
    main()
