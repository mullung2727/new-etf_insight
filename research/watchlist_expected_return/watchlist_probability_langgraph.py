"""뉴스·텔레그램·15시 시세로 D+1 시가 상승가능성 점수를 생성한다."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import email.utils
import json
import sqlite3
import statistics
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, TypedDict
from zoneinfo import ZoneInfo

import duckdb
import requests
from langgraph.graph import END, StateGraph


ROOT = Path(__file__).resolve().parents[2]
ETL_DIR = ROOT / "etl"
SRC_DIR = ETL_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from new_etf_insight.llm import generate_json  # noqa: E402


DEFAULT_WATCHLIST_DB = ETL_DIR / "db" / "watchlist.sqlite3"
DEFAULT_TELEGRAM_DB = ETL_DIR / "db" / "telegram_public.sqlite3"
DEFAULT_KRX_DB = ETL_DIR / "db" / "krx_ohlcv.duckdb"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results" / "shadow_probability"
DEFAULT_REPORTS_DIR = ROOT.parents[1] / "reports"
SCHEMA_PATH = Path(__file__).resolve().with_name("watchlist_scoring_schema.json")
THEME_DICT_PATH = Path(__file__).resolve().with_name("theme_dictionary.json")
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "watchlist_probability.md"
LEGACY_CATALYST_PROMPT_VERSION = "catalyst-survival-v3"
CATALYST_PROMPT_VERSION = "catalyst-theme-v5"
THEME_PROMPT_EFFECTIVE_DATE = "20260828"
V4_CATALYST_PROMPT_VERSION = "catalyst-survival-v4"
THEME_NO_CATALYST = "재료없음"
THEME_NOT_IN_DICT = "사전에없음"
# 프롬프트가 재료 부재 시 쓰게 한 category_raw 값. 테마 축 값과 표기가 다르다.
CATALYST_NO_MATERIAL = "재료 없음"
# 순위가 아니라 점수로 재판단을 건다. {A:60, 사전에없음:40}은 2위여도 명확하고
# {A:34, B:33, 사전에없음:33}은 3위여도 애매하다.
THEME_ESCALATION_UNKNOWN_SCORE = 30
THEME_ESCALATION_MARGIN = 15
PRICED_IN_POLICY_EFFECTIVE_DATE = "20260805"
LEGACY_PRICED_IN_POLICY = """| 선반영 | 새 재료이며 반영 증거 없음 | 0 |
|  | 일부 반복 보도 또는 사전 기대 | 5 |
|  | 전일 급등·테마 확산 등 상당 부분 반영 | 12 |
|  | 연속 급등·상한가·널리 알려진 재료 | 20 |"""
CURRENT_PRICED_IN_POLICY = """| 선반영 | 없음 (`none`) — 새 재료이며 사전 반영 증거 없음 | 0 |
|  | 낮음 (`low`) — 일부 반복 보도 또는 사전 기대 | 3 |
|  | 중간 (`medium`) — 전일 급등·테마 확산 등 상당 부분 반영 | 7 |
|  | 높음 (`high`) — 연속 급등·상한가·널리 알려진 재료 | 12 |

