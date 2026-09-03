"""당일 매도 종목이 왜 그렇게 움직였는지의 근거를 모으고 등급을 판정한다.

두 단계다.

  1. 수집 (LLM 없음): 매도 체결 종목마다 공시·뉴스·텔레그램을 긁고 매수근거를 붙인다.
  2. 판정 (LLM): 종목별로 원인과 A/B/C 등급을 받는다. A인데 근거 링크가 없거나
     수집되지 않은 링크를 대면 C로 강등한다.

수집만 하려면 `--grade` 를 빼면 된다. 소스가 비어 있으면 이 기능 자체가 무의미하므로
LLM을 붙이기 전에 수집 결과를 눈으로 확인하는 용도다.

Usage (from etl/):
    uv run python scripts/collect_trading_result_evidence.py --date 20260828
    uv run python scripts/collect_trading_result_evidence.py --date 20260828 --grade
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  (cp949 가드 + path)

import argparse
import datetime as dt
import email.utils
import json
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from report_daily_trading_result import DEFAULT_WATCHLIST_DB, _date_dash, load_filled_sells
from wl_sqlite import connect_rw


SEOUL = ZoneInfo("Asia/Seoul")
ETL_DIR = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_TELEGRAM_DB = ETL_DIR / "db" / "telegram_public.sqlite3"
PROMPT_PATH = Path(__file__).resolve().with_name("trading_result_cause.md")
SCHEMA_PATH = Path(__file__).resolve().with_name("trading_result_cause_schema.json")
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

# 보고 배치 시각. 16:00 텔레그램 장마감 세션과 장 종료 후 공시가 들어온 뒤다.
REPORT_HOUR = 16
REPORT_MINUTE = 20


def as_of_kst(date_kst: str) -> dt.datetime:
    """원인 판정의 정보 차단선. 이 시각 이후에 생긴 정보는 쓰지 않는다."""
    target = dt.datetime.strptime(_date_dash(date_kst), "%Y-%m-%d")
    return target.replace(hour=REPORT_HOUR, minute=REPORT_MINUTE, tzinfo=SEOUL)


def _parse_point_in_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(SEOUL)


# ── 소스 1: 뉴스 ────────────────────────────────────────────────────────────────

def fetch_news(name: str, ticker: str, as_of: dt.datetime, limit: int = 8) -> list[dict]:
    """구글 뉴스 RSS에서 as_of 이전 기사만.

    research/watchlist_expected_return 의 fetch_historical_news 와 같은 질의다.
    그 모듈은 최상위에서 langgraph·duckdb 를 import 해 운영 배치가 끌어오기엔 무거워
    여기 따로 둔다.
    """
    lower = (as_of.date() - dt.timedelta(days=3)).isoformat()
    upper = (as_of.date() + dt.timedelta(days=1)).isoformat()
    response = requests.get(
        "https://news.google.com/rss/search",
        params={
            "q": f'"{name}" {ticker} 주식 after:{lower} before:{upper}',
            "hl": "ko",
            "gl": "KR",
            "ceid": "KR:ko",
        },
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    output: list[dict] = []
    for node in root.findall(".//item"):
        title = " ".join((node.findtext("title") or "").split())
        link = " ".join((node.findtext("link") or "").split())
        try:
            published = email.utils.parsedate_to_datetime(
                node.findtext("pubDate") or ""
            ).astimezone(SEOUL)
        except (TypeError, ValueError):
            continue
        if title and published <= as_of:
            output.append(
                {"title": title, "link": link, "published_at": published.isoformat()}
            )
        if len(output) >= limit:
            break
    return output


# ── 소스 2: 공시 ────────────────────────────────────────────────────────────────

def fetch_filings(date_kst: str) -> list[dict]:
    """지정일 DART 접수 공시 전체. 종목 필터는 호출부에서 stock_code 로 한다."""
    from new_etf_insight.dart_client import fetch_all_filings, get_api_key

    day = _date_dash(date_kst).replace("-", "")
    filings, _ = fetch_all_filings(get_api_key(), day, day)
    return filings


def index_filings_by_ticker(filings: list[dict], tickers: set[str]) -> dict[str, list[dict]]:
    output: dict[str, list[dict]] = {ticker: [] for ticker in tickers}
    for item in filings:
        code = (item.get("stock_code") or "").strip()
        if code not in output:
            continue
        rcept_no = (item.get("rcept_no") or "").strip()
        output[code].append(
            {
                "rcept_no": rcept_no,
                "report_nm": item.get("report_nm"),
                "flr_nm": item.get("flr_nm"),
                "rcept_dt": item.get("rcept_dt"),
                "link": DART_VIEWER_URL.format(rcept_no) if rcept_no else None,
            }
        )
    return output


# ── 소스 3: 텔레그램 ────────────────────────────────────────────────────────────

def load_telegram(
    db_path: Path, tickers: set[str], date_kst: str, as_of: dt.datetime
) -> dict[str, list[dict]]:
    """당일 텔레그램 종목 인사이트. 스코어링과 달리 장마감(close) 세션도 포함한다.

    스코어링은 15:00에 돌아 close 세션을 볼 수 없지만 이 배치는 16:20이라 볼 수 있다.
    장중 무슨 일이 있었는지는 대부분 close 세션에 들어온다.
    """
    output: dict[str, list[dict]] = {ticker: [] for ticker in tickers}
    if not output:
        return output
    date_dash = _date_dash(date_kst)
    marks = ",".join("?" for _ in output)
    with closing(sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT date_kst, session, ticker, mention_channels, source_post_refs,
                   discovery_reason, analysis, created_at
            FROM telegram_stock_insights
            WHERE date_kst=? AND ticker IN ({marks})
            ORDER BY CASE session WHEN 'morning' THEN 0 WHEN 'close' THEN 1 ELSE 2 END
            """,
            [date_dash, *output.keys()],
        ).fetchall()
    for row in rows:
        created_at = _parse_point_in_time(row["created_at"])
        if not created_at or created_at > as_of:
            continue
        output[row["ticker"]].append(
            {
                "session": row["session"],
                "created_at": created_at.isoformat(timespec="seconds"),
                "channels": json.loads(row["mention_channels"] or "[]"),
                "post_refs": json.loads(row["source_post_refs"] or "[]"),
                "discovery_reason": row["discovery_reason"],
                "analysis": json.loads(row["analysis"]) if row["analysis"] else None,
            }
        )
    return output


