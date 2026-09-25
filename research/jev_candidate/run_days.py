"""P2 표본 러너 (open 트랙만) — Top30 → 글 → 1차 필터 → 10문항 → 저장.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m research.jev_candidate.run_days --dates 20260409,20260410
    etl\\.venv\\Scripts\\python.exe -m research.jev_candidate.run_days --dates 20260409 --report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import duckdb
from dotenv import load_dotenv
from typesafe_sdk import TypeSafeClient

from research.jev_candidate import questions, state, store
from research.jev_candidate.questions import QUESTION_SET_VER
from research.jev_candidate.state import JEV_MODEL

KRX_DB = ROOT / "etl" / "db" / "krx_ohlcv.duckdb"
TG_DB = ROOT / "etl" / "db" / "telegram_public.sqlite3"

# 종목B 이후 라벨 — 당일 Top30 나머지 29개용 (Top30 순서대로)
_EXTRA_LABELS = ["종목" + chr(c) for c in range(ord("B"), ord("Z") + 1)] + [
    "종목AA",
    "종목AB",
    "종목AC",
    "종목AD",
]


def _dashed(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD."""
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def _has_answers(con: sqlite3.Connection, run_id: str, ticker: str) -> bool:
    """답 존재 여부 — ask 실패 후 state만 남은 건 재시도한다."""
    row = con.execute(
        "SELECT 1 FROM jev_answers WHERE run_id = ? AND ticker = ? LIMIT 1",
        (run_id, ticker),
    ).fetchone()
    return row is not None


def _top30(duck_con: object, date: str) -> list[dict]:
    """당일 거래대금 Top60 → 이름 매핑 → 스팩 제외 → 30개.

    정리매매 플래그는 DB 에 없어 제외 못 함 — P4 에서 재검토.
    """
    code_to_name = {
        code: name
        for code, name in duck_con.execute("SELECT code, name FROM stock_names").fetchall()
    }
    rows = duck_con.execute(
        "SELECT ticker, close, trading_value FROM ohlcv"
        " WHERE date = ? AND volume > 0 AND open > 0 AND close > 0"
        " ORDER BY trading_value DESC LIMIT 60",
        [date],
    ).fetchall()
    out = []
    for ticker, close, _tv in rows:
        name = code_to_name.get(ticker)
        if not name or "스팩" in name or "기업인수목적" in name:
            continue
        out.append({"ticker": ticker, "name": name, "close": close})
        if len(out) == 30:
            break
    return out


def _prev_closes(duck_con: object, tickers: list[str], prev_date: str) -> dict[str, float]:
    """전 거래일 종가 매핑 — 없으면 빠짐."""
    if not tickers:
        return {}
    holders = ", ".join("?" for _ in tickers)
    rows = duck_con.execute(
        f"SELECT ticker, close FROM ohlcv WHERE date = ? AND ticker IN ({holders})",
        [prev_date, *tickers],
    ).fetchall()
    return {t: c for t, c in rows if c}


def _i_state(
    dst_row: dict, src_row: dict, pct_dst: float,
    src_passed_by_section: dict[str, list[dict]], top30: list[dict],
) -> tuple[str, dict, dict]:
    """I state 조립 (순수) — 가격은 dst 것, 글·매핑은 src 것 (src 이름 → 종목A).

    dst_row 는 저장 단계에서 이름·티커를 쓰므로 여기선 서명용으로만 받는다.
    """
    mapping = _anon_mapping(top30, src_row)
    ordered = [
        (lb, src_passed_by_section.get(lb, []))
        for lb in state.SECTION_ORDER
        if lb in src_passed_by_section
    ]
    ordered += [
        (lb, posts)
        for lb, posts in src_passed_by_section.items()
        if lb not in state.SECTION_ORDER
    ]
    text, stats = state.build_state(pct_dst, ordered, mapping)
    return text, stats, {"src": src_row["ticker"], **mapping}


def _anon_mapping(top30: list[dict], target: dict) -> dict[str, str]:
    """당일 공통 매핑 — 대상 → 종목A, 나머지 Top30 순서대로 종목B...."""
    mapping = {target["name"]: "종목A", target["ticker"]: "종목A"}
    labels = iter(_EXTRA_LABELS)
    for row in top30:
        if row["ticker"] == target["ticker"]:
            continue
        label = next(labels)
        mapping.setdefault(row["name"], label)
        mapping.setdefault(row["ticker"], label)
    return mapping


