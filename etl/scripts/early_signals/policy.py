"""가격 문맥과 행동 결정 — 등급·행동은 코드가 정한다 (PLAN §9, §10).

LLM 이 review_buy 나 high 를 제안해도 이 함수들을 우회할 수 없다(§13.3).
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
KRX_DB = ROOT / "etl" / "db" / "krx_ohlcv.duckdb"

REQUIRED_SESSIONS = 61          # 60일 수익률에 t 와 t-60 종가가 필요하다(§9.1)
LOOKBACK_HIGH = 120             # 보조 지표
MIN_TURNOVER = 1_000_000_000    # 20일 평균 거래대금 하한 10억원(§10.3-3)
GRADES = ("high", "medium", "low", "unknown")


SPARSE_SESSION_RATIO = 0.5      # 중앙값 대비 이 비율 미만이면 수집 결손일로 본다


def load_market_sessions(cutoff_date: str, con=None) -> list[str]:
    """시장 전체 거래일 순번. 종목의 최근 N행을 시장 N거래일로 보지 않는다(§9.1)."""
    return market_calendar(cutoff_date, con)["sessions"]


def market_calendar(cutoff_date: str, con=None) -> dict[str, Any]:
    """거래일 순번과 수집 결손일을 함께 돌려준다(§9.1 시장 자료 누락 검사).

    날짜별 종목 수가 중앙값의 절반도 안 되면 그날은 시장 휴장이 아니라 **수집이 덜 된
    날**이다. 그대로 두면 그날 행이 없는 종목이 전부 history_gap 으로 떨어져 승격이
    막힌다. 거래일에서 빼되 어느 날짜를 왜 뺐는지 기록한다.
    """
    own = con is None
    if own:
        import duckdb

        con = duckdb.connect(str(KRX_DB), read_only=True)
    try:
        rows = con.execute(
            "SELECT date, count(*) FROM ohlcv WHERE date <= ? GROUP BY date ORDER BY date",
            [cutoff_date],
        ).fetchall()
    finally:
        if own:
            con.close()
    counts = sorted(int(row[1]) for row in rows)
    if not counts:
        return {"sessions": [], "sparse_sessions": [], "median_listings": 0}
    median = counts[len(counts) // 2]
    floor = median * SPARSE_SESSION_RATIO
    sessions, sparse = [], []
    for date, listings in rows:
        (sessions if listings >= floor else sparse).append(str(date))
    return {"sessions": sessions,
            "sparse_sessions": [{"date": d} for d in sparse],
            "median_listings": median}


def load_daily(ticker: str, dates: list[str], con=None) -> dict[str, dict[str, Any]]:
    own = con is None
    if own:
        import duckdb

        con = duckdb.connect(str(KRX_DB), read_only=True)
    try:
        marks = ",".join("?" * len(dates))
        rows = con.execute(
            f"SELECT date, open, high, low, close, volume, trading_value, market_cap, list_shrs"
            f" FROM ohlcv WHERE ticker=? AND date IN ({marks})", [ticker, *dates]
        ).fetchall()
    finally:
        if own:
            con.close()
    keys = ("date", "open", "high", "low", "close", "volume", "trading_value",
            "market_cap", "list_shrs")
    return {str(row[0]): dict(zip(keys, row)) for row in rows}


def validate_price_history(
    ticker: str, sessions: list[str], daily: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """승격에 필요한 연속 61 거래일 무결성을 검사한다(§9.1).

    거래대금 0만으로 거래정지를 단정하지 않는다. 종가·고가·저가가 양수여야 한다.
    """
    window = sessions[-REQUIRED_SESSIONS:]
    issues: list[str] = []
    if len(window) < REQUIRED_SESSIONS:
        return {"ok": False, "reason_codes": ["market_calendar_short"], "window": window}
    missing = [date for date in window if date not in daily]
    if missing:
        issues.append("history_gap")
    invalid = [date for date in window
               if date in daily and not _valid_bar(daily[date])]
    if invalid:
        issues.append("suspended_or_invalid_bar")
    if _corporate_action(window, daily):
        issues.append("adjustment_pending")
    return {"ok": not issues, "reason_codes": issues, "window": window}


def _valid_bar(bar: dict[str, Any]) -> bool:
    return all((bar.get(k) or 0) > 0 for k in ("close", "high", "low")) and \
        (bar.get("trading_value") or 0) >= 0


def _corporate_action(window: list[str], daily: dict[str, dict[str, Any]]) -> bool:
    """상장주식수가 밴드 밖으로 변하면 미조정 가격 점프를 의심한다(§9.2).

    자동 보정하지 않는다 — 공식 조정계수 확보 전에는 보류다.
    """
    shares = [daily[d].get("list_shrs") for d in window if d in daily and daily[d].get("list_shrs")]
    return any(not (0.67 <= later / earlier <= 1.5)
               for earlier, later in zip(shares, shares[1:]))


def compute_price_context(
    ticker: str, sessions: list[str], daily: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """5/20/60일 수익률·거래대금 배율·120일 고점을 시장 거래일 기준으로 계산한다(§9.1)."""
    validation = validate_price_history(ticker, sessions, daily)
    window = validation["window"]
    context: dict[str, Any] = {
        "price_as_of": window[-1] if window else None,
        "history_ok": validation["ok"],
        "reason_codes": list(validation["reason_codes"]),
    }
    closes = {d: daily[d]["close"] for d in window if d in daily and _valid_bar(daily[d])}
    last = window[-1] if window else None
    if not last or last not in closes:
        context["state"] = "unknown"
        context["reason_codes"].append("stale_price")
        return context

    for horizon in (5, 20, 60):
        base_date = window[-1 - horizon] if len(window) > horizon else None
        context[f"return_{horizon}d"] = (
            round(closes[last] / closes[base_date] - 1, 6)
            if base_date and base_date in closes else None)

    recent = [daily[d].get("trading_value") or 0 for d in window[-5:] if d in daily]
    prior = [daily[d].get("trading_value") or 0 for d in window[-25:-5] if d in daily]
    prior_mean = statistics.fmean(prior) if prior else 0
    context["turnover_ratio"] = (
        round(statistics.fmean(recent) / prior_mean, 4) if recent and prior_mean else None)
    context["turnover_20d"] = round(statistics.fmean(prior + recent), 0) if (prior or recent) else None
    context["close"] = closes[last]
    context["market_cap"] = daily[last].get("market_cap")

    highs = [daily[d]["high"] for d in sessions[-LOOKBACK_HIGH:]
             if d in daily and _valid_bar(daily[d])]
    if len(highs) == LOOKBACK_HIGH:
        context["drawdown_120d"] = round(closes[last] / max(highs) - 1, 6)
    else:
        context["drawdown_120d"] = None
        context["reason_codes"].append("insufficient_120d")

    context["state"] = classify_price_state(context)
    return context


def classify_price_state(context: dict[str, Any]) -> str:
    """필수값 검증 후 extended → quiet → moving 순으로 최초 일치를 적용한다(§9.1)."""
    r5, r20, ratio = context.get("return_5d"), context.get("return_20d"), context.get("turnover_ratio")
    if r5 is None or r20 is None or ratio is None:
        return "unknown"
    if r20 >= 0.25 or r5 >= 0.15:
        return "extended"
    if r20 < 0.10 and ratio < 2:
        return "quiet"
    return "moving"


def grade_evidence(events: list[dict[str, Any]], origin_groups: dict[str, str]) -> tuple[str, list[str]]:
    """독립 출처 2개 이상 + 그중 원문 확인된 실행 사실 1개 이상이면 high(§10.1).

    independence=unknown 인 근거는 high 조건의 근거 수에서 제외한다(§5 규칙 5).
    """
    reasons: list[str] = []
    if not events:
        return "unknown", ["no_event"]
    known = {origin_groups.get(e["source_version_id"], "") for e in events
             if origin_groups.get(e["source_version_id"]) and
             e.get("independence") != "unknown"}
    facts = [e for e in events if e["change"].get("fact_type") == "observed_fact"]
    if len(known) >= 2 and facts:
        return "high", reasons
    if facts or any(e["change"].get("fact_type") in ("company_guidance", "analyst_estimate")
                    for e in events):
        if len(known) < 2:
            reasons.append("independent_sources_lt_2")
        if not facts:
            reasons.append("no_observed_fact")
        return "medium", reasons
    return "low", ["rumor_or_repeated_only"]


def grade_timing(events: list[dict[str, Any]], cutoff_date: str) -> tuple[str, list[str]]:
    """cutoff 이후 84일 안에 확인 사건이 있고 조건이 명확하면 high(§10.1)."""
    from datetime import date, timedelta

    limit = date.fromisoformat(cutoff_date) + timedelta(days=84)
    dated = []
    for event in events:
        end = (event["change"].get("expected_end") or "").strip()
        if len(end) == 7:
            end += "-28"
        if len(end) == 10:
            try:
                dated.append((date.fromisoformat(end), event))
            except ValueError:
                continue
    if not dated:
        return "unknown", ["no_expected_date"]
    within = [d for d, e in dated if d <= limit]
    if not within:
        return "low", ["expected_beyond_84d"]
    has_condition = any((e["change"].get("confirmation_condition") or "").strip()
                        for _, e in dated)
    return ("high", []) if has_condition else ("medium", ["no_confirmation_condition"])


def grade_price(context: dict[str, Any]) -> tuple[str, list[str]]:
    """근거 있는 가격 시나리오가 없으면 unknown 이다(§10.2).

    현재 구현은 broker_reference·earnings_multiple 입력을 아직 확보하지 않으므로
    항상 unavailable → unknown 이다. 그 사실을 reason_codes 로 남긴다.
    """
    if context.get("state") == "unknown":
        return "unknown", ["price_unavailable"]
    return "unknown", ["scenario_unavailable"]


def apply_policy(
    *, evidence: str, timing: str, price: str, has_new_change: bool,
    price_context: dict[str, Any], invalidated: bool = False, insufficient: bool = False,
    has_open_condition: bool = True,
) -> dict[str, Any]:
    """§10.3 우선순위를 위에서 아래로 최초 일치 하나만 적용한다. 4x4x4 전 조합을 덮는다."""
    codes: list[str] = list(price_context.get("reason_codes", []))
    if insufficient:
        return _decision("insufficient", codes + ["core_info_missing"])
    if invalidated:
        return _decision("invalidated", codes + ["thesis_invalidated"])
    if not price_context.get("history_ok"):
        return _decision("watch", codes + ["price_history_incomplete"])
    if (price_context.get("turnover_20d") or 0) < MIN_TURNOVER:
        return _decision("watch", codes + ["liquidity_below_min"])
    if evidence == "high" and timing == "high" and price in ("high", "medium") and has_new_change:
        return _decision("review_buy", codes)
    if evidence in ("high", "medium") and timing in ("high", "medium") and price == "low":
        return _decision("wait_price", codes + ["price_attractiveness_low"])
    if price == "unknown":
        codes.append("price_unavailable")
    if evidence == "medium":
        codes.append("evidence_confirmation_pending")
    if timing == "medium":
        codes.append("timing_confirmation_pending")
    if has_open_condition:
        return _decision("watch", codes or ["open_condition_remains"])
    if not has_new_change:
        return _decision("inactive", codes + ["repeated_only"])
    return _decision("watch", codes + ["policy_fallback"])


def _decision(action: str, codes: list[str]) -> dict[str, Any]:
    return {"action": action, "reason_codes": sorted(set(codes))}


def select_candidates(assessments: list[dict[str, Any]], limit: int = 5,
                      per_theme: int = 2) -> list[dict[str, Any]]:
    """review_buy 중 최대 limit 개, 같은 주테마 최대 per_theme 개(§10.4)."""
    ranked = sorted(
        [a for a in assessments if a["action"] == "review_buy"],
        key=lambda a: (
            0 if a["grades"]["price"] == "high" else 1,
            a.get("next_check_date") or "9999-12-31",
            -(a.get("latest_evidence_ts") or 0),
            a["subject_id"],
        ),
    )
    picked: list[dict[str, Any]] = []
    per_theme_count: dict[str, int] = {}
    for item in ranked:
        theme = item.get("primary_theme") or "none"
        if per_theme_count.get(theme, 0) >= per_theme:
            continue
        picked.append(item)
        per_theme_count[theme] = per_theme_count.get(theme, 0) + 1
        if len(picked) >= limit:
            break
    return picked