# ── 매수근거 ────────────────────────────────────────────────────────────────────

def _trim_catalyst(catalyst: dict, *, full: bool) -> dict:
    """프롬프트에 넣을 재료 요약.

    evidence_refs 는 일부러 뺀다. 스코어링 당일 뉴스 링크라 오늘 수집한 근거가 아니고,
    프롬프트에 남겨두면 LLM 이 오늘의 근거로 인용해 환각 판정에 걸린다.
    """
    keys = (
        "label", "description", "category_raw", "status",
        "expected_duration", "alive_score", "reason", "invalidation",
    ) if full else (
        "label", "category_raw", "status", "expected_duration", "alive_score",
    )
    return {key: catalyst.get(key) for key in keys if catalyst.get(key) is not None}


def load_catalyst_assessment(
    con: sqlite3.Connection, tickers: set[str], date_compact: str
) -> dict[str, dict]:
    """스코어링 단계가 남긴 사전 재료 판단(llm_catalyst_assessments).

    llm_scores.category 는 스코어링 파이프라인이 박아 넣는 상수라 재료 정보가 없다.
    실제 재료 종류·지속 예상기간·무효화 조건은 이 테이블에만 있다.

    테이블은 스코어링 파이프라인이 만든다. 아직 안 돈 DB 에는 없을 수 있으므로
    없으면 조용히 비운다(정산 보고를 막을 이유가 없다).
    """
    output: dict[str, dict] = {}
    marks = ",".join("?" for _ in tickers)
    try:
        rows = con.execute(
            f"""
            SELECT date, ticker, primary_status, primary_duration, primary_alive_score,
                   max_alive_score, assessment_json, theme_scores_json,
                   theme_event_direction
            FROM llm_catalyst_assessments
            WHERE ticker IN ({marks}) AND date <= ?
            ORDER BY date
            """,
            [*sorted(tickers), date_compact],
        ).fetchall()
    except sqlite3.OperationalError:
        return output

    for row in rows:  # date 오름차순이라 마지막 행이 최신
        try:
            assessment = json.loads(row["assessment_json"] or "{}")
        except json.JSONDecodeError:
            assessment = {}
        entry = {
            "assessed_date": row["date"],
            "primary_status": row["primary_status"],
            "primary_duration": row["primary_duration"],
            "primary_alive_score": row["primary_alive_score"],
            "max_alive_score": row["max_alive_score"],
        }
        primary = assessment.get("primary_catalyst")
        if isinstance(primary, dict):
            entry["primary_catalyst"] = _trim_catalyst(primary, full=True)
        secondary = assessment.get("secondary_catalysts")
        if isinstance(secondary, list) and secondary:
            entry["secondary_catalysts"] = [
                _trim_catalyst(item, full=False) for item in secondary if isinstance(item, dict)
            ]
        if row["theme_scores_json"]:
            try:
                entry["theme_scores"] = json.loads(row["theme_scores_json"])
            except json.JSONDecodeError:
                pass
        if row["theme_event_direction"]:
            entry["theme_event_direction"] = row["theme_event_direction"]
        output[row["ticker"]] = entry
    return output


