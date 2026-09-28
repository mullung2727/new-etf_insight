"""스윙 후보 Jev 판정: 1번 재료 지속성 점수 + 2번 리스크 이벤트 탈락.

설계: docs/PLAN_SWING_PICK.md §0-2(1·2번 행), §0-3(Jev 전체).
이 모듈은 Jev 판정까지만 한다. LangGraph·순위·DB 저장은 다음 단계라 여기서
만들지 않는다.

왜 Jev가 1·2번을 맡나:
- 글을 읽고 판단하는 항목이라 코드 규칙으로 못 한다. 재료가 1주~1달 시장
  관심을 유지할지, 자료에 희석 이벤트가 있는지는 텍스트 이해가 필요하다.
- GPT 대신 Jev인 이유: GPT 한도가 부족하고, Jev는 입력 $0.042/1M 토큰·출력
  무료라 후보 전부(하루 ~100종목)를 판정해도 하루 $0.1 미만이다 (PLAN §1).

판정값 배경:
- sustain score 범위 0~2: 보기 3개(없음/일회성·약함·강함)의 기대값이라 최대 2다.
  jev_candidate.sqlite3 실측 min 0.01·max 2.0 (2026-09-28).
- S1_EDGES 0.67/1.33: 0~2를 3등분한 경계다. 처음 0.33/0.67로 적은 건 0~1 가정
  오류였다 (PLAN §0-3).
- 2번은 점수가 아니라 탈락 게이트다. 유상증자·CB 등 희석 이벤트는 다른 점수가
  높아도 스윙 보유 중 물량 부담이 되므로, '예' 확률 0.5 이상이면 탈락이다.

입력 묶음 배경:
- 종목명 익명화(종목A): 모델이 종목 이름에 대한 사전 인상으로 판단하지 않게
  한다. 기존 research/jev_candidate 관례를 따른다.
- awake_realtimeCheck 제외: 순매도 상위 목록을 자동으로 올리는 봇이라 재료
  판단 입력으로는 잡음이다. 기존 jev_candidate EXCLUDED_CHANNELS와 같다.
- 토큰 상한 초과 시 텔레그램부터 줄이고 리포트는 마지막까지 유지한다.
  리포트가 재료의 가장 공신력 있는 출처라서다.
- 공시 10일은 사용자가 30일에서 줄인 값이다 (PLAN §6). 11일 이전 유상증자·CB
  공시는 못 본다 — 수용된 범위다.

의존성 배경:
- research/jev_candidate의 anonymize·approx_tokens·_tokens를 복사했다.
  운영 배치(etl/scripts)가 연구 코드(research/)를 import하면 연구 코드 수정이
  운영을 깨기 때문이다. 상수(JEV_MODEL 등)도 같은 이유로 복사한다.
- 공시·뉴스는 네트워크라 fetch 함수를 인자로 주입받는다. 운영 기본값은
  scripts.collect_trading_result_evidence의 fetch_filings·fetch_news다.

구조: 순수 함수(build_input·s1_from_score·risk_out_from)와 I/O(로더·fetch·Jev
호출)를 분리한다. DB 경로는 인자로 주입하고 읽기 전용으로 연다.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.report_metrics.storage import find_previous_target

# 버전 고정 — latest를 쓰면 판정이 조용히 바뀐다.
JEV_MODEL = "jev-1.13.0"
# 순매도 상위 목록 자동 봇. 재료 판단 입력으로는 잡음이라 뺀다.
EXCLUDED_CHANNELS = ("awake_realtimeCheck",)
PLACEHOLDER = "종목A"
# score < 0.67 → 0, < 1.33 → 1, 그 외 2. 0~2를 3등분한 경계다.
S1_EDGES = (0.67, 1.33)
# 2번 noul('예' 확률) >= 0.5 → 탈락. 점수가 아니라 게이트다.
RISK_OUT = 0.5
# 공시 조회 기간(달력일, 오늘 포함). 30일에서 사용자가 줄인 값.
FILING_DAYS = 10
NEWS_LIMIT = 8
# 입력 묶음 approx 토큰 상한. Jev state 한도 32k보다 여유 있게 잡는다.
STATE_TOKEN_LIMIT = 8000
# Jev 병렬 호출 수. 한도 1,200 req/분이라 8이면 충분하다.
MAX_WORKERS = 8

KST = ZoneInfo("Asia/Seoul")

# PLAN §0-3 문구 그대로. research/jev_candidate ask()와 같은 dict 형식이다.
QUESTIONS = {
    "sustain": {
        "type": "score",
        "instructions": "자료 전체를 종합할 때, 종목A 주가를 움직인 재료가 앞으로 1주~1달 동안 시장 관심을 유지할 근거의 강도",
        "criteria": ["없음/일회성", "약함", "강함"],
    },
    "risk": {
        "type": "noul",
        "instructions": "자료에 유상증자, CB·BW 발행, 보호예수 해제, 관리종목 지정, 대주주 매도 같은 지분 희석이나 물량 부담 이벤트가 있다",
    },
}

# 섹션 순서 고정. 비어 있으면 헤더 + "(없음)" — 섹션 유무 자체가 정보라서다.
_SECTION_ORDER = ("[텔레그램]", "[유튜브]", "[증권사 리포트]", "[공시]", "[뉴스]")
_MISSING = "(없음)"

# research/jev_candidate/state.py에서 복사 (운영↔연구 의존 분리, 위 배경 참조).
def approx_tokens(text: str) -> int:
    """한글 대충 세기 — len(text) // 2."""
    return len(text) // 2


