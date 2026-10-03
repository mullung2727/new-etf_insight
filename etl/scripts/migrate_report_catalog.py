"""기존 exports/stock_reports/ PDF를 report_documents 카탈로그에 편입(설계 §6-2).

원본 파일 이동·삭제·수정 없음. 기존 report_* 테이블은 읽기만(title 조회).
"""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

import argparse  # noqa: E402
import re  # noqa: E402
import sqlite3  # noqa: E402
from pathlib import Path  # noqa: E402

from download_naver_research import DEFAULT_EXPORT_BASE  # noqa: E402
from report_metrics import catalog, storage  # noqa: E402

_CODE_RE = re.compile(r"^\d{6}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_SOURCE = "legacy_file"
_BATCH_COMMIT = 100


def migrate(export_base: Path, con: sqlite3.Connection, *, now: str | None = None) -> dict:
    """export_base 아래 `*_<6자리코드>/*.pdf`를 카탈로그에 편입. 100건마다 commit."""
    stats = {"scanned": 0, "inserted_docs": 0, "dup_docs": 0,
             "skipped_known": 0, "skipped_bad_name": 0, "not_pdf": 0}
    export_base = Path(export_base)
    if not export_base.is_dir():
        return stats
    done = 0
    for d in sorted((p for p in export_base.iterdir() if p.is_dir()), key=lambda p: p.name):
        code = d.name.rsplit("_", 1)[1] if "_" in d.name else ""
        if not _CODE_RE.match(code):
            continue
        for pdf in sorted(d.glob("*.pdf"), key=lambda p: p.name):
            stem = pdf.stem
            if len(stem) <= 11 or stem[10] != "_" or not _DATE_RE.match(stem[:10]):
                stats["skipped_bad_name"] += 1
                continue
            parts = stem[11:].split("_", 1)
            if len(parts) != 2:
                stats["skipped_bad_name"] += 1
                continue
            broker, key = parts
            stats["scanned"] += 1
            pdf_rel = f"stock_reports/{d.name}/{pdf.name}"
            known = con.execute(
                "SELECT 1 FROM report_sources WHERE source = ? AND source_report_id = ?",
                (_SOURCE, pdf_rel),
            ).fetchone()
            if known is not None:
                stats["skipped_known"] += 1
                continue
            with open(pdf, "rb") as f:
                head = f.read(4)
            digest = catalog.sha256_file(pdf)
            file_status = "saved" if head == b"%PDF" else "not_pdf"
            if file_status == "not_pdf":
                stats["not_pdf"] += 1
            title = None
            try:
                trow = con.execute(
                    "SELECT title FROM report_api_facts WHERE pdf_key = ?", (key,),
                ).fetchone()
            except sqlite3.OperationalError:
                trow = None
            if trow is not None:
                title = trow[0]
            doc_id, created = catalog.upsert_document(
                con, document_type="company", title=title, broker=broker,
                published_date=stem[:10], pdf_path=pdf_rel, sha256=digest,
                pdf_key=key, file_status=file_status, now=now,
            )
            stats["inserted_docs" if created else "dup_docs"] += 1
            catalog.upsert_source(con, source=_SOURCE, source_report_id=pdf_rel,
                                  document_id=doc_id, now=now)
            catalog.upsert_document_stock(con, document_id=doc_id, stock_code=code,
                                          relation_type="primary", method="naver_itemcode")
            done += 1
            if done % _BATCH_COMMIT == 0:
                con.commit()
    con.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="기존 stock_reports PDF를 카탈로그에 편입")
    parser.add_argument("--export-base", type=Path, default=DEFAULT_EXPORT_BASE)
    parser.add_argument("--db-path", type=Path, default=storage.DEFAULT_DB)
    args = parser.parse_args()
    with storage.connect_rw(args.db_path) as con:
        catalog.init_catalog(con)
        stats = migrate(args.export_base, con)
    print(f"[report_catalog] scanned={stats['scanned']} inserted={stats['inserted_docs']} "
          f"dup={stats['dup_docs']} skipped_known={stats['skipped_known']} "
          f"bad_name={stats['skipped_bad_name']} not_pdf={stats['not_pdf']}")


if __name__ == "__main__":
    main()