def load_buy_rationale(db_path: Path, tickers: set[str], date_kst: str) -> dict[str, dict]:
    """매수 판단에 쓰인 llm_scores 행 + 사전 재료 판단. 종목명도 여기서 가져온다.

    매도 종목은 전부 워치리스트를 거쳐 매수됐으므로 llm_scores 에 있다.
    지정일 이전 가장 최근 판정을 쓴다.

    llm_scores.date 는 하이픈 없는 YYYYMMDD 다. 주문 테이블의 sold_at(하이픈)과
    포맷이 달라, 하이픈 날짜로 비교하면 문자열 비교에서 전 행이 탈락한다(실측).
    """
    output: dict[str, dict] = {}
    if not tickers:
        return output
    date_compact = _date_dash(date_kst).replace("-", "")
    marks = ",".join("?" for _ in tickers)
    with closing(sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"""
            SELECT date, ticker, name, score, category, reason_summary, final_opinion
            FROM llm_scores
            WHERE ticker IN ({marks}) AND date <= ?
            ORDER BY date
            """,
            [*sorted(tickers), date_compact],
        ).fetchall()
        catalysts = load_catalyst_assessment(con, tickers, date_compact)
    for row in rows:  # date 오름차순이라 마지막 행이 최신
        output[row["ticker"]] = {
            "scored_date": row["date"],
            "name": row["name"],
            "score": row["score"],
            "category": row["category"],
            "reason_summary": row["reason_summary"],
            "final_opinion": row["final_opinion"],
        }
    for ticker, catalyst in catalysts.items():
        output.setdefault(ticker, {})["catalyst"] = catalyst
    return output


# ── 수집 ────────────────────────────────────────────────────────────────────────