class _FilterCache:
    """1차 필터 공용 캐시 — 스레드마다 별도 sqlite 연결.

    sqlite3 연결은 생성 스레드에서만 쓸 수 있어 ThreadPoolExecutor 와
    공유 불가 — 스레드-로컬 연결을 쓴다. 파일 잠금은 sqlite 가 직렬화.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._local = threading.local()
        self._mu = threading.Lock()
        self.lookups = 0
        self.hits = 0

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = store.connect(self._db_path)
            self._local.con = con
        return con

    def get(self, key: str) -> float | None:
        val = store.get_filter(self._con(), key)
        with self._mu:
            self.lookups += 1
            if val is not None:
                self.hits += 1
        return val

    def put(self, key: str, noul: float) -> None:
        store.put_filter(self._con(), key, noul)


class _AnswerCache:
    """2차 질문 공용 캐시 — 순차 구간이라 주 연결 그대로 쓴다."""

    def __init__(self, con: sqlite3.Connection, model: str):
        self._con = con
        self._model = model
        self.lookups = 0
        self.hits = 0

    def get(self, key: str) -> list[dict] | None:
        rows = store.get_answers(self._con, key)
        self.lookups += 1
        if rows is not None:
            self.hits += 1
        return rows

    def put(self, key: str, rows: list[dict], usage: dict) -> None:
        store.put_answers(self._con, key, self._model, rows, usage)


def _run_day(
    duck_con: object,
    tg_con: sqlite3.Connection,
    con: sqlite3.Connection,
    client: object,
    day: str,
    trading: list[str],
    db_path: str,
) -> dict:
    """하루치 — prev2·다음 거래일 기준 open 트랙."""
    i = trading.index(day)
    prev2, prev1, nxt = trading[i - 2], trading[i - 1], trading[i + 1]
    top30 = _top30(duck_con, day)
    closes = _prev_closes(duck_con, [r["ticker"] for r in top30], prev1)
    skipped = sum(1 for r in top30 if r["ticker"] not in closes)
    targets = [r for r in top30 if r["ticker"] in closes]

    run_id = f"backtest-open-{day}-{QUESTION_SET_VER}"
    store.save_run(
        con,
        run_id=run_id,
        mode="backtest",
        track="open",
        date=day,
        as_of=f"{_dashed(nxt)} 08:00",
        jev_model=JEV_MODEL,
        gpt_model=None,
        question_set_ver=QUESTION_SET_VER,
        prompt_ver=None,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    run_id_i = run_id + "-I"
    store.save_run(
        con,
        run_id=run_id_i,
        mode="backtest",
        track="open",
        date=day,
        as_of=f"{_dashed(nxt)} 08:00",
        jev_model=JEV_MODEL,
        gpt_model=None,
        question_set_ver=QUESTION_SET_VER,
        prompt_ver=f"shuffle-seed={day}",
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    sections = state.section_bounds(_dashed(day), "open", _dashed(prev2), _dashed(nxt))
    full_start, full_end = sections[0][1], sections[-1][2]
    labels = [lb for lb, _, _ in sections]

    # 후보 글 적재 — state 유무와 무관하게 전 종목 (I 섞기용, 1차 필터 캐시라 재실행 비용 0)
    per_ticker: dict[str, dict] = {}
    for t in targets:
        mapping = _anon_mapping(top30, t)
        posts = state.load_candidate_posts(tg_con, [t["name"]], full_start, full_end)
        by_section: dict[str, list[dict]] = {lb: [] for lb, _, _ in sections}
        for p in posts:
            for lb, s, e in sections:
                if s <= p["posted_at_kst"] < e:
                    by_section[lb].append({**p, "section": lb})
                    break
        per_ticker[t["ticker"]] = {"row": t, "mapping": mapping, "by_section": by_section}

    # 1차 필터 — 8 병렬 (종목 단위), 캐시는 run_id 공용
    fcache = _FilterCache(db_path)

    def _filter(ticker: str) -> list[dict]:
        info = per_ticker[ticker]
        all_posts = [p for lb, _, _ in sections for p in info["by_section"][lb]]
        return state.filter_posts(client, all_posts, info["mapping"], cache=fcache)

    work = list(per_ticker)
    filtered: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(8) as ex:
        for ticker, rows in zip(work, ex.map(_filter, work)):
            filtered[ticker] = rows

    # state → 질문 → 저장 (순차) — state+답 둘 다 있어야 건너뜀
    n_cand = n_pass = n_err = 0
    billed = 0
    acache = _AnswerCache(con, JEV_MODEL)
    pct_by: dict[str, float] = {}
    passed_by: dict[str, list[dict]] = {}
    counts_by: dict[str, tuple] = {}
    for ticker in work:
        info = per_ticker[ticker]
        t, mapping = info["row"], info["mapping"]
        rows = filtered[ticker]
        n_cand += len(rows)
        passed = [r for r in rows if r.get("passed")]
        n_pass += len(passed)
        pct = max(-30.0, min(30.0, (t["close"] / closes[ticker] - 1) * 100))
        pct_by[ticker] = pct
        passed_by[ticker] = passed
        counts_by[ticker] = (len(rows), sum(1 for r in rows if r.get("ambiguous")))
        if store.has_state(con, run_id, ticker) and _has_answers(con, run_id, ticker):
            continue
        by_label = {lb: [] for lb, _, _ in sections}
        for r in passed:
            by_label[r["section"]].append(r)
        state_text, stats = state.build_state(
            pct, [(lb, by_label[lb]) for lb, _, _ in sections], mapping
        )
        store.save_posts(
            con,
            [
                {
                    **r,
                    "run_id": run_id,
                    "date": day,
                    "ticker": ticker,
                    "posted_at_kst": r["posted_at_kst"].isoformat()
                    if hasattr(r["posted_at_kst"], "isoformat")
                    else r["posted_at_kst"],
                }
                for r in rows
            ],
        )
        store.save_state(
            con,
            {
                "run_id": run_id,
                "date": day,
                "ticker": ticker,
                "name": t["name"],
                "price_pct": pct,
                "state_text": state_text,
                "state_hash": hashlib.sha1(state_text.encode("utf-8")).hexdigest(),
                "anon_map_json": json.dumps(mapping, ensure_ascii=False),
                "n_candidates": len(rows),
                "n_passed": len(passed),
                "n_ambiguous": sum(1 for r in rows if r.get("ambiguous")),
                "n_included": stats["n_included"],
                "truncated": int(bool(stats["truncated"])),
            },
        )
        try:
            ans_rows, usage = questions.ask(client, state_text, JEV_MODEL, cache=acache)
            billed += (usage.get("input_tokens") or 0)
        except Exception:
            n_err += 1
            continue
        store.save_answers(con, run_id, day, ticker, ans_rows)

    # I 글 섞기 — 같은 가격 구간 안에서 글 섹션만 맞바꾼 state → 같은 10문항
    row_by = {t["ticker"]: t for t in targets}
    bucket_by = {ticker: state.price_sentence(p) for ticker, p in pct_by.items()}
    groups = {ticker: passed_by[ticker] for ticker in work}
    assign, unswapped = state.shuffle_posts(groups, bucket_by, seed=int(day))
    n_i = n_i_err = 0
    for dst in work:
        if store.has_state(con, run_id_i, dst) and _has_answers(con, run_id_i, dst):
            continue
        src = assign[dst]
        src_by_section = {lb: [] for lb in labels}
        for r in passed_by[src]:
            src_by_section.setdefault(r["section"], []).append(r)
        text, stats, mapping_i = _i_state(
            row_by[dst], row_by[src], pct_by[dst], src_by_section, top30
        )
        store.save_state(
            con,
            {
                "run_id": run_id_i,
                "date": day,
                "ticker": dst,
                "name": row_by[dst]["name"],
                "price_pct": pct_by[dst],
                "state_text": text,
                "state_hash": hashlib.sha1(text.encode("utf-8")).hexdigest(),
                "anon_map_json": json.dumps(mapping_i, ensure_ascii=False),
                "n_candidates": counts_by[src][0],
                "n_passed": len(passed_by[src]),
                "n_ambiguous": counts_by[src][1],
                "n_included": stats["n_included"],
                "truncated": int(bool(stats["truncated"])),
            },
        )
        try:
            ans_rows, usage = questions.ask(client, text, JEV_MODEL, cache=acache)
            billed += (usage.get("input_tokens") or 0)
        except Exception:
            n_i_err += 1
            continue
        store.save_answers(con, run_id_i, day, dst, ans_rows)
        n_i += 1
    print(
        f"{day} tickers={len(targets)} skipped_no_prev={skipped}"
        f" candidates={n_cand} passed={n_pass} jev_errors={n_err}"
        f" filter_jev_calls={fcache.lookups - fcache.hits} filter_cache_hits={fcache.hits}"
        f" answer_jev_calls={acache.lookups - acache.hits} answer_cache_hits={acache.hits}"
        f" billed_input_tokens={billed}"
        f" shuffle_unswapped={len(unswapped)} i_states={n_i} i_errors={n_i_err}"
    )
    return {"day": day, "tickers": len(targets), "candidates": n_cand, "passed": n_pass}


def _report(con: sqlite3.Connection, days: list[str]) -> None:
    """문항별 분포 + n_included==0 비중."""
    holders = ", ".join("?" for _ in days)
    ans = con.execute(
        f"SELECT qid, type, value FROM jev_answers"
        f" WHERE date IN ({holders}) AND run_id NOT LIKE '%-I'",
        days,
    ).fetchall()
    by_qid: dict[str, list[str]] = {}
    qtype: dict[str, str] = {}
    for qid, t, v in ans:
        by_qid.setdefault(qid, []).append(v)
        qtype[qid] = t
    for qid in sorted(by_qid):
        vals = by_qid[qid]
        t = qtype[qid]
        if t == "choice":
            counts: dict[str, int] = {}
            for v in vals:
                counts[v] = counts.get(v, 0) + 1
            shares = [c / len(vals) for c in counts.values()]
            peak = max(shares) if shares else 0.0
            flag = " 몰림" if peak >= 0.85 else ""
            print(f"{qid} choice n={len(vals)} {counts} peak={peak:.2f}{flag}")
        elif t == "score":
            xs = [float(v) for v in vals]
            mean = sum(xs) / len(xs)
            b0 = sum(1 for x in xs if 0 <= x < 0.5) / len(xs)
            b1 = sum(1 for x in xs if 0.5 <= x < 1.5) / len(xs)
            b2 = sum(1 for x in xs if x >= 1.5) / len(xs)
            peak = max(b0, b1, b2)
            flag = " 몰림" if peak >= 0.85 else ""
            print(
                f"{qid} score n={len(xs)} mean={mean:.2f}"
                f" [0,0.5)={b0:.2f} [0.5,1.5)={b1:.2f} [1.5,]={b2:.2f}"
                f" peak={peak:.2f}{flag}"
            )
        else:
            xs = [float(v) for v in vals]
            mean = sum(xs) / len(xs)
            ge = sum(1 for x in xs if x >= 0.5) / len(xs)
            amb = sum(1 for x in xs if 0.35 <= x <= 0.65) / len(xs)
            lo = sum(1 for x in xs if x < 0.35) / len(xs)
            hi = sum(1 for x in xs if x > 0.65) / len(xs)
            peak = max(lo, amb, hi)
            flag = " 몰림" if peak >= 0.85 else ""
            print(
                f"{qid} noul n={len(xs)} mean={mean:.2f}"
                f" >=0.5={ge:.2f} 0.35~0.65={amb:.2f} peak={peak:.2f}{flag}"
            )
    st = con.execute(
        f"SELECT COUNT(*), SUM(CASE WHEN n_included = 0 THEN 1 ELSE 0 END)"
        f" FROM states WHERE date IN ({holders}) AND run_id NOT LIKE '%-I'",
        days,
    ).fetchone()
    total, zeros = st[0] or 0, st[1] or 0
    print(f"states n={total} n_included==0 share={zeros / total:.2f}" if total else "states n=0")


def main(argv: list[str] | None = None) -> int:
    """CLI 진입 — close 트랙은 P4 로 돌린다."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True, help="YYYYMMDD 콤마 구분")
    ap.add_argument("--track", default="open")
    ap.add_argument("--db", default=str(store.DEFAULT_DB))
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)
    days = [d.strip() for d in args.dates.split(",") if d.strip()]

    con = store.connect(args.db)
    store.ensure_schema(con)
    if args.report:
        _report(con, days)
        return 0
    if args.track == "close":
        print("close 트랙은 P4 (15:10 분봉 Top30·가격)")
        return 2

    load_dotenv(ROOT / ".env")
    duck_con = duckdb.connect(str(KRX_DB), read_only=True)
    trading = [r[0] for r in duck_con.execute("SELECT DISTINCT date FROM ohlcv ORDER BY date").fetchall()]
    tg_con = sqlite3.connect(f"file:{TG_DB}?mode=ro", uri=True)
    client = TypeSafeClient()
    try:
        for day in days:
            if day not in trading or trading.index(day) < 2 or trading.index(day) + 1 >= len(trading):
                print(f"{day} 오류: prev2·다음 거래일 없음")
                continue
            _run_day(duck_con, tg_con, con, client, day, trading, args.db)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