- `priced_in_level`과 `priced_in_penalty`는 `none=0`, `low=3`, `medium=7`, `high=12`, `unknown=0`으로 반드시 고정한다.
- 단순 당일 상승률·거래량 증가·최근 5거래일 상승만으로 선반영 감점을 주지 마라. 재료의 반복 노출이나 사전 기대가 함께 확인돼야 한다.
- 같은 가격 움직임을 선반영과 소진에 중복 사용하지 마라. 선반영은 재료의 사전 노출, 소진은 고점 이탈과 모멘텀 둔화를 중심으로 판단한다."""
CURRENT_PRICED_IN_PENALTY = {"unknown": 0, "none": 0, "low": 3, "medium": 7, "high": 12}
SEOUL = ZoneInfo("Asia/Seoul")
SESSION_RANK = {"morning": 0, "close": 1, "evening": 2}


class State(TypedDict):
    date: str
    watchlist_db: str
    telegram_db: str
    krx_db: str
    candidates: list[dict]
    news_by_ticker: dict[str, list[dict]]
    telegram_by_ticker: dict[str, list[dict]]
    scores: list[dict]
    warnings: list[str]


def _as_of(date: str) -> dt.datetime:
    return dt.datetime.strptime(date + "150000", "%Y%m%d%H%M%S").replace(tzinfo=SEOUL)


def load_candidates(state: State) -> State:
    con = sqlite3.connect(state["watchlist_db"])
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        has_snapshots = con.execute("""
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='watchlist_market_snapshots'
        """).fetchone() is not None
        snapshot_columns = """
            , m.snapshot_at, m.current_price AS snapshot_current_price,
              m.open_price AS snapshot_open_price, m.high_price AS snapshot_high_price,
              m.volume AS snapshot_volume, m.change_rate AS snapshot_change_rate,
              m.source AS snapshot_source
        """ if has_snapshots else """
            , NULL AS snapshot_at, NULL AS snapshot_current_price,
              NULL AS snapshot_open_price, NULL AS snapshot_high_price,
              NULL AS snapshot_volume, NULL AS snapshot_change_rate,
              NULL AS snapshot_source
        """
        snapshot_join = """
            LEFT JOIN watchlist_market_snapshots m
              ON m.date=w.date AND m.ticker=w.stock_code
        """ if has_snapshots else ""
        rows = con.execute(f"""
            SELECT w.date, w.stock_code AS ticker,
                   COALESCE(s.name, r.name, w.stock_code) AS name,
                   s.score AS old_score, s.ratio, s.today_volume, s.avg5_volume,
                   s.trading_value, s.close, s.category,
                   s.evidence_board, s.evidence_news, s.evidence_web,
                   r.rank AS intraday_rank
                   {snapshot_columns}
            FROM watchlist w
            LEFT JOIN llm_scores s ON s.date=w.date AND s.ticker=w.stock_code
            LEFT JOIN intraday_ranking r ON r.date=w.date AND r.ticker=w.stock_code
            {snapshot_join}
            WHERE w.date=?
            ORDER BY w.stock_code
        """, (state["date"],)).fetchall()
    finally:
        con.close()

    previous_caps = {}
    previous_avg5_volumes = {}
    previous_5d_closes = {}
    if rows:
        tickers = [row["ticker"] for row in rows]
        marks = ",".join("?" for _ in tickers)
        with duckdb.connect(state["krx_db"], read_only=True) as con:
            caps = con.execute(f"""
                SELECT o.ticker, o.market_cap
                FROM ohlcv o
                JOIN (
                    SELECT ticker, MAX(date) AS date FROM ohlcv
                    WHERE date < ? AND ticker IN ({marks}) GROUP BY ticker
                ) latest ON latest.ticker=o.ticker AND latest.date=o.date
            """, [state["date"], *tickers]).fetchall()
            previous_caps = {ticker: cap for ticker, cap in caps}
            averages = con.execute(f"""
                SELECT ticker, AVG(volume), MAX(CASE WHEN rn=5 THEN close END)
                FROM (
                    SELECT ticker, volume, close,
                           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                    FROM ohlcv
                    WHERE date < ? AND ticker IN ({marks})
                ) recent
                WHERE rn <= 5
                GROUP BY ticker
            """, [state["date"], *tickers]).fetchall()
            previous_avg5_volumes = {ticker: avg_volume for ticker, avg_volume, _ in averages}
            previous_5d_closes = {ticker: close for ticker, _, close in averages}

    candidates = []
    for row in rows:
        item = dict(row)
        item["market_cap_previous_day"] = previous_caps.get(item["ticker"])
        item["previous_5d_close"] = previous_5d_closes.get(item["ticker"])
        item["avg5_volume"] = previous_avg5_volumes.get(item["ticker"]) or item.get("avg5_volume")
        item["today_volume"] = item.get("snapshot_volume") or item.get("today_volume")
        item["close"] = item.get("snapshot_current_price") or item.get("close")
        if item.get("today_volume") and item.get("avg5_volume"):
            item["ratio"] = item["today_volume"] / item["avg5_volume"]
        if item.get("today_volume") and item.get("close"):
            item["trading_value"] = item["today_volume"] * item["close"]
        candidates.append(item)
    return {**state, "candidates": candidates}


def fetch_historical_news(name: str, ticker: str, as_of: dt.datetime, limit: int = 8) -> list[dict]:
    lower = (as_of.date() - dt.timedelta(days=3)).isoformat()
    upper = (as_of.date() + dt.timedelta(days=1)).isoformat()
    query = f'"{name}" {ticker} 주식 after:{lower} before:{upper}'
    response = requests.get(
        "https://news.google.com/rss/search",
        params={"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    output = []
    for node in root.findall(".//item"):
        title = " ".join((node.findtext("title") or "").split())
        link = " ".join((node.findtext("link") or "").split())
        published_raw = node.findtext("pubDate") or ""
        try:
            published = email.utils.parsedate_to_datetime(published_raw).astimezone(SEOUL)
        except (TypeError, ValueError):
            continue
        if title and published <= as_of:
            output.append({"title": title, "link": link, "published_at": published.isoformat()})
        if len(output) >= limit:
            break
    return output


def collect_news(state: State) -> State:
    news = {}
    warnings = list(state["warnings"])
    as_of = _as_of(state["date"])
    is_live_date = state["date"] == dt.datetime.now(SEOUL).strftime("%Y%m%d")
    for candidate in state["candidates"]:
        if not is_live_date:
            news[candidate["ticker"]] = []
            warnings.append(f"historical_live_news_excluded:{candidate['ticker']}")
            continue
        try:
            news[candidate["ticker"]] = fetch_historical_news(
                candidate["name"], candidate["ticker"], as_of
            )
        except Exception as exc:  # 네트워크 실패는 종목 단위로 격리
            news[candidate["ticker"]] = []
            warnings.append(f"news_fetch_failed:{candidate['ticker']}:{type(exc).__name__}")
    return {**state, "news_by_ticker": news, "warnings": warnings}


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


def load_telegram(state: State) -> State:
    output: dict[str, list[dict]] = {candidate["ticker"]: [] for candidate in state["candidates"]}
    if not output:
        return {**state, "telegram_by_ticker": output}
    target = dt.datetime.strptime(state["date"], "%Y%m%d").date()
    as_of = _as_of(state["date"])
    lower = (target - dt.timedelta(days=7)).isoformat()
    target_dash = target.isoformat()
    marks = ",".join("?" for _ in output)
    con = sqlite3.connect(state["telegram_db"])
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute(f"""
            SELECT date_kst, session, ticker, mention_channels, source_post_refs,
                   discovery_reason, analysis, created_at, updated_at
            FROM telegram_stock_insights
            WHERE date_kst BETWEEN ? AND ? AND ticker IN ({marks})
            ORDER BY date_kst, CASE session WHEN 'morning' THEN 0 WHEN 'close' THEN 1 ELSE 2 END
        """, [lower, target_dash, *output.keys()]).fetchall()
    finally:
        con.close()
    for row in rows:
        created_at = _parse_point_in_time(row["created_at"])
        updated_at = _parse_point_in_time(row["updated_at"])
        if not created_at or not updated_at or created_at > as_of or updated_at > as_of:
            continue
        if row["date_kst"] == target_dash and row["session"] != "morning":
            continue
        analysis = json.loads(row["analysis"]) if row["analysis"] else None
        output[row["ticker"]].append({
            "date": row["date_kst"],
            "session": row["session"],
            "created_at": created_at.isoformat(timespec="seconds"),
            "updated_at": updated_at.isoformat(timespec="seconds"),
            "channels": json.loads(row["mention_channels"] or "[]"),
            "post_refs": json.loads(row["source_post_refs"] or "[]"),
            "discovery_reason": row["discovery_reason"],
            "analysis": analysis,
        })
    return {**state, "telegram_by_ticker": output}


def build_market_snapshot(candidate: dict, date: str) -> dict:
    raw_time = candidate.get("snapshot_at")
    try:
        observed_at = dt.datetime.fromisoformat(raw_time).astimezone(SEOUL) if raw_time else None
    except ValueError:
        observed_at = None
    valid_time = bool(
        observed_at
        and observed_at.strftime("%Y%m%d") == date
        and observed_at.hour == 15
        and 0 <= observed_at.minute <= 2
    )
    current = candidate.get("snapshot_current_price")
    open_price = candidate.get("snapshot_open_price")
    high = candidate.get("snapshot_high_price")
    volume = candidate.get("snapshot_volume")
    if not valid_time or not all(value and value > 0 for value in (current, open_price, high, volume)):
        return {
            "available": False,
            "reason": "유효한 과거 D일 15:00 ka10001 시세 스냅샷 없음",
            "market_cap_previous_day": candidate.get("market_cap_previous_day"),
        }
    avg5 = candidate.get("avg5_volume")
    previous_5d_close = candidate.get("previous_5d_close")
    return {
        "available": True,
        "snapshot_at": observed_at.isoformat(timespec="seconds"),
        "source": candidate.get("snapshot_source"),
        "current_price": current,
        "open_price": open_price,
        "high_price": high,
        "volume": volume,
        "change_rate_pct": candidate.get("snapshot_change_rate"),
        "rise_from_open_pct": round((current / open_price - 1) * 100, 4),
        "pullback_from_high_pct": round((current / high - 1) * 100, 4),
        "avg5_volume": avg5,
        "volume_ratio_vs_avg5": round(volume / avg5, 4) if avg5 else None,
        "return_5d_pct": round((current / previous_5d_close - 1) * 100, 4)
        if previous_5d_close else None,
        "market_cap_previous_day": candidate.get("market_cap_previous_day"),
    }


def build_score_input(state: State, candidate: dict) -> dict:
    return {
        "as_of": _as_of(state["date"]).isoformat(),
        "target": "D+1_OPEN_ABOVE_D_CLOSE",
        "stock": {"ticker": candidate["ticker"], "name": candidate["name"]},
        "market_snapshot": build_market_snapshot(candidate, state["date"]),
        "excluded_untimed_legacy_evidence": bool(
            candidate.get("evidence_board") or candidate.get("evidence_news")
        ),
        "collected_news": state["news_by_ticker"].get(candidate["ticker"], []),
        "telegram_history": state["telegram_by_ticker"].get(candidate["ticker"], []),
    }


def prompt_version_for_date(date: str) -> str:
    compact_date = date.replace("-", "")
    if compact_date < PRICED_IN_POLICY_EFFECTIVE_DATE:
        return LEGACY_CATALYST_PROMPT_VERSION
    if compact_date < THEME_PROMPT_EFFECTIVE_DATE:
        return V4_CATALYST_PROMPT_VERSION
    return CATALYST_PROMPT_VERSION


def make_prompt(state: State, candidate: dict, theme_dict: dict | None = None) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        date=state["date"],
        priced_in_policy=(
            CURRENT_PRICED_IN_POLICY
            if state["date"].replace("-", "") >= PRICED_IN_POLICY_EFFECTIVE_DATE
            else LEGACY_PRICED_IN_POLICY
        ),
        theme_dictionary=render_theme_dictionary(theme_dict or load_theme_dictionary()),
        input_json=json.dumps(build_score_input(state, candidate), ensure_ascii=False, indent=2),
    )


ScoreFn = Callable[[str], str]


def calculate_probability_score(components: dict) -> int:
    positive = (
        components["catalyst_strength"]
        + components["freshness"]
        + components["confirmation"]
    )
    negative = (
        components["negative_event_risk"]
        + components["negative_trend_penalty"]
        + components["priced_in_penalty"]
        + components["exhaustion_penalty"]
    )
    return max(5, min(95, 50 + positive - negative))


def apply_priced_in_policy(date: str, components: dict) -> None:
    if date.replace("-", "") < PRICED_IN_POLICY_EFFECTIVE_DATE:
        return
    level = components["priced_in_level"]
    if level not in CURRENT_PRICED_IN_PENALTY:
        raise ValueError(f"invalid priced_in_level for current policy: {level}")
    components["priced_in_penalty"] = CURRENT_PRICED_IN_PENALTY[level]


def calculate_negative_trend_penalty(return_5d_pct: float | None) -> int:
    if return_5d_pct is None or return_5d_pct >= -3:
        return 0
    if return_5d_pct >= -7:
        return 3
    if return_5d_pct >= -12:
        return 6
    if return_5d_pct >= -20:
        return 10
    return 15


_CATALYST_FIELDS = {
    "label", "description", "category_raw", "status", "expected_duration",
    "alive_score", "reason", "invalidation", "evidence_refs",
}
_CATALYST_STATUSES = {"alive", "uncertain", "exhausted"}
_CATALYST_DURATIONS = {
    "intraday", "two_to_five_trading_days", "one_week_or_more", "unknown",
}


def load_theme_dictionary(path: Path | None = None) -> dict:
    return json.loads((path or THEME_DICT_PATH).read_text(encoding="utf-8"))


def theme_axis_names(theme_dict: dict) -> dict[str, list[str]]:
    """축별 배분 대상 이름. 산업·사건 값 + 그 축에 허용된 special."""
    return {
        axis: [item["name"] for item in theme_dict[key]]
        + [item["name"] for item in theme_dict["special"] if axis in item["axes"]]
        for axis, key in (("sector", "theme_sector"), ("event", "theme_event"))
    }


def theme_terminal_names(theme_dict: dict) -> set[str]:
    """1위로 나와도 재판단하지 않는 값. 사전 결함이 아니라 정상 종착점이다."""
    return {
        item["name"] for item in theme_dict["special"]
        if item["name"] != THEME_NOT_IN_DICT
    }


def render_theme_dictionary(theme_dict: dict) -> str:
    """프롬프트에 주입할 사전 본문. enum 이름과 소속 판단 근거를 함께 준다."""
    lines = [f"사전 버전: {theme_dict['version']}", ""]
    for axis, key in (("sector", "theme_sector"), ("event", "theme_event")):
        lines.append(f"#### {axis}")
        for item in theme_dict[key]:
            lines.append(f"- `{item['name']}` — 예: {', '.join(item['members'][:12])}")
        lines.append("")
    lines.append("#### 특수값")
    for item in theme_dict["special"]:
        axes = " / ".join(item["axes"])
        lines.append(f"- `{item['name']}` ({axes} 축) — {item['description']}")
    lines.append("")
    lines.append("#### 테마가 아니므로 쓰지 말 것")
    for axis in theme_dict["excluded_axes"]:
        lines.append(f"- {axis['axis']}: {axis['reason']}")
    return "\n".join(lines)


def build_scoring_schema(theme_dict: dict) -> dict:
    """사전 이름을 name enum으로 주입한 출력 스키마.

    사전은 개정되므로 스키마 파일에 enum을 박지 않는다. 호출 직전에 주입해
    codex --output-schema 의 생성 시점 강제와 사전 단일 원천을 동시에 지킨다.
    """
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    base = schema["$defs"]["theme_score"]
    for axis, names in theme_axis_names(theme_dict).items():
        item = copy.deepcopy(base)
        item["properties"]["name"] = {"type": "string", "enum": names}
        schema["properties"]["theme_scores"]["properties"][axis]["items"] = item
    return schema


def normalize_theme_scores(result: dict) -> None:
    """축별 합계를 정확히 100으로 재계산한다.

    LLM 산술은 믿지 않는다(사전 클러스터링에서 합계 오차 실측). 최대잔여법으로
    정수 합 100을 보장하고, 원래 비율은 유지한다.
    """
    for axis, items in result["theme_scores"].items():
        if not items:
            raise ValueError(f"theme_scores.{axis} must not be empty")
        total = sum(item["score"] for item in items)
        if total <= 0:
            raise ValueError(f"theme_scores.{axis} total must be positive")
        exact = [item["score"] * 100 / total for item in items]
        floors = [int(value) for value in exact]
        order = sorted(range(len(items)), key=lambda i: floors[i] - exact[i])
        for index in order[: 100 - sum(floors)]:
            floors[index] += 1
        for item, score in zip(items, floors):
            item["score"] = score


def validate_theme_scores(result: dict, theme_dict: dict) -> None:
    """사전 대조·중복·특수값 정합성. codex 스키마 강제를 로컬에서 다시 확인한다."""
    scores = result.get("theme_scores")
    if not isinstance(scores, dict) or set(scores) != {"sector", "event"}:
        raise ValueError("theme_scores must have sector and event")
    allowed = theme_axis_names(theme_dict)
    tops = {}
    for axis, items in scores.items():
        if not isinstance(items, list):
            raise ValueError(f"theme_scores.{axis} must be an array")
        names = [item["name"] for item in items]
        if len(names) != len(set(names)):
            raise ValueError(f"theme_scores.{axis} has duplicate names")
        unknown = [name for name in names if name not in allowed[axis]]
        if unknown:
            raise ValueError(f"theme_scores.{axis} name is not in dictionary: {unknown}")
        if sum(item["score"] for item in items) != 100:
            raise ValueError(f"theme_scores.{axis} must sum to 100")
        tops[axis] = max(items, key=lambda item: item["score"])["name"]

    if result["theme_event_direction"] not in {"positive", "negative", "neutral"}:
        raise ValueError("theme_event_direction is invalid")
    if tops["event"] == THEME_NO_CATALYST and result["theme_event_direction"] != "neutral":
        raise ValueError("theme_event_direction must be neutral when event top is 재료없음")

    not_in_dict = max(
        (item["score"] for items in scores.values() for item in items
         if item["name"] == THEME_NOT_IN_DICT),
        default=0,
    )
    candidate = result["new_theme_candidate"]
    if not_in_dict > 0 and not (isinstance(candidate, str) and candidate.strip()):
        raise ValueError("new_theme_candidate is required when 사전에없음 scored")
    if not_in_dict == 0 and candidate is not None:
        raise ValueError("new_theme_candidate must be null when 사전에없음 is zero")


def validate_theme_catalyst_consistency(theme_scores: dict, primary_catalyst: dict) -> None:
    """재료 판정과 테마 배분의 모순을 양방향으로 막는다.

    재판단은 테마 배분이 애매해서 걸린 것이지 재료 판정이 틀려서가 아니다. 같은
    입력에 새 정보가 없는데 재료 유무를 뒤집으면 여기서 거부된다.
    """
    no_material = primary_catalyst["category_raw"].strip() == CATALYST_NO_MATERIAL
    theme_says_none = all(
        max(items, key=lambda item: item["score"])["name"] == THEME_NO_CATALYST
        for items in theme_scores.values()
    )
    if no_material and not theme_says_none:
        raise ValueError("category_raw is 재료 없음 but theme top is not 재료없음")
    if theme_says_none and not no_material:
        raise ValueError("theme top is 재료없음 but a catalyst was identified")


def theme_escalation_reason(result: dict, theme_dict: dict | None = None) -> str | None:
    """상위 모델 재판단이 필요한 축과 사유. 필요 없으면 None.

    `재료없음`·`산업무관`이 1위인 축은 정상 종착점이라 재판단하지 않는다. 실측상
    `재료 없음`만 34%라, 걸러내지 않으면 재판단이 그 표본에서 헛돈다.
    """
    terminal = theme_terminal_names(theme_dict or load_theme_dictionary())
    for axis, items in result["theme_scores"].items():
        ranked = sorted(items, key=lambda item: -item["score"])
        if ranked[0]["name"] in terminal:
            continue
        unknown = next(
            (item["score"] for item in items if item["name"] == THEME_NOT_IN_DICT), 0
        )
        if unknown >= THEME_ESCALATION_UNKNOWN_SCORE:
            return f"{axis}: 사전에없음 {unknown}점"
        margin = ranked[0]["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0)
        if margin < THEME_ESCALATION_MARGIN:
            return f"{axis}: 1위-2위 격차 {margin}점"
    return None


def make_escalation_prompt(
    state: State, candidate: dict, first: dict, reason: str, theme_dict: dict
) -> str:
    """1차 판정과 그 한계를 배경으로 붙인 재판단 프롬프트."""
    primary = first["primary_catalyst"]
    fixed = {
        "label": primary["label"],
        "description": primary["description"],
        "category_raw": primary["category_raw"],
        "status": primary["status"],
    }
    no_material = primary["category_raw"].strip() == CATALYST_NO_MATERIAL
    background = "\n".join([
        "",
        "## 재판단 배경",
        "",
        "1차 판정에서 **테마 배분만** 아래 사유로 확정되지 않았다.",
        "",
        f"- 재판단 사유: {reason}",
        f"- 1차 테마 배분: {json.dumps(first['theme_scores'], ensure_ascii=False)}",
        "",
        "### 확정 사실 - 다시 판단하지 마라",
        "",
        "1차가 판정한 주재료는 확정이다. 재판단은 새 정보 없이 같은 입력을 다시 보는 것이라",
        "재료 유무를 뒤집을 근거가 없다.",
        "",
        "```json",
        json.dumps(fixed, ensure_ascii=False, indent=2),
        "```",
        "",
        "### 다시 판단할 것",
        "",
        "- `theme_scores`의 두 축 배분만 다시 정하라. 1차 배분에 끌려가지 마라.",
        "- 위 주재료가 어느 판에 속하고 어떤 종류의 사건인지에 집중하라.",
        (
            "- 위 주재료가 `재료 없음`이므로 두 축 모두 `재료없음`이 1위여야 한다."
            if no_material else
            "- 위 주재료가 확인된 재료이므로 `재료없음`을 1위로 두면 안 된다."
        ),
        "- 사전의 어느 값에도 맞지 않으면 `사전에없음`에 점수를 주고 `new_theme_candidate`를 채워라.",
        "- 나머지 필드는 1차와 같은 기준으로 채우되 채점에 쓰이지 않는다.",
    ])
    return make_prompt(state, candidate, theme_dict) + "\n" + background


def escalate_theme_scores(
    state: State, escalate_fn: ScoreFn | None = None, model: str | None = None
) -> State:
    """재판단이 걸린 종목만 상위 모델로 1회 다시 판정한다.

    실패하면 1차 결과를 그대로 둔다. 재판단은 1회뿐이라 여기서 다시 걸지 않는다.
    """
    targets = [score for score in state["scores"] if score.get("theme_escalation_reason")]
    if not targets:
        return state

    theme_dict = load_theme_dictionary()
    candidates = {candidate["ticker"]: candidate for candidate in state["candidates"]}
    warnings = list(state["warnings"])

    with tempfile.TemporaryDirectory() as tmpdir:
        schema_path = Path(tmpdir) / "watchlist_scoring_schema.json"
        schema_path.write_text(
            json.dumps(build_scoring_schema(theme_dict), ensure_ascii=False), encoding="utf-8"
        )
        generator = escalate_fn or (
            lambda prompt: generate_json(
                prompt, output_schema_path=schema_path, search=False, model=model
            )
        )
        for score in targets:
            ticker = score["ticker"]
            try:
                prompt = make_escalation_prompt(
                    state, candidates[ticker], score, score["theme_escalation_reason"], theme_dict
                )
                result = json.loads(generator(prompt))
                if result["ticker"] != ticker:
                    raise ValueError("ticker mismatch")
                normalize_theme_scores(result)
                validate_theme_scores(result, theme_dict)
                # 1차 재료 판정이 기준이다. 재판단이 낸 catalyst는 쓰지 않는다.
                validate_theme_catalyst_consistency(
                    result["theme_scores"], score["primary_catalyst"]
                )
            except Exception as exc:  # noqa: BLE001 — 재판단 실패는 1차 결과를 유지한다
                warnings.append(f"theme_escalation_failed:{ticker}:{type(exc).__name__}")
                continue
            score["theme_scores"] = result["theme_scores"]
            score["theme_event_direction"] = result["theme_event_direction"]
            score["new_theme_candidate"] = result["new_theme_candidate"]
            score["theme_escalated"] = True
            score["theme_escalation_model"] = model
    return {**state, "warnings": warnings}


def route_after_scoring(state: State) -> str:
    if any(score.get("theme_escalation_reason") for score in state["scores"]):
        return "escalate"
    return "end"


def _validate_catalyst(catalyst: object, field: str) -> None:
    if not isinstance(catalyst, dict):
        raise ValueError(f"{field} must be an object")
    if set(catalyst) != _CATALYST_FIELDS:
        raise ValueError(f"{field} fields mismatch")
    for key in ("label", "description", "category_raw", "reason", "invalidation"):
        if not isinstance(catalyst[key], str) or not catalyst[key].strip():
            raise ValueError(f"{field}.{key} must be a non-empty string")
    if catalyst["status"] not in _CATALYST_STATUSES:
        raise ValueError(f"{field}.status is invalid")
    if catalyst["expected_duration"] not in _CATALYST_DURATIONS:
        raise ValueError(f"{field}.expected_duration is invalid")
    score = catalyst["alive_score"]
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        raise ValueError(f"{field}.alive_score must be an integer from 1 to 5")
    refs = catalyst["evidence_refs"]
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise ValueError(f"{field}.evidence_refs must be a string array")


def validate_catalyst_assessment(result: dict) -> None:
    """주·보조재료 구조와 단순 1~5점 계약을 검증한다."""
    if "primary_catalyst" not in result or "secondary_catalysts" not in result:
        raise ValueError("catalyst assessment is required")
    _validate_catalyst(result["primary_catalyst"], "primary_catalyst")
    secondary = result["secondary_catalysts"]
    if not isinstance(secondary, list):
        raise ValueError("secondary_catalysts must be an array")
    for index, catalyst in enumerate(secondary):
        _validate_catalyst(catalyst, f"secondary_catalysts[{index}]")


def validate_catalyst_evidence_refs(
    result: dict, news_rows: list[dict], telegram_rows: list[dict]
) -> None:
    allowed = {
        item["link"] for item in news_rows if isinstance(item.get("link"), str)
    }
    allowed.update(
        ref for item in telegram_rows for ref in item.get("post_refs", [])
        if isinstance(ref, str)
    )
    catalysts = [result["primary_catalyst"], *result["secondary_catalysts"]]
    unknown = [
        ref for catalyst in catalysts for ref in catalyst["evidence_refs"]
        if ref not in allowed
    ]
    if unknown:
        raise ValueError("catalyst evidence ref is not present in input")


def score_candidates(state: State, score_fn: ScoreFn | None = None) -> State:
    theme_dict = load_theme_dictionary()
    with tempfile.TemporaryDirectory() as tmpdir:
        schema_path = Path(tmpdir) / "watchlist_scoring_schema.json"
        schema_path.write_text(
            json.dumps(build_scoring_schema(theme_dict), ensure_ascii=False), encoding="utf-8"
        )
        generator = score_fn or (
            lambda prompt: generate_json(prompt, output_schema_path=schema_path, search=False)
        )
        return _score_with(state, generator, theme_dict)


def _score_with(state: State, generator: ScoreFn, theme_dict: dict) -> State:
    scores = []
    warnings = list(state["warnings"])
    for candidate in state["candidates"]:
        try:
            result = json.loads(generator(make_prompt(state, candidate, theme_dict)))
            if result["ticker"] != candidate["ticker"]:
                raise ValueError("ticker mismatch")
            news_rows = state["news_by_ticker"].get(candidate["ticker"], [])
            telegram_rows = state["telegram_by_ticker"].get(candidate["ticker"], [])
            validate_catalyst_assessment(result)
            validate_catalyst_evidence_refs(result, news_rows, telegram_rows)
            normalize_theme_scores(result)
            validate_theme_scores(result, theme_dict)
            validate_theme_catalyst_consistency(
                result["theme_scores"], result["primary_catalyst"]
            )
            result["theme_escalation_reason"] = theme_escalation_reason(result, theme_dict)
            result["theme_escalated"] = False
            result["theme_escalation_model"] = None
            result["theme_dict_version"] = theme_dict["version"]
            reported_score = result["probability_score"]
            snapshot = build_market_snapshot(candidate, state["date"])
            apply_priced_in_policy(state["date"], result["score_components"])
            result["score_components"]["negative_trend_penalty"] = (
                calculate_negative_trend_penalty(snapshot.get("return_5d_pct"))
            )
            result["probability_score"] = calculate_probability_score(result["score_components"])
            result["llm_reported_probability_score"] = reported_score
            scores.append({
                "date": state["date"],
                "as_of": _as_of(state["date"]).isoformat(),
                "old_score": candidate.get("old_score"),
                "ratio": candidate.get("ratio"),
                "today_volume": candidate.get("today_volume"),
                "avg5_volume": candidate.get("avg5_volume"),
                "trading_value": candidate.get("trading_value"),
                "close": candidate.get("snapshot_current_price") or candidate.get("close"),
                "change_rate_pct": snapshot.get("change_rate_pct"),
                "rise_from_open_pct": snapshot.get("rise_from_open_pct"),
                "pullback_from_high_pct": snapshot.get("pullback_from_high_pct"),
                "return_5d_pct": snapshot.get("return_5d_pct"),
                "telegram_rows": len(telegram_rows),
                "news_rows": len(news_rows),
                "sources": sorted({
                    *(
                        item["link"] for item in news_rows
                        if isinstance(item.get("link"), str) and item["link"].startswith("http")
                    ),
                    *(
                        ref for item in telegram_rows for ref in item.get("post_refs", [])
                        if isinstance(ref, str) and ref.startswith("http")
                    ),
                }),
                **result,
            })
        except Exception as exc:
            warnings.append(f"score_failed:{candidate['ticker']}:{type(exc).__name__}")
    return {**state, "scores": scores, "warnings": warnings}


def build_graph(
    score_fn: ScoreFn | None = None,
    escalate_fn: ScoreFn | None = None,
    escalation_model: str | None = None,
):
    graph = StateGraph(State)
    graph.add_node("load_candidates", load_candidates)
    graph.add_node("collect_news", collect_news)
    graph.add_node("load_telegram", load_telegram)
    graph.add_node("score_candidates", lambda state: score_candidates(state, score_fn))
    graph.add_node(
        "escalate_theme_scores",
        lambda state: escalate_theme_scores(state, escalate_fn, escalation_model),
    )
    graph.set_entry_point("load_candidates")
    graph.add_edge("load_candidates", "collect_news")
    graph.add_edge("collect_news", "load_telegram")
    graph.add_edge("load_telegram", "score_candidates")
    graph.add_conditional_edges(
        "score_candidates",
        route_after_scoring,
        {"escalate": "escalate_theme_scores", "end": END},
    )
    graph.add_edge("escalate_theme_scores", END)
    return graph.compile()


def run_date(
    date: str,
    watchlist_db: Path = DEFAULT_WATCHLIST_DB,
    telegram_db: Path = DEFAULT_TELEGRAM_DB,
    krx_db: Path = DEFAULT_KRX_DB,
    score_fn: ScoreFn | None = None,
    escalate_fn: ScoreFn | None = None,
    escalation_model: str | None = None,
) -> dict:
    initial: State = {
        "date": date,
        "watchlist_db": str(watchlist_db),
        "telegram_db": str(telegram_db),
        "krx_db": str(krx_db),
        "candidates": [],
        "news_by_ticker": {},
        "telegram_by_ticker": {},
        "scores": [],
        "warnings": [],
    }
    final = build_graph(score_fn, escalate_fn, escalation_model).invoke(initial)
    return {
        "date": date,
        "target": "D+1_OPEN_ABOVE_D_CLOSE",
        "candidate_count": len(final["candidates"]),
        "scored_count": len(final["scores"]),
        "scores": final["scores"],
        "warnings": final["warnings"],
    }


def compare_scores(results: list[dict]) -> dict:
    rows = [score for result in results for score in result["scores"]]
    deltas = [row["probability_score"] - row["old_score"] for row in rows if row.get("old_score") is not None]
    by_date = {}
    meaningful = 0
    for result in results:
        dated = result["scores"]
        old_order = {row["ticker"]: index for index, row in enumerate(sorted(dated, key=lambda x: x.get("old_score") or -1, reverse=True))}
        new_order = {row["ticker"]: index for index, row in enumerate(sorted(dated, key=lambda x: x["probability_score"], reverse=True))}
        comparison = []
        for row in dated:
            delta = row["probability_score"] - row["old_score"] if row.get("old_score") is not None else None
            rank_change = old_order.get(row["ticker"], 0) - new_order[row["ticker"]]
            changed = bool(delta is not None and (abs(delta) >= 10 or abs(rank_change) >= 1))
            meaningful += changed
            comparison.append({
                "ticker": row["ticker"], "name": row["name"], "old_score": row.get("old_score"),
                "new_score": row["probability_score"], "delta": delta, "rank_change": rank_change,
                "meaningfully_changed": changed, "telegram_rows": row["telegram_rows"],
            })
        by_date[result["date"]] = comparison
    return {
        "row_count": len(rows),
        "mean_absolute_delta": round(statistics.fmean(abs(value) for value in deltas), 4) if deltas else None,
        "meaningfully_changed_count": meaningful,
        "meaningfully_changed_rate": round(meaningful / len(rows), 4) if rows else None,
        "old_score_stddev": round(statistics.pstdev(row["old_score"] for row in rows if row.get("old_score") is not None), 4) if len(deltas) > 1 else None,
        "new_score_stddev": round(statistics.pstdev(row["probability_score"] for row in rows), 4) if len(rows) > 1 else None,
        "by_date": by_date,
    }


def _ranking_auc(rows: list[dict], score_key: str) -> float | None:
    positive = [
        row[score_key] for row in rows
        if row["actual_up"] and row.get(score_key) is not None
    ]
    negative = [
        row[score_key] for row in rows
        if not row["actual_up"] and row.get(score_key) is not None
    ]
    if not positive or not negative:
        return None
    wins = sum(1 if up > down else 0.5 if up == down else 0 for up in positive for down in negative)
    return round(wins / (len(positive) * len(negative)), 4)


def evaluate_available_outcomes(results: list[dict], krx_db: Path) -> dict:
    """현재 적재된 KRX 범위에서 D+1 시가 결과가 있는 점수만 평가한다."""
    evaluated = []
    with duckdb.connect(str(krx_db), read_only=True) as con:
        for result in results:
            for score in result["scores"]:
                outcome = con.execute("""
                    SELECT current.close, next_day.date, next_day.open
                    FROM ohlcv current
                    JOIN ohlcv next_day
                      ON next_day.ticker=current.ticker
                     AND next_day.date=(
                         SELECT MIN(date) FROM ohlcv
                         WHERE ticker=current.ticker AND date>current.date
                     )
                    WHERE current.date=? AND current.ticker=?
                """, [score["date"], score["ticker"]]).fetchone()
                if not outcome or not outcome[0] or outcome[2] is None:
                    continue
                close, next_date, next_open = outcome
                evaluated.append({
                    "date": score["date"],
                    "next_date": next_date,
                    "ticker": score["ticker"],
                    "name": score["name"],
                    "old_score": score.get("old_score"),
                    "new_score": score["probability_score"],
                    "d1_open_return_pct": round((next_open / close - 1) * 100, 4),
                    "actual_up": next_open > close,
                })
    brier = statistics.fmean(
        ((row["new_score"] / 100) - int(row["actual_up"])) ** 2 for row in evaluated
    ) if evaluated else None
    return {
        "scored_count": sum(len(result["scores"]) for result in results),
        "evaluated_count": len(evaluated),
        "pending_count": sum(len(result["scores"]) for result in results) - len(evaluated),
        "new_score_ranking_auc": _ranking_auc(evaluated, "new_score"),
        "old_score_ranking_auc": _ranking_auc(evaluated, "old_score"),
        "new_score_brier": round(brier, 4) if brier is not None else None,
        "new_score_accuracy_at_50": round(statistics.fmean(
            (row["new_score"] >= 50) == row["actual_up"] for row in evaluated
        ), 4) if evaluated else None,
        "rows": evaluated,
    }


def latest_watchlist_dates(db_path: Path, count: int = 3) -> list[str]:
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA query_only=ON")
        dates = [row[0] for row in con.execute(
            "SELECT DISTINCT date FROM watchlist ORDER BY date DESC LIMIT ?", (count,)
        )]
    finally:
        con.close()
    return sorted(dates)


def to_llm_score_row(score: dict) -> dict:
    components = score["score_components"]
    up_factors = "; ".join(score.get("up_factors") or []) or "확인된 상승 요인 없음"
    down_factors = "; ".join(score.get("down_factors") or []) or "확인된 하락 요인 없음"
    primary = score["primary_catalyst"]
    raw_score = (
        50
        + components["catalyst_strength"]
        + components["freshness"]
        + components["confirmation"]
        - components["negative_event_risk"]
        - components["negative_trend_penalty"]
        - components["priced_in_penalty"]
        - components["exhaustion_penalty"]
    )
    score_result = f"= {raw_score}점"
    if raw_score != score["probability_score"]:
        score_result += f" → clamp 최종 {score['probability_score']}점"

    def format_integer(value: object, suffix: str) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "데이터 없음"
        return f"{value:,.0f}{suffix}"

    def format_decimal(value: object, suffix: str, digits: int = 2) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "데이터 없음"
        return f"{value:+.{digits}f}{suffix}"

    ratio = score.get("ratio")
    ratio_text = (
        f"{ratio:.2f}배"
        if isinstance(ratio, (int, float)) and not isinstance(ratio, bool)
        else "데이터 없음"
    )
    qualitative_reason = (
        f"재료강도 {components['catalyst_strength']}점, "
        f"신선도 {components['freshness']}점, "
        f"독립확인 {components['confirmation']}점, "
        f"악재위험 {components['negative_event_risk']}점. "
        f"주재료: {primary['label']} "
        f"({primary['status']}, 생존 {primary['alive_score']}/5) - {primary['reason']} "
        f"뉴스: {score['news_summary']} 텔레그램: {score['telegram_summary']}"
    )
    quantitative_reason = (
        f"현재가 {format_integer(score.get('close'), '원')}, "
        f"등락률 {format_decimal(score.get('change_rate_pct'), '%')}, "
        f"시가 대비 {format_decimal(score.get('rise_from_open_pct'), '%')}, "
        f"고점 대비 {format_decimal(score.get('pullback_from_high_pct'), '%')}, "
        f"거래량 {format_integer(score.get('today_volume'), '주')}, "
        f"5일 평균 거래량 {format_integer(score.get('avg5_volume'), '주')}, "
        f"거래량 배율 {ratio_text}, "
        f"5일 수익률 {format_decimal(score.get('return_5d_pct'), '%')}. "
        f"정량 감점: 최근하락 {components['negative_trend_penalty']}점, "
        f"선반영 {components['priced_in_penalty']}점, "
        f"소진 {components['exhaustion_penalty']}점."
    )
    overall_reason = score["reasoning"]
    if "[종합]" in overall_reason:
        overall_reason = overall_reason.split("[종합]", 1)[1].strip()
    score_reason = (
        "[점수 산식] "
        f"50 + 재료강도 {components['catalyst_strength']} "
        f"+ 신선도 {components['freshness']} "
        f"+ 독립확인 {components['confirmation']} "
        f"- 악재위험 {components['negative_event_risk']} "
        f"- 최근하락 {components['negative_trend_penalty']} "
        f"- 선반영 {components['priced_in_penalty']} "
        f"- 소진 {components['exhaustion_penalty']} "
        f"{score_result}.\n"
        f"[정성적 근거] {qualitative_reason}\n"
        f"[정량적 근거] {quantitative_reason}\n"
        f"[종합 판단] {overall_reason}"
    )
    return {
        "date": score["date"],
        "ticker": score["ticker"],
        "name": score["name"],
        "ratio": score.get("ratio"),
        "today_volume": score.get("today_volume"),
        "avg5_volume": score.get("avg5_volume"),
        "trading_value": score.get("trading_value"),
        "close": score.get("close"),
        "score": score["probability_score"],
        "category": "D+1 시가 상승가능성",
        "reason_summary": score_reason,
        "final_opinion": (
            f"상승 요인: {up_factors} / 하락 요인: {down_factors} / "
            f"신뢰도: {score['confidence']}·근거품질: {score['evidence_quality']}"
        ),
        "evidence_board": "종토방 정성 점수는 신규 상승가능성 산정에서 제외",
        "evidence_news": f"뉴스: {score['news_summary']}",
        "evidence_web": f"텔레그램: {score['telegram_summary']}",
        "sources": score.get("sources") or [],
        "score_components": components,
        "as_of": score.get("as_of"),
        "primary_catalyst": score["primary_catalyst"],
        "secondary_catalysts": score["secondary_catalysts"],
    }


def _upsert_llm_scores(con: sqlite3.Connection, rows: list[dict]) -> int:
    values = [(
        row["date"].replace("-", ""), row["ticker"], row["name"], row.get("ratio"),
        row.get("today_volume"), row.get("avg5_volume"), row.get("trading_value"),
        row.get("close"), row["score"], row["category"], row["reason_summary"],
        row["final_opinion"], row["evidence_board"], row["evidence_news"],
        row["evidence_web"], json.dumps(row["sources"], ensure_ascii=False),
    ) for row in rows]
    if values:
        con.executemany("""
            INSERT INTO llm_scores (
                date,ticker,name,ratio,today_volume,avg5_volume,trading_value,close,
                score,category,reason_summary,final_opinion,evidence_board,
                evidence_news,evidence_web,sources
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date,ticker) DO UPDATE SET
                name=excluded.name, ratio=excluded.ratio,
                today_volume=excluded.today_volume, avg5_volume=excluded.avg5_volume,
                trading_value=excluded.trading_value, close=excluded.close,
                score=excluded.score, category=excluded.category,
                reason_summary=excluded.reason_summary,
                final_opinion=excluded.final_opinion,
                evidence_board=excluded.evidence_board,
                evidence_news=excluded.evidence_news,
                evidence_web=excluded.evidence_web, sources=excluded.sources
        """, values)
    return len(values)


def upsert_llm_scores(db_path: Path, rows: list[dict]) -> int:
    con = sqlite3.connect(db_path)
    try:
        count = _upsert_llm_scores(con, rows)
        con.commit()
        return count
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


_THEME_COLUMNS = (
    ("theme_scores_json", "TEXT"),
    ("theme_event_direction", "TEXT"),
    ("new_theme_candidate", "TEXT"),
    ("theme_dict_version", "TEXT"),
    ("theme_escalated", "INTEGER"),
    ("theme_escalation_reason", "TEXT"),
    ("theme_escalation_model", "TEXT"),
)


def _add_missing_theme_columns(con: sqlite3.Connection) -> None:
    """CREATE TABLE IF NOT EXISTS는 기존 테이블에 컬럼을 추가하지 않는다."""
    existing = {row[1] for row in con.execute("PRAGMA table_info(llm_catalyst_assessments)")}
    for name, sql_type in _THEME_COLUMNS:
        if name not in existing:
            con.execute(f"ALTER TABLE llm_catalyst_assessments ADD COLUMN {name} {sql_type}")
    con.execute("""
        CREATE TABLE IF NOT EXISTS theme_dict_migrations (
            from_version TEXT NOT NULL,
            to_version TEXT NOT NULL,
            axis TEXT NOT NULL,
            old_value TEXT NOT NULL,
            new_value TEXT,
            action TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (from_version, to_version, axis, old_value)
        )
    """)


def _upsert_catalyst_assessments(
    con: sqlite3.Connection,
    scores: list[dict],
    *,
    model: str | None,
    generated_at: str,
) -> int:
    values = []
    for score in scores:
        validate_catalyst_assessment(score)
        primary = score["primary_catalyst"]
        all_catalysts = [primary, *score["secondary_catalysts"]]
        assessment = {
            "primary_catalyst": primary,
            "secondary_catalysts": score["secondary_catalysts"],
        }
        values.append((
            score["date"].replace("-", ""), score["ticker"], score["as_of"],
            primary["category_raw"], primary["status"], primary["expected_duration"],
            primary["alive_score"], max(item["alive_score"] for item in all_catalysts),
            json.dumps(assessment, ensure_ascii=False), prompt_version_for_date(score["date"]),
            model, generated_at,
            json.dumps(score["theme_scores"], ensure_ascii=False)
            if score.get("theme_scores") else None,
            score.get("theme_event_direction"), score.get("new_theme_candidate"),
            score.get("theme_dict_version"), int(bool(score.get("theme_escalated"))),
            score.get("theme_escalation_reason"), score.get("theme_escalation_model"),
        ))

    con.execute("""
        CREATE TABLE IF NOT EXISTS llm_catalyst_assessments (
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            as_of TEXT NOT NULL,
            primary_category_raw TEXT NOT NULL,
            primary_status TEXT NOT NULL,
            primary_duration TEXT NOT NULL,
            primary_alive_score INTEGER NOT NULL,
            max_alive_score INTEGER NOT NULL,
            assessment_json TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            model TEXT,
            generated_at TEXT NOT NULL,
            theme_scores_json TEXT,
            theme_event_direction TEXT,
            new_theme_candidate TEXT,
            theme_dict_version TEXT,
            theme_escalated INTEGER,
            theme_escalation_reason TEXT,
            theme_escalation_model TEXT,
            PRIMARY KEY (date, ticker)
        )
    """)
    _add_missing_theme_columns(con)
    if values:
        con.executemany("""
            INSERT INTO llm_catalyst_assessments (
                date,ticker,as_of,primary_category_raw,primary_status,
                primary_duration,primary_alive_score,max_alive_score,
                assessment_json,prompt_version,model,generated_at,
                theme_scores_json,theme_event_direction,new_theme_candidate,
                theme_dict_version,theme_escalated,theme_escalation_reason,
                theme_escalation_model
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date,ticker) DO UPDATE SET
                as_of=excluded.as_of,
                primary_category_raw=excluded.primary_category_raw,
                primary_status=excluded.primary_status,
                primary_duration=excluded.primary_duration,
                primary_alive_score=excluded.primary_alive_score,
                max_alive_score=excluded.max_alive_score,
                assessment_json=excluded.assessment_json,
                prompt_version=excluded.prompt_version,
                model=excluded.model,
                generated_at=excluded.generated_at,
                theme_scores_json=excluded.theme_scores_json,
                theme_event_direction=excluded.theme_event_direction,
                new_theme_candidate=excluded.new_theme_candidate,
                theme_dict_version=excluded.theme_dict_version,
                theme_escalated=excluded.theme_escalated,
                theme_escalation_reason=excluded.theme_escalation_reason,
                theme_escalation_model=excluded.theme_escalation_model
        """, values)
    return len(values)


def upsert_catalyst_assessments(
    db_path: Path,
    scores: list[dict],
    *,
    model: str | None = None,
    generated_at: str | None = None,
) -> int:
    """기존 llm_scores와 분리해 주·보조재료 원본을 멱등 저장한다."""
    generated = generated_at or dt.datetime.now(SEOUL).isoformat(timespec="seconds")
    con = sqlite3.connect(db_path)
    try:
        count = _upsert_catalyst_assessments(
            con, scores, model=model, generated_at=generated
        )
        con.commit()
        return count
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def persist_scoring_results(
    db_path: Path,
    results: list[dict],
    *,
    model: str | None = None,
) -> dict[str, int]:
    """완전한 실행 결과만 기존 점수와 신규 재료 테이블에 함께 저장한다."""
    ensure_complete_scores(results)
    scores = [score for result in results for score in result["scores"]]
    for score in scores:
        validate_catalyst_assessment(score)
    operational_rows = [to_llm_score_row(score) for score in scores]
    generated_at = dt.datetime.now(SEOUL).isoformat(timespec="seconds")
    con = sqlite3.connect(db_path)
    try:
        counts = {
            "llm_scores": _upsert_llm_scores(con, operational_rows),
            "catalyst_assessments": _upsert_catalyst_assessments(
                con, scores, model=model, generated_at=generated_at
            ),
        }
        con.commit()
        return counts
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def write_operational_report(reports_dir: Path, date: str, rows: list[dict], db_path: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    dashed = dt.datetime.strptime(date, "%Y%m%d").strftime("%Y-%m-%d")
    doc = {
        "page_title": f"ETF watchlist D+1 시가 상승가능성 리포트 {dashed}",
        "date": dashed,
        "source_data": {
            "build_watchlist": "success",
            "watchlist_db": str(db_path),
            "definition": "score is probability of D+1 open above D close",
            "catalyst_definition": "primary and secondary catalyst survival assessment",
            "catalyst_prompt_version": prompt_version_for_date(date),
        },
        "items": rows,
        "sources": sorted({source for row in rows for source in row["sources"]}),
    }
    path = reports_dir / f"watchlist_research_{dashed}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ensure_complete_scores(results: list[dict]) -> None:
    incomplete = [
        f"{result['date']}:{result['scored_count']}/{result['candidate_count']}"
        for result in results
        if result["scored_count"] != result["candidate_count"]
    ]
    if incomplete:
        raise RuntimeError("incomplete probability scoring: " + ", ".join(incomplete))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="D+1 시가 상승가능성 LangGraph")
    parser.add_argument("--dates", nargs="*", help="YYYYMMDD; 기본 최근 3거래일")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--telegram-db", type=Path, default=DEFAULT_TELEGRAM_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-db", action="store_true", help="llm_scores와 catalyst shadow를 갱신")
    parser.add_argument("--model-label", default="codex", help="catalyst 재현용 provider/model 식별자")
    parser.add_argument(
        "--escalation-model",
        default=None,
        help="테마 배분이 애매할 때 재판단에 쓸 모델. 미지정이면 1차와 같은 모델",
    )
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args(argv)
    dates = args.dates or latest_watchlist_dates(args.watchlist_db)
    results = [
        run_date(
            date, args.watchlist_db, args.telegram_db, args.krx_db,
            escalation_model=args.escalation_model,
        )
        for date in dates
    ]
    write_counts = {"llm_scores": 0, "catalyst_assessments": 0}
    if args.write_db:
        write_counts = persist_scoring_results(
            args.watchlist_db, results, model=args.model_label
        )
    operational_rows = [to_llm_score_row(score) for result in results for score in result["scores"]]
    report_paths = []
    if args.write_db:
        for result in results:
            rows = [to_llm_score_row(score) for score in result["scores"]]
            # 파일명만 남긴다. 절대경로를 커밋되는 산출물에 박으면 OS 계정명이 같이 들어가고
            # 다른 머신에서는 가리키는 곳이 없다. 디렉터리는 --reports-dir 이 정한다.
            report_paths.append(write_operational_report(
                args.reports_dir, result["date"], rows, args.watchlist_db
            ).name)
    output = {
        "generated_at": dt.datetime.now(SEOUL).isoformat(timespec="seconds"),
        "db_write": args.write_db,
        "db_rows_written": write_counts["llm_scores"],
        "catalyst_rows_written": write_counts["catalyst_assessments"],
        "operational_reports": report_paths,
        "results": results,
        "comparison": compare_scores(results),
        "outcome_evaluation": evaluate_available_outcomes(results, args.krx_db),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "recent_3day_probability_scores.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