def collect_evidence(
    date_kst: str,
    *,
    watchlist_db: Path = DEFAULT_WATCHLIST_DB,
    telegram_db: Path = DEFAULT_TELEGRAM_DB,
    news_fn=fetch_news,
    filings_fn=fetch_filings,
) -> dict:
    """매도 종목별 근거 묶음. 소스 하나가 죽어도 나머지로 계속한다."""
    as_of = as_of_kst(date_kst)
    trades = load_filled_sells(watchlist_db, date_kst)
    tickers = {row["ticker"] for row in trades}
    warnings: list[str] = []

    rationale = load_buy_rationale(watchlist_db, tickers, date_kst)
    for ticker in sorted(tickers - set(rationale)):
        warnings.append(f"buy_rationale_missing:{ticker}")

    try:
        filings_by_ticker = index_filings_by_ticker(filings_fn(date_kst), tickers)
    except Exception as exc:  # noqa: BLE001 — 소스 장애로 배치를 죽이지 않는다
        filings_by_ticker = {ticker: [] for ticker in tickers}
        warnings.append(f"filings_fetch_failed:{type(exc).__name__}")

    try:
        telegram_by_ticker = load_telegram(telegram_db, tickers, date_kst, as_of)
    except Exception as exc:  # noqa: BLE001
        telegram_by_ticker = {ticker: [] for ticker in tickers}
        warnings.append(f"telegram_load_failed:{type(exc).__name__}")

    records = []
    for ticker in sorted(tickers):
        buy = rationale.get(ticker, {})
        name = buy.get("name")
        if name:
            try:
                news = news_fn(name, ticker, as_of)
            except Exception as exc:  # noqa: BLE001 — 종목 단위 격리
                news = []
                warnings.append(f"news_fetch_failed:{ticker}:{type(exc).__name__}")
        else:
            news = []
            warnings.append(f"news_skipped_no_name:{ticker}")
        records.append(
            {
                "ticker": ticker,
                "name": name,
                "trades": [
                    {
                        key: row[key]
                        for key in ("strategy", "buy_price", "sell_price", "sold_at",
                                    "exit_reason", "pnl_pct", "sell_pl_won", "bought_date")
                    }
                    for row in trades
                    if row["ticker"] == ticker
                ],
                "buy_rationale": buy or None,
                "filings": filings_by_ticker.get(ticker, []),
                "news": news,
                "telegram": telegram_by_ticker.get(ticker, []),
            }
        )
    return {
        "date": _date_dash(date_kst),
        "as_of": as_of.isoformat(timespec="seconds"),
        "tickers": records,
        "warnings": warnings,
    }


# ── 판정 ────────────────────────────────────────────────────────────────────────

def allowed_refs(record: dict) -> set[str]:
    """LLM이 근거로 댈 수 있는 값 전체. 여기 없는 값은 환각이다."""
    refs: set[str] = set()
    for filing in record["filings"]:
        refs.update(value for value in (filing.get("rcept_no"), filing.get("link")) if value)
    refs.update(item["link"] for item in record["news"] if item.get("link"))
    for insight in record["telegram"]:
        refs.update(ref for ref in insight.get("post_refs", []) if isinstance(ref, str))
    return refs


def has_any_source(record: dict) -> bool:
    return bool(record["filings"] or record["news"] or record["telegram"])


# 근거가 강한 쪽부터. 거리 계산(변동폭)에 순서를 그대로 쓴다.
GRADES = ("A", "B", "C", "D", "E")
GRADE_INDEX = {grade: index for index, grade in enumerate(GRADES)}
NO_SOURCE_GRADE = "E"      # 소스가 0건이면 코드가 강제한다. LLM 판단이 아니다.
UNSUPPORTED_GRADE = "D"    # 소스는 있는데 주장을 뒷받침할 링크가 없을 때
REF_REQUIRED_GRADES = ("A", "B")  # 원인을 특정했다고 주장하는 등급


