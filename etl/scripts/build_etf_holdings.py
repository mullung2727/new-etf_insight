"""TIME 4 ETF 일별 보유 크롤 + 이벤트 검출 + Discord 알림.

- ETF: 426030(idx2) 385720(idx11) 495060(idx15) 456600(idx6)
- 저장: etl/db/etf_insight.sqlite3 (etf_holdings_daily, etf_holding_events)
- 실행: --daily (당일, 알림) | --backfill YYYYMMDD YYYYMMDD [--notify] | --import-csv PATH ETF [--notify]
- 이벤트: 5% 진입 / 3% 이탈 히스테리시스, 현금 행 제외, 매 실행 시 ETF별 전체 재계산. 알림은 확신주 관련만(5%+ 편입, 5%돌파, 3%이탈, 확신주 편출).

cert verify off: timefolioetf.co.kr 인증서 만료(2026-10 확인).
"""
from __future__ import annotations

import re
import sqlite3
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv

load_dotenv()

import notify as notify_mod

ETFS = {"426030": "2", "385720": "11", "495060": "15", "456600": "6"}
UA = {"User-Agent": "Mozilla/5.0"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
DB = Path(__file__).resolve().parent.parent / "db" / "etf_insight.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_holdings_daily(
  asof TEXT, etf TEXT, code TEXT, name TEXT, qty REAL, amt_krw REAL,
  weight_pct REAL, fetched_at TEXT,
  PRIMARY KEY(asof, etf, code));
CREATE TABLE IF NOT EXISTS etf_holding_events(
  date TEXT, etf TEXT, code TEXT, name TEXT, kind TEXT, w_prev REAL, w_cur REAL,
  PRIMARY KEY(date, etf, code, kind));
