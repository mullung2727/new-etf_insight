"""스윙 후보 배치 LangGraph: 수집 → 컷 → Jev → 코드 판정 → 순위 → GPT 요약 → 저장 → 전송.

설계: docs/PLAN_SWING_PICK.md §2-1(노드) 전체. 실행 순서는 직선 8노드다.
판정 로직은 기존 모듈에 있고 (judge_code·judge_jev·rank·store) 여기서 새로
만드는 건 조립·State 흐름·메시지뿐이다.

왜가 모인 배경:
- 날짜: today = 실행일 YYYY-MM-DD (KST). prev_date = KRX DB의 max(date) <
  today — 19:00엔 KRX DB에 당일 치가 없으니 (다음 날 08:00 적재) D-1 시세가
  기준이다. prev_date != expected_prev(달력상 직전 거래일)이면 아침 KRX 배치가
  실패했다는 신호라 krx_prev_stale 경고를 남기고 계속 돈다.
- 탈락 종목의 Jev·키움 조회를 생략한다: 1차 컷 종목은 Jev를 안 돌리고,
  리스크 탈락 종목은 judge_code(키움)를 안 돌린다. 버려질 종목에 돈·호출을
  쓰지 않기 위해서다. 단 행 자체는 저장한다 (나중에 항목별 예측력 검증용).
- Jev 실패 종목은 순위에서 뺀다 (fail closed). 희석 리스크를 모르는 종목을
  추천할 수 없어서다. 반대로 broker 상태 조회 실패는 통과시킨다 (fail open) —
  상태 모름이 곧 위험은 아니고 2번 Jev가 이미 관리종목 지정 등을 봤기 때문이다.
- GPT는 최종 선정 종목 요약 1회뿐이다. GPT 한도가 부족해서 후보 선별에는
  Jev를 쓰고, GPT는 마지막 요약에만 쓴다 (PLAN §6).
- 소스 하나가 죽어도 배치는 계속 돈다. 텔레그램 DB 잠금 같은 부분 장애가
  전일 판정을 날릴 이유가 없어서다. 대신 source_failed 경고를 남긴다
  (조용한 누락 금지).

주입: build_graph(deps). Deps에 DB 경로·broker·클라이언트·fetch·http·LLM·알림을
전부 넣는다. 테스트는 전부 가짜로 대체한다 (네트워크 금지).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, TypedDict

import duckdb
import requests
from langgraph.graph import END, StateGraph

from scripts.check_krx_trading_day import is_krx_trading_day
from scripts.notify import notify as _real_notify
from scripts.report_metrics.storage import connect_ro as report_connect_ro
from scripts.report_metrics.storage import report_candidates
from scripts.swing_pick.judge_code import judge_code, load_ohlcv, prefilter
from scripts.swing_pick.judge_jev import judge_jev
from scripts.swing_pick.rank import pick_top, rank_candidates, total_score
from scripts.swing_pick.store import DEFAULT_DB as DEFAULT_STORE_DB
from scripts.swing_pick.store import mark_notified, save_day
from scripts.trading_batch_common import REQUEST_TIMEOUT

_HERE = Path(__file__).resolve().parent
SUMMARY_PROMPT_PATH = _HERE / "prompts" / "summary.md"
SUMMARY_SCHEMA_PATH = _HERE / "summary_schema.json"

# GPT 입력에 넣는 Jev 입력 텍스트 앞부분 길이. 요약 근거용 발췌라 전문이
# 필요 없고, 길면 GPT 한도를 갉아먹는다.
JEV_EXCERPT_CHARS = 2000

# 선정 목표·상태 조회 상한. 상위부터 확인하며 3개를 채우고 최대 10번만 본다.
TOP_K = 3
MAX_STATUS_CHECKS = 10

_SOURCE_LABELS = {
    "telegram": "텔레그램",
    "youtube": "유튜브",
    "report": "리포트",
    "high52": "신고가",
}


class State(TypedDict, total=False):
    today: str            # YYYY-MM-DD
    prev_date: str        # YYYYMMDD (KRX DB max date < today)
    warnings: list        # str 목록
    candidates: list      # [{ticker, name, sources}]
    rows: dict            # ticker -> 판정 행 (DB 컬럼 + input_text)
    picks: list           # 선정 ticker 순서 목록
    summary: dict | None  # GPT 요약 {overview, picks}
    message: str          # 전송 문구
    saved: bool
    notified: bool
    jev_tokens: int


def _default_fetch_filings(date_kst: str) -> list[dict]:
    """운영 기본 공시 fetch. lazy import — 이 모듈 import 때 무거운 수집
    모듈을 끌어오지 않기 위해서다."""
    from scripts.collect_trading_result_evidence import fetch_filings

    return fetch_filings(date_kst)


def _default_fetch_news(name: str, ticker: str, as_of, limit: int) -> list[dict]:
    from scripts.collect_trading_result_evidence import fetch_news

    return fetch_news(name, ticker, as_of, limit)


@dataclass
class Deps:
    tg_db: str | Path
    yt_db: str | Path
    report_db: str | Path
    ohlcv_db: str | Path
    fin_db: str | Path
    high52_db: str | Path
    broker_url: str = "http://localhost:8001"
    jev_client: Any = None
    fetch_filings: Callable | None = None
    fetch_news: Callable | None = None
    http_get: Callable = requests.get
    generate_fn: Callable | None = None  # None이면 실제 GPT. skip과는 별개
    notify_fn: Callable | None = None    # None이면 실제 notify
    store_path: str | Path = DEFAULT_STORE_DB
    dry_run: bool = False
    skip_summary: bool = False  # --skip-summary. GPT 호출 자체를 안 함


def _compact(day_dash: str) -> str:
    return day_dash.replace("-", "")


def _dash(day_compact: str) -> str:
    return f"{day_compact[:4]}-{day_compact[4:6]}-{day_compact[6:]}"


def expected_prev_trading_day(today: str) -> str:
    """today 이전으로 하루씩 거슬러 첫 거래일 (YYYYMMDD).

    31일 bound — 달력 함수가 꼬여도 무한루프는 안 돈다.
    """
    day = date.fromisoformat(today)
    for _ in range(31):
        day -= timedelta(days=1)
        if is_krx_trading_day(day.strftime("%Y%m%d")):
            return day.strftime("%Y%m%d")
    raise RuntimeError(f"no trading day in 31 days before {today}")


def load_prev_date(ohlcv_db: str | Path, today: str) -> str:
    """KRX DB ohlcv의 max(date) < today. 비어 있으면 그대로 터뜨린다.

    조용히 빈 기준일로 계속 돌리면 전 후보가 no_prev_data 컷이 돼
    '후보 없음'으로 둔갑한다. KRX 적재 실패는 loud failure가 맞다.
    """
    con = duckdb.connect(str(ohlcv_db), read_only=True)
    try:
        row = con.execute(
            "SELECT MAX(date) FROM ohlcv WHERE date < ?",
            [_compact(today)],
        ).fetchone()
    finally:
        con.close()
    prev = row[0] if row else None
    if prev is None:
        raise RuntimeError(f"ohlcv_empty_before:{_compact(today)}")
    return str(prev)


def _sqlite_ro(db_path: str | Path) -> sqlite3.Connection:
    # mode=ro — 수집 단계에서 DB 파일이 없을 때 빈 파일을 만들면 안 된다.
    # 없으면 예외 → source_failed 경고로 기록하고 계속 돈다.
    con = sqlite3.connect(
        f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True
    )
    con.row_factory = sqlite3.Row
    return con


def _is_ticker(value: object) -> bool:
    t = str(value or "").strip()
    return len(t) == 6 and t.isdigit()


def _collect_telegram(tg_db: str | Path, today: str) -> list[dict]:
    with closing(_sqlite_ro(tg_db)) as con:
        rows = con.execute(
            "SELECT ticker, name FROM telegram_stock_insights WHERE date_kst = ?",
            (today,),
        ).fetchall()
    return [{"ticker": r["ticker"], "name": r["name"]} for r in rows]


def _collect_youtube(yt_db: str | Path, today: str) -> list[dict]:
    with closing(_sqlite_ro(yt_db)) as con:
        rows = con.execute(
            "SELECT ticker, name FROM youtube_stock_insights WHERE date_kst = ?",
            (today,),
        ).fetchall()
    return [{"ticker": r["ticker"], "name": r["name"]} for r in rows]


def _collect_report(report_db: str | Path, start_exclusive: str, today: str) -> list[dict]:
    with report_connect_ro(report_db) as con:
        cands = report_candidates(con, start_exclusive, today)
    return [{"ticker": c["stock_code"], "name": c["stock_name"]} for c in cands]


def _collect_high52(high52_db: str | Path, today_compact: str) -> list[dict]:
    with closing(_sqlite_ro(high52_db)) as con:
        rows = con.execute(
            "SELECT ticker, name FROM high52_screen WHERE date = ? AND cand = 1",
            (today_compact,),
        ).fetchall()
    return [{"ticker": r["ticker"], "name": r["name"]} for r in rows]


def _merge_candidates(by_source: list[tuple[str, list[dict]]]) -> list[dict]:
    """ticker로 병합. sources 정렬, name은 먼저 나온 비어있지 않은 값.
    6자리 숫자가 아닌 ticker는 버린다 (미국 티커 등insights 오기입 방어).
    ticker 오름차순으로 정렬해 순서를 고정한다.
    """
    merged: dict[str, dict] = {}
    for source, items in by_source:
        for item in items:
            ticker = str(item.get("ticker") or "").strip()
            if not _is_ticker(ticker):
                continue
            slot = merged.setdefault(ticker, {"ticker": ticker, "name": "", "sources": []})
            name = str(item.get("name") or "").strip()
            if name and not slot["name"]:
                slot["name"] = name
            if source not in slot["sources"]:
                slot["sources"].append(source)
    for slot in merged.values():
        slot["sources"].sort()
    return [merged[t] for t in sorted(merged)]


def node_collect(state: State, deps: Deps) -> dict:
    today = state["today"]
    warnings = list(state.get("warnings") or [])
    prev_date = load_prev_date(deps.ohlcv_db, today)
    expected = expected_prev_trading_day(today)
    if prev_date != expected:
        warnings.append(f"krx_prev_stale: {prev_date} (expected {expected})")

    prev_dash = _dash(prev_date)
    compact = _compact(today)
    # 소스 하나가 죽어도 계속 — 부분 장애가 전일 판정을 날리면 안 된다.
    jobs = [
        ("telegram", lambda: _collect_telegram(deps.tg_db, today)),
        ("youtube", lambda: _collect_youtube(deps.yt_db, today)),
        ("report", lambda: _collect_report(deps.report_db, prev_dash, today)),
        ("high52", lambda: _collect_high52(deps.high52_db, compact)),
    ]
    by_source = []
    for name, job in jobs:
        try:
            by_source.append((name, job()))
        except Exception as exc:
            warnings.append(f"source_failed:{name}:{type(exc).__name__}")
            by_source.append((name, []))
    return {
        "prev_date": prev_date,
        "warnings": warnings,
        "candidates": _merge_candidates(by_source),
    }


def _new_row(cand: dict, trading_value) -> dict:
    # DB 컬럼 + input_text(GPT 발췌용, 저장 안 함). 전 키를 미리 깔아
    # 뒤 노드·save_day·format_message가 .get 방어를 안 해도 되게 한다.
    return {
        "ticker": cand["ticker"], "name": cand["name"], "sources": cand["sources"],
        "trading_value": trading_value,
        "jev_sustain": None, "jev_risk": None,
        "op_profit": None, "op_profit_yoy": None,
        "frgn_net_5d": None, "orgn_net_5d": None,
        "ma20_gap": None, "ret_5d": None, "per": None, "per_note": None,
        "themes": None,
        "s1": None, "s3": None, "s4": None, "s5": None,
        "risk_out": None, "total": None, "rank": None,
        "errors": {}, "jev_input_hash": None, "excluded_reason": None,
        "input_text": None,
    }


def node_prefilter(state: State, deps: Deps) -> dict:
    candidates = state.get("candidates") or []
    tickers = [c["ticker"] for c in candidates]
    prev_rows, _, _ = load_ohlcv(deps.ohlcv_db, tickers, state["prev_date"])
    _passed, cut = prefilter(candidates, prev_rows)
    cut_reason = {c["ticker"]: c["cut_reason"] for c in cut}
    rows = {}
    for cand in candidates:
        ticker = cand["ticker"]
        row = _new_row(cand, prev_rows.get(ticker, {}).get("trading_value"))
        if ticker in cut_reason:
            row["excluded_reason"] = f"cut:{cut_reason[ticker]}"
        rows[ticker] = row
    return {"rows": rows}


def node_judge_jev(state: State, deps: Deps) -> dict:
    rows = state["rows"]
    targets = [t for t, r in rows.items() if r.get("excluded_reason") is None]
    if not targets:
        return {"rows": rows, "jev_tokens": 0}
    if deps.jev_client is None:
        # 설정 오류는 loud — 조용히 전원 jev_error로 두면 원인 파악이 늦는다.
        raise RuntimeError("jev_client is required")
    fetch_filings = deps.fetch_filings or _default_fetch_filings
    fetch_news = deps.fetch_news or _default_fetch_news
    prev_dash = _dash(state["prev_date"])
    out = judge_jev(
        [{"ticker": t, "name": rows[t]["name"]} for t in targets],
        today=state["today"],
        prev_trading_date=prev_dash,
        report_start_exclusive=prev_dash,
        client=deps.jev_client,
        tg_db=deps.tg_db,
        yt_db=deps.yt_db,
        report_db=deps.report_db,
        fetch_filings=fetch_filings,
        fetch_news=fetch_news,
    )
    warnings = list(state.get("warnings") or [])
    if out["failed_filing_dates"]:
        warnings.append(f"filings_failed:{','.join(out['failed_filing_dates'])}")
    for ticker, res in out["results"].items():
        row = rows[ticker]
        row["s1"] = res["s1"]
        row["risk_out"] = res["risk_out"]
        row["jev_sustain"] = res["jev_sustain"]
        row["jev_risk"] = res["jev_risk"]
        row["jev_input_hash"] = res["jev_input_hash"]
        row["input_text"] = res["input_text"]
        row["errors"].update(res["errors"])
        if res["risk_out"] is True:
            row["excluded_reason"] = "risk"
        elif res["risk_out"] is None:
            # Jev 실패 = 희석 리스크를 모름 → 추천에서 뺀다 (fail closed).
            row["excluded_reason"] = "jev_error"
    return {"rows": rows, "warnings": warnings, "jev_tokens": out["input_tokens_total"]}


_CODE_KEYS = (
    "s3", "s4", "s5", "op_profit", "op_profit_yoy",
    "frgn_net_5d", "orgn_net_5d", "ma20_gap", "ret_5d", "per", "per_note",
)


def node_judge_code(state: State, deps: Deps) -> dict:
    rows = state["rows"]
    # 탈락 종목은 키움 조회 생략 — 버려질 종목에 호출을 쓰지 않는다.
    targets = [t for t, r in rows.items() if r.get("excluded_reason") is None]
    if not targets:
        return {"rows": rows}
    results = judge_code(
        targets,
        today=_compact(state["today"]),
        prev_date=state["prev_date"],
        broker_url=deps.broker_url,
        ohlcv_db=deps.ohlcv_db,
        fin_db=deps.fin_db,
        get=deps.http_get,
    )
    for ticker, res in results.items():
        row = rows[ticker]
        for key in _CODE_KEYS:
            row[key] = res[key]
        # market_cap_prev·quarter·today_price·ma20는 저장 스키마에 없어 버린다.
        row["errors"].update(res["errors"])
    return {"rows": rows}


def _fetch_status(http_get: Callable, broker_url: str, ticker: str) -> dict | None:
    resp = http_get(
        f"{broker_url}/quotes/{ticker}/status", timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else None


def _fetch_themes(http_get: Callable, broker_url: str, ticker: str) -> list[dict]:
    """선정 종목만 조회. 기간수익률 상위 3개를 저장 형태로."""
    resp = http_get(
        f"{broker_url}/quotes/{ticker}/themes",
        params={"days": 5},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("themes_not_list")
    ordered = sorted(
        data,
        key=lambda d: (
            (d.get("period_return") is None),
            -(d.get("period_return") or 0),
        ),
    )
    return [
        {"name": d.get("name"), "ret": d.get("period_return"),
         "up": d.get("rising"), "down": d.get("falling")}
        for d in ordered[:3]
    ]


def node_rank(state: State, deps: Deps) -> dict:
    rows = state["rows"]
    for row in rows.values():
        # 탈락 포함 전 행 계산 — 나중에 탈락 종목 점수 분포도 검증하려고.
        row["total"] = total_score(row)
    ordered = rank_candidates(list(rows.values()))
    picked, reasons = pick_top(
        ordered,
        lambda t: _fetch_status(deps.http_get, deps.broker_url, t),
        k=TOP_K,
        max_checks=MAX_STATUS_CHECKS,
    )
    picked_tickers = [r["ticker"] for r in picked]
    for ticker, reason in reasons.items():
        # status_unknown은 통과라 제외 사유를 안 붙인다 — pick_top이 돌려준
        # 선정 목록에 없는 ticker만 상태 탈락이다.
        if ticker not in picked_tickers:
            rows[ticker]["excluded_reason"] = f"status:{reason}"
        else:  # 선정됐지만 상태 확인 실패 — 추천 근거가 약하다는 흔적은 남긴다.
            rows[ticker]["errors"]["status"] = reason
    for i, ticker in enumerate(picked_tickers, 1):
        rows[ticker]["rank"] = i
        try:
            rows[ticker]["themes"] = _fetch_themes(deps.http_get, deps.broker_url, ticker)
        except Exception as exc:
            # 테마는 참고값이라 실패해도 선정은 유지, 빈 리스트 + 오류 기록.
            rows[ticker]["themes"] = []
            rows[ticker]["errors"]["themes"] = type(exc).__name__
    return {"rows": rows, "picks": picked_tickers}


def _summary_payload(rows: dict, picks: list[str]) -> list[dict]:
    payload = []
    for ticker in picks:
        row = rows[ticker]
        payload.append({
            "ticker": ticker,
            "name": row["name"],
            "sources": row["sources"],
            "scores": {
                "s1": row["s1"], "s3": row["s3"], "s4": row["s4"],
                "s5": row["s5"], "total": row["total"],
            },
            "raw": {
                "jev_sustain": row["jev_sustain"], "jev_risk": row["jev_risk"],
                "op_profit": row["op_profit"], "op_profit_yoy": row["op_profit_yoy"],
                "frgn_net_5d": row["frgn_net_5d"], "orgn_net_5d": row["orgn_net_5d"],
                "ma20_gap": row["ma20_gap"], "ret_5d": row["ret_5d"],
            },
            "per": row["per"],
            "per_note": row["per_note"],
            "themes": row["themes"],
            "jev_excerpt": (row.get("input_text") or "")[:JEV_EXCERPT_CHARS],
        })
    return payload


def node_summarize(state: State, deps: Deps) -> dict:
    picks = state.get("picks") or []
    if not picks or deps.skip_summary:
        # 선정 0개면 GPT 호출 안 함 — 요약할 게 없는데 호출할 이유가 없다.
        return {"summary": None}
    gen = deps.generate_fn
    if gen is None:
        from new_etf_insight.llm import generate_json as gen
    prompt = SUMMARY_PROMPT_PATH.read_text(encoding="utf-8").replace(
        "{payload}",
        json.dumps(
            _summary_payload(state["rows"], picks), ensure_ascii=False
        ),
    )
    try:
        summary = json.loads(
            gen(prompt, output_schema_path=SUMMARY_SCHEMA_PATH, search=False)
        )
        if not isinstance(summary, dict) or "overview" not in summary \
                or "picks" not in summary:
            raise ValueError("summary_shape")
    except Exception as exc:
        # GPT 실패는 배치 실패가 아니다 — 요약 없이도 판정·저장·전송은 간다.
        warnings = list(state.get("warnings") or [])
        warnings.append(f"summary_failed:{type(exc).__name__}")
        return {"summary": None, "warnings": warnings}
    return {"summary": summary}


def node_store(state: State, deps: Deps) -> dict:
    if deps.dry_run:
        return {"saved": False}
    rows = state["rows"]
    warnings = state.get("warnings") or []
    save_day(
        deps.store_path,
        state["today"],
        list(rows.values()),
        {
            "n_candidates": len(state.get("candidates") or []),
            "n_risk_out": sum(1 for r in rows.values()
                              if r.get("excluded_reason") == "risk"),
            "n_errors": sum(1 for r in rows.values() if r.get("errors")),
            "jev_input_tokens": state.get("jev_tokens"),
            "summary": json.dumps(state.get("summary"), ensure_ascii=False)
            if state.get("summary") is not None else None,
            "summary_model": None,
            # notified는 notify 노드 뒤에 알 수 있어 일단 False, 성공 시 UPDATE.
            "notified": 0,
            "prev_date": state.get("prev_date"),
            "warnings": warnings,
        },
    )
    return {"saved": True}


def _score_or_dash(value) -> str:
    return "?" if value is None else str(value)


def format_message(state: State) -> str:
    """전송 문구 조립. 순수 함수 — state 읽기만 한다."""
    today = state.get("today") or ""
    prev_date = state.get("prev_date") or ""
    rows: dict = state.get("rows") or {}
    picks: list = state.get("picks") or []
    summary: dict | None = state.get("summary")
    warnings: list = state.get("warnings") or []
    n_cut = sum(1 for r in rows.values()
                if str(r.get("excluded_reason") or "").startswith("cut:"))
    n_risk = sum(1 for r in rows.values()
                 if r.get("excluded_reason") == "risk")
    n_err = sum(1 for r in rows.values() if r.get("errors"))
    tokens = state.get("jev_tokens") or 0

    lines = [
        f"[스윙 후보] {today} (시세 기준 {prev_date})",
        f"후보 {len(state.get('candidates') or [])} · 1차컷 {n_cut} "
        f"· 리스크탈락 {n_risk} · 오류 {n_err} · Jev 토큰 {tokens}",
    ]
    if not picks:
        lines.append("오늘 조건을 통과한 후보 없음")
    thesis_by_ticker = {}
    if isinstance(summary, dict):
        for item in summary.get("picks") or []:
            if isinstance(item, dict) and item.get("ticker"):
                thesis_by_ticker[item["ticker"]] = item
    for i, ticker in enumerate(picks, 1):
        row = rows.get(ticker, {})
        scores = (
            f"재료{_score_or_dash(row.get('s1'))} "
            f"실적{_score_or_dash(row.get('s3'))} "
            f"수급{_score_or_dash(row.get('s4'))} "
            f"차트{_score_or_dash(row.get('s5'))}"
        )
        labels = [_SOURCE_LABELS.get(s, s) for s in row.get("sources") or []]
        lines.append(
            f"{i}. {row.get('name')}({ticker}) {row.get('total')}/8점 "
            f"[{scores}] 소스: {'·'.join(labels)}"
        )
        per = row.get("per")
        per_part = f"PER {per:.1f}" if per is not None \
            else f"PER {row.get('per_note')}" if row.get("per_note") \
            else "PER -"
        themes = row.get("themes") or []
        if themes:
            top = themes[0]
            ret = top.get("ret")
            theme_part = f"{top.get('name')}({ret:+.1f}%)" if ret is not None \
                else str(top.get("name"))
        else:
            theme_part = "없음"
        lines.append(f"   {per_part} · 테마: {theme_part}")
        item = thesis_by_ticker.get(ticker)
        if item:
            lines.append(f"   → {item.get('thesis')}")
            risks = item.get("risks") or []
            if risks:
                lines.append(f"   ⚠ {' / '.join(risks)}")
    if isinstance(summary, dict) and summary.get("overview"):
        lines.append(str(summary["overview"]))
    if warnings:
        lines.append("경고:")
        lines.extend(f"- {w}" for w in warnings)
    return "\n".join(lines)


def node_notify(state: State, deps: Deps) -> dict:
    message = format_message(state)
    if deps.dry_run:
        # 전송 대신 문구만 state에 — dry_run 검증은 이 문구로 한다.
        return {"message": message, "notified": False}
    notify_fn = deps.notify_fn or _real_notify
    ok = notify_fn(message, channel="batch")
    if ok and state.get("saved"):
        mark_notified(deps.store_path, state["today"])
    return {"message": message, "notified": bool(ok)}


def build_graph(deps: Deps):
    """8노드 직선 그래프. LangGraph 패턴은 youtube_analysis_langgraph 차용."""

    def collect_n(s: State) -> dict:
        return node_collect(s, deps)

    def prefilter_n(s: State) -> dict:
        return node_prefilter(s, deps)

    def jev_n(s: State) -> dict:
        return node_judge_jev(s, deps)

    def code_n(s: State) -> dict:
        return node_judge_code(s, deps)

    def rank_n(s: State) -> dict:
        return node_rank(s, deps)

    def summarize_n(s: State) -> dict:
        return node_summarize(s, deps)

    def store_n(s: State) -> dict:
        return node_store(s, deps)

    def notify_n(s: State) -> dict:
        return node_notify(s, deps)

    g = StateGraph(State)
    g.add_node("collect", collect_n)
    g.add_node("prefilter", prefilter_n)
    g.add_node("judge_jev", jev_n)
    g.add_node("judge_code", code_n)
    g.add_node("rank", rank_n)
    g.add_node("summarize", summarize_n)
    g.add_node("store", store_n)
    g.add_node("notify", notify_n)
    g.set_entry_point("collect")
    g.add_edge("collect", "prefilter")
    g.add_edge("prefilter", "judge_jev")
    g.add_edge("judge_jev", "judge_code")
    g.add_edge("judge_code", "rank")
    g.add_edge("rank", "summarize")
    g.add_edge("summarize", "store")
    g.add_edge("store", "notify")
    g.add_edge("notify", END)
    return g.compile()