def enforce_grade(judgement: dict, record: dict) -> tuple[dict, list[str]]:
    """LLM 등급을 근거로 검증하고 못 미치면 강등한다.

    LLM은 물어보면 반드시 그럴듯한 원인을 만들어낸다. 강등이 그걸 막는 유일한 장치다.

    강등 목적지가 둘로 나뉜다. 소스가 아예 없으면 E(원인 불명)로 보내고, 소스는 있는데
    링크를 못 대면 D(정황)로 보낸다. 둘을 E 로 합치면 '근거가 없었다'와 '근거는 있는데
    LLM 주장을 못 믿는다'가 한 칸에 뭉쳐 나중에 구분이 안 된다.
    """
    result = dict(judgement)
    result["evidence_refs"] = list(result.get("evidence_refs") or [])
    warnings: list[str] = []
    ticker = record["ticker"]

    unknown = sorted(set(result["evidence_refs"]) - allowed_refs(record))
    if unknown:
        result["evidence_refs"] = [
            ref for ref in result["evidence_refs"] if ref not in unknown
        ]
        warnings.append(f"hallucinated_ref:{ticker}:{','.join(unknown)}")

    if not has_any_source(record):
        if result["grade"] != NO_SOURCE_GRADE:
            warnings.append(f"downgraded_no_source:{ticker}:{result['grade']}")
        result["grade"] = NO_SOURCE_GRADE
    elif result["grade"] in REF_REQUIRED_GRADES and unknown:
        # 지어낸 링크를 걷어내면 남는 게 없어 '링크 없음'과 겹치지만, 원인이 다르므로
        # 환각을 먼저 판정한다. 둘을 한 경고로 합치면 어느 쪽인지 구분이 안 된다.
        warnings.append(f"downgraded_hallucinated_ref:{ticker}:{result['grade']}")
        result["grade"] = UNSUPPORTED_GRADE
    elif result["grade"] in REF_REQUIRED_GRADES and not result["evidence_refs"]:
        warnings.append(f"downgraded_without_ref:{ticker}:{result['grade']}")
        result["grade"] = UNSUPPORTED_GRADE

    if result["grade"] == NO_SOURCE_GRADE:
        result["buy_rationale_match"] = "unknown"
    return result, warnings


def median_judgement(judgements: list[dict]) -> tuple[dict, int]:
    """여러 번 판정한 결과에서 중앙값 등급의 판정과 변동폭을 고른다.

    평균을 쓰지 않는다. A~E 를 숫자로 평균 내면 2.33 같은 값이 나와 어느 칸인지
    사람이 다시 판단해야 한다. 중앙값이면 셋 중 둘이 같을 때 그것이 그대로 답이다.
    반복 횟수가 짝수라 가운데가 둘이면 근거가 약한 쪽(뒤 글자)을 택한다.

    원인 문장은 평균이 없으므로 중앙값 등급을 낸 판정을 통째로 쓴다.
    """
    ordered = sorted(judgements, key=lambda item: GRADE_INDEX[item["grade"]])
    picked = ordered[len(ordered) // 2]
    spread = GRADE_INDEX[ordered[-1]["grade"]] - GRADE_INDEX[ordered[0]["grade"]]
    return picked, spread


def make_cause_prompt(date_kst: str, record: dict) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        date=_date_dash(date_kst),
        ticker_json=json.dumps(record, ensure_ascii=False, indent=2),
    )


DEFAULT_REPEAT = 3
UNSTABLE_SPREAD = 2  # 이만큼 벌어지면 프롬프트가 애매하다는 신호