CREATE INDEX IF NOT EXISTS idx_holding_events_date ON etf_holding_events(date, etf);
"""


def fetch(idx: str, asof: str) -> tuple[str, list[tuple]]:
    """asof YYYY-MM-DD -> (echo_date, rows(code,name,qty,amt,w))."""
    url = f"https://timefolioetf.co.kr/m11_view.php?idx={idx}&pdfDate={asof}"
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=30, context=CTX
    ).read().decode("utf-8", "replace")
    echo = re.search(r'name="pdfDate" value="([\d-]+)"', raw)
    seg = re.search(r'id="constituentItems"(.*)', raw, re.S)
    rows = re.findall(
        r"<tr>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>",
        seg.group(1)[:150000] if seg else "",
    )
    return (echo.group(1) if echo else "?"), [tuple(c.strip() for c in r) for r in rows]


def open_db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB))
    con.executescript(SCHEMA)
    return con


def store(con: sqlite3.Connection, etf: str, asof: str, rows: list[tuple]) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    vals = [(asof, etf, c, n, float(q.replace(",", "") or 0), float(a.replace(",", "") or 0),
             float(w.replace(",", "") or 0), now) for c, n, q, a, w in rows]
    return con.executemany(
        "INSERT OR IGNORE INTO etf_holdings_daily VALUES (?,?,?,?,?,?,?,?)", vals).rowcount


def walk_events(snaps: list[tuple[str, dict]]) -> dict[str, list[tuple]]:
    """snaps: [(asof, {code: (name, w)}), ...] asof 오름차순.
    Returns {asof: [(code, name, kind, w_prev, w_cur, alert), ...]} (이벤트 있는 날만)."""
    inside: dict[str, bool] = {}
    out: dict[str, list[tuple]] = {}
    prev: dict | None = None
    for asof, cur in snaps:
        if prev is None:
            for c, (_n, w) in cur.items():
                if c != "":
                    inside[c] = w >= 5
        else:
            evs = []
            for c in sorted(set(prev) | set(cur)):
                if c == "":
                    continue
                in_prev, in_cur = c in prev, c in cur
                w0 = prev[c][1] if in_prev else 0.0
                w1 = cur[c][1] if in_cur else 0.0
                if in_cur and not in_prev:
                    evs.append((c, cur[c][0], "ENTRY", 0.0, w1, w1 >= 5))
                    inside[c] = w1 >= 5
                elif in_prev and not in_cur:
                    evs.append((c, prev[c][0], "EXIT", w0, 0.0, inside.get(c, False)))
                    inside[c] = False
                else:
                    old = inside.get(c, False)
                    if not old and w1 >= 5:
                        new = True
                    elif old and w1 < 3:
                        new = False
                    else:
                        new = old
                    inside[c] = new
                    if new and not old:
                        evs.append((c, cur[c][0], "UP5", w0, w1, True))
                    elif old and not new:
                        evs.append((c, cur[c][0], "DN3", w0, w1, True))
            if evs:
                out[asof] = evs
        prev = cur
    return out


def rebuild_events(con: sqlite3.Connection, etf: str) -> dict[str, list[tuple]]:
    """etf 의 etf_holdings_daily 전체를 asof 순으로 읽어 walk_events 로 재계산,
    etf_holding_events 에서 해당 etf 행을 DELETE 후 전부 INSERT. 커밋은 호출자. 결과 dict 반환."""
    by_asof: dict[str, dict] = {}
    for asof, code, name, w in con.execute(
            "SELECT asof, code, name, weight_pct FROM etf_holdings_daily WHERE etf=? ORDER BY asof",
            (etf,)).fetchall():
        by_asof.setdefault(asof, {})[code] = (name, w)
    res = walk_events(sorted(by_asof.items()))
    con.execute("DELETE FROM etf_holding_events WHERE etf=?", (etf,))
    con.executemany("INSERT INTO etf_holding_events VALUES (?,?,?,?,?,?,?)",
                    [(d, etf, c, n, k, w0, w1) for d, evs in res.items() for c, n, k, w0, w1, _a in evs])
    return res


def latest_snapshot(idx: str) -> tuple[str, list[tuple]]:
    day = datetime.now().date()
    for _ in range(10):
        asof = day.strftime("%Y-%m-%d")
        echo, rows = fetch(idx, asof)
        if echo == asof and rows:
            return asof, rows
        day -= timedelta(days=1)
    raise RuntimeError("no trading day found in last 10 days")


def format_alert(date: str, by_etf: dict) -> str:
    lines = [f"[ETF보유] {date} 이벤트"]
    for etf, evs in sorted(by_etf.items()):
        lines.append(f"-- {etf} ({len(evs)})")
        for c, n, k, w0, w1, _a in sorted(evs, key=lambda e: -max(e[3], e[4]))[:25]:
            label = {"ENTRY": "신규", "EXIT": "편출", "UP5": "5%↑", "DN3": "3%↓"}.get(k, k)
            lines.append(f"  [{label}] {n[:24]} ({c}) {w0:.2f}->{w1:.2f}")
    return "\n".join(lines)


def run_daily() -> tuple[int, int]:
    con = open_db()
    stored = nev = 0
    alerts: dict[str, list] = {}
    asofs: list[str] = []
    sent = True
    try:
        for etf, idx in ETFS.items():
            asof, rows = latest_snapshot(idx)
            if con.execute("SELECT 1 FROM etf_holdings_daily WHERE etf=? AND asof=? LIMIT 1",
                           (etf, asof)).fetchone():
                print(f"{etf} {asof}: skip (stored)")
                continue
            stored += store(con, etf, asof, rows)
            res = rebuild_events(con, etf)
            con.commit()
            evs = res.get(asof, [])
            nev += len(evs)
            hot = [e for e in evs if e[5]]
            if hot:
                alerts[etf] = hot
                asofs.append(asof)
            print(f"{etf} {asof}: stored {len(rows)} rows")
            time.sleep(0.5)
    finally:
        con.close()
        if alerts:
            sent = notify_mod.send_etf_holdings_alert(format_alert(max(asofs), alerts))
    if not sent:
        raise RuntimeError("ETF holdings alert delivery failed")
    return stored, nev


def run_backfill(s: str, e: str) -> tuple[int, int]:
    con = open_db()
    stored = 0
    touched: set[str] = set()
    failed: list[str] = []
    d0, d1 = datetime.strptime(s, "%Y%m%d").date(), datetime.strptime(e, "%Y%m%d").date()
    d = d0
    while d <= d1:
        if d.weekday() < 5:
            asof = d.strftime("%Y-%m-%d")
            for etf, idx in ETFS.items():
                if con.execute("SELECT 1 FROM etf_holdings_daily WHERE etf=? AND asof=? LIMIT 1",
                               (etf, asof)).fetchone():
                    continue
                try:
                    echo, rows = fetch(idx, asof)
                except Exception as ex:
                    print(f"{etf} {asof}: fetch failed {ex}")
                    failed.append(f"{etf} {asof}")
                    continue
                if echo != asof or not rows:
                    continue
                n = store(con, etf, asof, rows)
                stored += n
                if n:
                    touched.add(etf)
                con.commit()
                time.sleep(0.4)
        d += timedelta(days=1)
    nev = 0
    for etf in sorted(touched):
        nev += sum(len(v) for v in rebuild_events(con, etf).values())
    con.commit()
    con.close()
    print(f"backfill stored={stored} events={nev}")
    if failed:
        raise RuntimeError(f"backfill fetch failed {len(failed)}: {', '.join(failed[:10])}")
    return stored, nev


def run_import_csv(path: str, etf: str) -> tuple[int, int]:
    import csv
    con = open_db()
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_asof: dict[str, list] = {}
    for r in rows:
        by_asof.setdefault(r["asof"], []).append(
            (r["code"], r["name"], r["qty"], r["amt_krw"], r["weight_pct"]))
    stored = 0
    for asof in sorted(by_asof):
        recs = [(c, n, float(q or 0), float(a or 0), float(w or 0)) for c, n, q, a, w in by_asof[asof]]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stored += con.executemany(
            "INSERT OR IGNORE INTO etf_holdings_daily VALUES (?,?,?,?,?,?,?,?)",
            [(asof, etf, c, n, q, a, w, now) for c, n, q, a, w in recs]).rowcount
    res = rebuild_events(con, etf)
    con.commit()
    con.close()
    nev = sum(len(v) for v in res.values())
    print(f"import {etf}: stored={stored} events={nev}")
    return stored, nev


def main(argv: list[str]) -> None:
    if argv[:1] == ["--daily"]:
        s, e = run_daily()
        print(f"daily stored={s} events={e}")
    elif argv[:1] == ["--backfill"] and len(argv) >= 3:
        s, e = run_backfill(argv[1], argv[2])
        print(f"backfill stored={s} events={e}")
    elif argv[:1] == ["--import-csv"] and len(argv) >= 3:
        s, e = run_import_csv(argv[1], argv[2])
        print(f"import stored={s} events={e}")
    else:
        raise SystemExit("usage: --daily | --backfill YYYYMMDD YYYYMMDD | --import-csv PATH ETF")


if __name__ == "__main__":
    main(sys.argv[1:])