def anonymize(text: str, mapping: dict[str, str]) -> str:
    """실명·티커 → 플레이스홀더. 긴 키부터 치환."""
    for real in sorted(mapping, key=len, reverse=True):
        text = text.replace(real, mapping[real])
    return text


def _collapse(text: str) -> str:
    """공백 뭉개기 — 개행·연속 공백을 1칸으로."""
    return " ".join(text.split())


def _posted_kst(raw: str) -> datetime:
    """posted_at_utc(오프셋 포함 ISO) → KST. naive면 UTC로 간주."""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


# research/jev_candidate/questions.py _tokens 복사 — 객체·dict·None 모두 받는다.
def _tokens(obj, key: str):
    """usage 토큰 읽기 — 객체·dict·None 모두 받는다."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _strip_html(content_html: str | None) -> str:
    """네이버 리서치 본문 정리 — 태그 제거 → 엔티티 복원 → 공백 뭉갬.

    태그를 먼저 지워야 &lt;p&gt; 같은 이스케이프 텍스트가 태그로 오인되지 않는다.
    """
    text = re.sub(r"<[^>]+>", " ", content_html or "")
    return _collapse(html.unescape(text))


def _connect_ro(db_path) -> sqlite3.Connection:
    """읽기 전용 연결. 로더는 전부 조회만 한다."""
    con = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def load_telegram_posts(tg_db, ticker, dates: list[str]) -> list[dict]:
    """최근 2거래일 해당 종목 언급 원문.

    telegram_stock_insights의 ticker·date_kst 행 전체(모든 session)에서
    source_post_refs 합집합 → telegram_posts 조회. EXCLUDED_CHANNELS 제외,
    posted_at_utc 오름차순(오래된 순).
    """
    if not dates:
        return []
    marks = ",".join("?" for _ in dates)
    with closing(_connect_ro(tg_db)) as con:
        rows = con.execute(
            f"SELECT source_post_refs FROM telegram_stock_insights"
            f" WHERE ticker = ? AND date_kst IN ({marks})",
            [ticker, *dates],
        ).fetchall()
        refs: set[str] = set()
        for row in rows:
            refs.update(json.loads(row["source_post_refs"] or "[]"))
        if not refs:
            return []
        ref_marks = ",".join("?" for _ in refs)
        chan_marks = ",".join("?" for _ in EXCLUDED_CHANNELS)
        posts = con.execute(
            f"SELECT post_ref, channel, posted_at_utc, text FROM telegram_posts"
            f" WHERE post_ref IN ({ref_marks})"
            f" AND channel NOT IN ({chan_marks})"
            f" ORDER BY posted_at_utc ASC",
            [*refs, *EXCLUDED_CHANNELS],
        ).fetchall()
    return [
        {"post_ref": r["post_ref"], "channel": r["channel"],
         "posted_at_utc": r["posted_at_utc"], "text": r["text"]}
        for r in posts
    ]


def load_youtube_items(yt_db, ticker, dates: list[str]) -> list[dict]:
    """해당 종목 언급 영상 요약. 날짜(행)당 1개.

    headline은 source_video_ids 첫 영상의 summary_json에서 가져온다.
    JSON 파싱 실패·행 없음이면 None — headline 없이도 analysis로 판단한다.
    """
    if not dates:
        return []
    marks = ",".join("?" for _ in dates)
    with closing(_connect_ro(yt_db)) as con:
        rows = con.execute(
            f"SELECT analysis, discovery_reason, source_video_ids"
            f" FROM youtube_stock_insights"
            f" WHERE ticker = ? AND date_kst IN ({marks}) ORDER BY date_kst ASC",
            [ticker, *dates],
        ).fetchall()
        parsed = [(r, json.loads(r["source_video_ids"] or "[]")) for r in rows]
        vids: set[str] = set()
        for _, ids in parsed:
            vids.update(ids)
        headlines: dict[str, str | None] = {}
        if vids:
            vid_marks = ",".join("?" for _ in vids)
            for s in con.execute(
                f"SELECT video_id, summary_json FROM youtube_video_summaries"
                f" WHERE video_id IN ({vid_marks})",
                [*vids],
            ).fetchall():
                try:
                    headlines[s["video_id"]] = json.loads(s["summary_json"]).get("headline")
                except (ValueError, TypeError, AttributeError):
                    headlines[s["video_id"]] = None
    return [
        {"headline": headlines.get(ids[0]) if ids else None,
         "analysis": r["analysis"], "discovery_reason": r["discovery_reason"]}
        for r, ids in parsed
    ]


def load_reports(report_db, ticker, start_exclusive, end_inclusive) -> list[dict]:
    """해당 종목 기간 리포트. report_date (start, end] — 직전 거래일 다음 날 ~ D.

    prev_target은 같은 증권사·종목의 직전 목표가. storage.find_previous_target
    재사용 — api/PDF 두 테이블 중 더 최근 쪽을 본다.
    """
    with closing(_connect_ro(report_db)) as con:
        rows = con.execute(
            "SELECT report_date, broker, title, opinion, goal_price, content_html"
            " FROM report_api_facts"
            " WHERE stock_code = ? AND report_date > ? AND report_date <= ?"
            " ORDER BY report_date ASC, broker ASC",
            (ticker, start_exclusive, end_inclusive),
        ).fetchall()
        out = []
        for r in rows:
            found, prev = find_previous_target(con, ticker, r["broker"], r["report_date"])
            out.append({
                "report_date": r["report_date"], "broker": r["broker"],
                "title": r["title"], "opinion": r["opinion"],
                "goal_price": r["goal_price"],
                "prev_target": prev if found else None,
                "content_html": r["content_html"],
            })
    return out


def collect_filings(dates: list[str], fetch_filings) -> tuple[dict[str, list[dict]], list[str]]:
    """날짜마다 fetch_filings(date) 1회(날짜별 전 종목) → 종목코드 인덱싱.

    날짜 하나가 실패해도 나머지는 수집하고 failed_dates에 기록한다.
    하루 공시가 비었다고 전 후보 판정을 막을 이유가 없어서다.
    운영 기본값: scripts.collect_trading_result_evidence.fetch_filings.
    """
    by_ticker: dict[str, list[dict]] = {}
    failed_dates: list[str] = []
    for day in dates:
        try:
            items = fetch_filings(day)
        except Exception:
            failed_dates.append(day)
            continue
        for item in items or []:
            code = (item.get("stock_code") or "").strip()
            if not code:
                continue
            by_ticker.setdefault(code, []).append(
                {"rcept_dt": item.get("rcept_dt"), "report_nm": item.get("report_nm")}
            )
    return by_ticker, failed_dates


def _telegram_lines(posts: list[dict]) -> list[str]:
    """오래된 순. 시각은 KST MM-DD HH:MM, 본문은 600자로 자른다."""
    ordered = sorted(posts, key=lambda p: _posted_kst(p["posted_at_utc"]))
    return [
        f"- {_posted_kst(p['posted_at_utc']).strftime('%m-%d %H:%M')}"
        f" {_collapse(p['text'])[:600]}"
        for p in ordered
    ]


def _youtube_lines(items: list[dict]) -> list[str]:
    return [
        f"- {it.get('headline') or ''}: {it.get('analysis') or it.get('discovery_reason') or ''}"
        for it in items
    ]


def _report_lines(reports: list[dict]) -> list[str]:
    return [
        f"- {r['report_date']} {r['broker']} {r['title']}"
        f" | 의견 {r['opinion']} | 목표가 {r['goal_price']} (직전 {r['prev_target']})"
        f" | {_strip_html(r.get('content_html'))[:400]}"
        for r in reports
    ]


def _filing_lines(filings: list[dict]) -> list[str]:
    return [f"- {f.get('rcept_dt')} {f.get('report_nm')}" for f in filings]


def _news_lines(news: list[dict]) -> list[str]:
    return [f"- {(n.get('published_at') or '')[:10]} {n.get('title')}" for n in news]


def _assemble(posts, youtube_items, reports, filings, news) -> str:
    """섹션 조립 (익명화 전). 순수 조립이라 토큰 초과 시 재호출한다."""
    bodies = {
        "[텔레그램]": _telegram_lines(posts),
        "[유튜브]": _youtube_lines(youtube_items),
        "[증권사 리포트]": _report_lines(reports),
        "[공시]": _filing_lines(filings),
        "[뉴스]": _news_lines(news),
    }
    parts = []
    for header in _SECTION_ORDER:
        lines = bodies[header]
        parts.append(header + "\n" + ("\n".join(lines) if lines else _MISSING))
    return "\n".join(parts)


def _drop_oldest(items: list[dict], key: str) -> None:
    """날짜 키 최소값 1개 제거. 키가 비었으면 리스트 앞부터 줄인다."""
    if not items:
        return
    idx = min(range(len(items)), key=lambda i: items[i].get(key) or "")
    items.pop(idx)


def build_input(ticker, name, *, telegram_posts, youtube_items, reports, filings, news) -> str:
    """종목당 Jev 입력 묶음 1개. 순수 함수 — DB·네트워크를 안 건드린다.

    토큰 상한 초과 시 텔레그램 글을 오래된 것부터 하나씩 빼며 재구성하고,
    그래도 넘으면 뉴스 → 공시 순으로 줄인다. 리포트는 마지막까지 유지한다 —
    재료의 가장 공신력 있는 출처라서다. (리포트·유튜브만으로 넘으면 그대로 둔다.
    더 줄일 게 없다는 뜻이라 조용히 자르기보다 원문 유지를 택한다.)
    """
    # 미리 오래된 순으로 정렬 — 토큰 초과 시 앞(가장 오래된 글)부터 뺀다.
    posts = sorted(telegram_posts, key=lambda p: _posted_kst(p["posted_at_utc"]))
    news_items = list(news)
    filing_items = list(filings)
    # 빈 키 치환 금지 — "".replace는 글자 사이마다 끼워넣어 본문을 망가뜨린다.
    mapping = {k: PLACEHOLDER for k in (name, ticker) if k}
    while True:
        text = anonymize(
            _assemble(posts, youtube_items, reports, filing_items, news_items), mapping
        )
        if approx_tokens(text) <= STATE_TOKEN_LIMIT:
            return text
        if posts:
            posts.pop(0)
        elif news_items:
            _drop_oldest(news_items, "published_at")
        elif filing_items:
            _drop_oldest(filing_items, "rcept_dt")
        else:
            return text


def ask_jev(client, text) -> dict:
    """Jev 1회 호출 — sustain score + risk noul + 입력 토큰. 예외는 호출자가 처리."""
    resp = client.system_one(text, QUESTIONS, model=JEV_MODEL)
    answers = getattr(resp, "answers", {}) or {}
    return {
        "jev_sustain": float(answers["sustain"].score),
        "jev_risk": float(answers["risk"].noul),
        "input_tokens": _tokens(getattr(resp, "usage", None), "input_tokens"),
    }


def s1_from_score(score) -> int:
    """1번 3단계 변환. 경계값(0.67/1.33)은 윗 단계에 포함 — PLAN §0-2 표."""
    if score < S1_EDGES[0]:
        return 0
    if score < S1_EDGES[1]:
        return 1
    return 2


def risk_out_from(noul) -> bool:
    """2번 탈락 게이트. '예' 확률 0.5 이상이면 탈락."""
    return noul >= RISK_OUT


def judge_jev(candidates, *, today, prev_trading_date, report_start_exclusive,
              client, tg_db, yt_db, report_db, fetch_filings, fetch_news,
              max_workers=MAX_WORKERS) -> dict:
    """전 후보 Jev 판정 1회분.

    텔레그램·유튜브는 최근 2거래일([prev_trading_date, today]), 공시는 today 포함
    최근 FILING_DAYS 달력일을 전 후보 공용으로 1회 수집한다. 뉴스는 종목별 조회
    (as_of = today 19:00 KST — 배치 시각이라 그 이후 뉴스는 못 본다).
    Jev 호출만 ThreadPoolExecutor로 병렬화한다. DB 읽기는 메인 스레드에서
    순차 처리 — sqlite 연결을 스레드에 나눠주는 복잡함을 피하려고.
    """
    day0 = datetime.strptime(today, "%Y-%m-%d").date()
    filing_dates = [
        (day0 - timedelta(days=i)).isoformat() for i in range(FILING_DAYS - 1, -1, -1)
    ]
    filings_by_ticker, failed_filing_dates = collect_filings(filing_dates, fetch_filings)
    as_of = datetime.strptime(today, "%Y-%m-%d").replace(
        hour=19, minute=0, tzinfo=KST)
    dates2 = [prev_trading_date, today]

    jobs = []
    for c in candidates:
        ticker, name = c["ticker"], c["name"]
        errors: dict[str, str] = {}
        # 로더 실패(DB 잠금·테이블 없음 등)도 그 소스만 비우고 계속한다 —
        # 한 종목·한 소스 오류가 전 후보 판정을 멈추면 안 된다.
        loaded = {}
        for key, load in (
            ("telegram", lambda: load_telegram_posts(tg_db, ticker, dates2)),
            ("youtube", lambda: load_youtube_items(yt_db, ticker, dates2)),
            ("reports", lambda: load_reports(report_db, ticker, report_start_exclusive, today)),
        ):
            try:
                loaded[key] = load()
            except Exception as e:
                errors[key] = type(e).__name__
                loaded[key] = []
        posts, yt, reports = loaded["telegram"], loaded["youtube"], loaded["reports"]
        try:
            news = fetch_news(name, ticker, as_of, NEWS_LIMIT)
        except Exception as e:
            # 뉴스 실패는 그 종목 입력에서 뉴스만 빼고 계속한다.
            errors["news"] = type(e).__name__
            news = []
        text = build_input(
            ticker, name, telegram_posts=posts, youtube_items=yt,
            reports=reports, filings=filings_by_ticker.get(ticker, []), news=news,
        )
        jobs.append((ticker, text, errors))

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(ask_jev, client, text): (ticker, text, errors)
                for ticker, text, errors in jobs}
        for fut in as_completed(futs):
            ticker, text, errors = futs[fut]
            text_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
            try:
                ans = fut.result()
            except Exception as e:
                # Jev 예외는 그 종목만 None 처리하고 나머지는 계속한다.
                results[ticker] = {
                    "s1": None, "risk_out": None,
                    "jev_sustain": None, "jev_risk": None, "input_tokens": None,
                    "jev_input_hash": text_hash, "input_text": text,
                    "errors": {**errors, "jev": type(e).__name__},
                }
                continue
            results[ticker] = {
                "s1": s1_from_score(ans["jev_sustain"]),
                "risk_out": risk_out_from(ans["jev_risk"]),
                "jev_sustain": ans["jev_sustain"], "jev_risk": ans["jev_risk"],
                "input_tokens": ans["input_tokens"],
                "jev_input_hash": text_hash, "input_text": text,
                "errors": errors,
            }
    total = sum(r["input_tokens"] for r in results.values()
                if r["input_tokens"] is not None)
    return {"results": results, "failed_filing_dates": failed_filing_dates,
            "input_tokens_total": total}