def grade_evidence(
    evidence: dict,
    *,
    generate=None,
    model: str | None = None,
    repeat: int = DEFAULT_REPEAT,
) -> dict:
    """종목마다 repeat 번 판정해 중앙값을 쓴다. 종목 하나가 실패해도 계속한다.

    같은 입력이라도 LLM 등급이 실행마다 흔들린다(실측). 한 번만 물으면 그 흔들림이
    그대로 결과가 되므로 여러 번 물어 중앙값을 취하고, 흔들린 폭을 같이 남긴다.
    """
    if repeat < 1:
        raise ValueError("repeat must be >= 1")
    if generate is None:
        from new_etf_insight.llm import generate_json

        def generate(prompt: str) -> str:
            return generate_json(
                prompt, output_schema_path=SCHEMA_PATH, search=False, model=model
            )

    warnings = list(evidence["warnings"])
    for record in evidence["tickers"]:
        prompt = make_cause_prompt(evidence["date"], record)
        judgements: list[dict] = []
        failures: list[str] = []
        for _ in range(repeat):
            try:
                judgement = json.loads(generate(prompt))
            except Exception as exc:  # noqa: BLE001 — 회차 단위 격리
                failures.append(type(exc).__name__)
                continue
            judgement, enforced = enforce_grade(judgement, record)
            judgements.append(judgement)
            warnings.extend(enforced)

        if not judgements:
            # 전 회차가 실패해야 판정 실패다. 일부만 실패하면 남은 것으로 판정한다.
            record["judgement"] = None
            record["grade_runs"] = []
            record["grade_spread"] = None
            warnings.append(f"grade_failed:{record['ticker']}:{','.join(failures) or 'unknown'}")
            continue
        if failures:
            warnings.append(f"grade_partial:{record['ticker']}:{len(failures)}/{repeat}")

        picked, spread = median_judgement(judgements)
        record["judgement"] = picked
        record["grade_runs"] = [item["grade"] for item in judgements]
        record["grade_spread"] = spread
        if spread >= UNSTABLE_SPREAD:
            runs = "".join(record["grade_runs"])
            warnings.append(f"grade_unstable:{record['ticker']}:{runs}")

    evidence["warnings"] = warnings
    evidence["repeat"] = repeat
    evidence["grade_counts"] = {
        grade: sum(
            1
            for record in evidence["tickers"]
            if (record.get("judgement") or {}).get("grade") == grade
        )
        for grade in GRADES
    }
    return evidence


# ── 저장 ────────────────────────────────────────────────────────────────────────

CAUSE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS trading_result_causes (
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    strategy TEXT NOT NULL,
    name TEXT,
    bought_date TEXT,
    held_days INTEGER,
    pnl_pct REAL,
    exit_reason TEXT,
    grade TEXT NOT NULL,
    cause TEXT NOT NULL,
    buy_rationale_match TEXT NOT NULL,
    reasoning TEXT,
    evidence_refs_json TEXT NOT NULL,
    catalyst_date TEXT,
    catalyst_category_raw TEXT,
    catalyst_status TEXT,
    catalyst_expected_duration TEXT,
    theme_scores_json TEXT,
    theme_event_direction TEXT,
    grade_runs_json TEXT NOT NULL,
    grade_spread INTEGER,
    filing_count INTEGER NOT NULL,
    news_count INTEGER NOT NULL,
    telegram_count INTEGER NOT NULL,
    as_of TEXT NOT NULL,
    model TEXT,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (date, ticker, strategy)
)
"""

CAUSE_COLUMNS = (
    "date", "ticker", "strategy", "name", "bought_date", "held_days", "pnl_pct",
    "exit_reason", "grade", "cause", "buy_rationale_match", "reasoning",
    "evidence_refs_json", "catalyst_date", "catalyst_category_raw", "catalyst_status",
    "catalyst_expected_duration", "theme_scores_json", "theme_event_direction",
    "grade_runs_json", "grade_spread",
    "filing_count", "news_count", "telegram_count", "as_of", "model", "generated_at",
)


def _held_days(bought_date: object, date_compact: str) -> int | None:
    """매수일부터 매도일까지 달력 일수. 거래일 수가 아니다."""
    if not isinstance(bought_date, str) or len(bought_date) != 8 or not bought_date.isdigit():
        return None
    try:
        start = dt.datetime.strptime(bought_date, "%Y%m%d").date()
        end = dt.datetime.strptime(date_compact, "%Y%m%d").date()
    except ValueError:
        return None
    return (end - start).days


def cause_rows(evidence: dict, *, model: str | None = None, generated_at: str | None = None) -> list[dict]:
    """저장할 행. 판정이 없는 종목(수집만 했거나 LLM 실패)은 남기지 않는다."""
    date_compact = evidence["date"].replace("-", "")
    generated_at = generated_at or dt.datetime.now(SEOUL).isoformat(timespec="seconds")
    rows: list[dict] = []
    for record in evidence.get("tickers", []):
        judgement = record.get("judgement")
        if not judgement:
            continue
        catalyst = (record.get("buy_rationale") or {}).get("catalyst") or {}
        primary = catalyst.get("primary_catalyst") or {}
        # 한 종목이 두 전략에 동시에 잡히면 손익률이 다르다. 전략별로 한 행씩 남긴다.
        for trade in record.get("trades", []):
            rows.append(
                {
                    "date": date_compact,
                    "ticker": record["ticker"],
                    "strategy": trade["strategy"],
                    "name": record.get("name"),
                    "bought_date": trade.get("bought_date"),
                    "held_days": _held_days(trade.get("bought_date"), date_compact),
                    "pnl_pct": trade.get("pnl_pct"),
                    "exit_reason": trade.get("exit_reason"),
                    "grade": judgement["grade"],
                    "cause": judgement["cause"],
                    "buy_rationale_match": judgement["buy_rationale_match"],
                    "reasoning": judgement.get("reasoning"),
                    "evidence_refs_json": json.dumps(
                        judgement.get("evidence_refs") or [], ensure_ascii=False
                    ),
                    "catalyst_date": catalyst.get("assessed_date"),
                    "catalyst_category_raw": primary.get("category_raw"),
                    "catalyst_status": catalyst.get("primary_status"),
                    "catalyst_expected_duration": catalyst.get("primary_duration"),
                    "theme_scores_json": (
                        json.dumps(catalyst["theme_scores"], ensure_ascii=False)
                        if catalyst.get("theme_scores") else None
                    ),
                    "theme_event_direction": catalyst.get("theme_event_direction"),
                    "grade_runs_json": json.dumps(record.get("grade_runs") or []),
                    "grade_spread": record.get("grade_spread"),
                    "filing_count": len(record.get("filings") or []),
                    "news_count": len(record.get("news") or []),
                    "telegram_count": len(record.get("telegram") or []),
                    "as_of": evidence["as_of"],
                    "model": model,
                    "generated_at": generated_at,
                }
            )
    return rows


def save_causes(
    db_path: Path, evidence: dict, *, model: str | None = None, generated_at: str | None = None
) -> int:
    """판정 결과를 watchlist DB 에 남긴다. 같은 날 재실행하면 덮어쓴다.

    보고문에만 찍고 버리면 이슈별 지속성 비교를 나중에 할 수 없다.
    """
    rows = cause_rows(evidence, model=model, generated_at=generated_at)
    if not rows:
        return 0
    marks = ",".join("?" for _ in CAUSE_COLUMNS)
    with connect_rw(db_path) as con:
        con.execute(CAUSE_TABLE_DDL)
        con.executemany(
            f"INSERT OR REPLACE INTO trading_result_causes "
            f"({','.join(CAUSE_COLUMNS)}) VALUES ({marks})",
            [tuple(row[column] for column in CAUSE_COLUMNS) for row in rows],
        )
    return len(rows)


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="당일 매도 종목 등락 원인 근거 수집·판정")
    parser.add_argument("--date", help="YYYYMMDD 또는 YYYY-MM-DD; 기본값은 오늘 KST")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--telegram-db", type=Path, default=DEFAULT_TELEGRAM_DB)
    parser.add_argument("--grade", action="store_true", help="LLM 원인·등급 판정까지 수행")
    parser.add_argument("--model", default=None, help="LLM 모델; 생략하면 기존 기본값")
    parser.add_argument(
        "--repeat", type=int, default=DEFAULT_REPEAT,
        help=f"종목당 판정 횟수. 중앙값을 쓴다 (기본 {DEFAULT_REPEAT})",
    )
    parser.add_argument("--out", type=Path, help="결과 JSON 저장 경로")
    args = parser.parse_args(argv)

    date_kst = args.date or dt.datetime.now(SEOUL).strftime("%Y%m%d")
    evidence = collect_evidence(
        date_kst, watchlist_db=args.watchlist_db, telegram_db=args.telegram_db
    )
    if args.grade:
        evidence = grade_evidence(evidence, model=args.model, repeat=args.repeat)
        saved = save_causes(args.watchlist_db, evidence, model=args.model)
        print(f"saved rows: {saved}", file=sys.stderr)

    text = json.dumps(evidence, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"saved: {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
