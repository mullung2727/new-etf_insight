from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query

from duck_watchlist import telegram_cursor
from schemas import DigestItem, DigestLatest, TelegramMention, ThemePeer

router = APIRouter(tags=["telegram"])

# session 은 사전순이 시간순과 다르다(close < evening < morning). 하루 안에서
# 최신 세션을 고르려면 명시 랭크 필요: 장전 → 종가 → 장후.
_SESSION_RANK = "CASE session WHEN 'morning' THEN 0 WHEN 'close' THEN 1 WHEN 'evening' THEN 2 ELSE 3 END"


def _loads(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


@router.get(
    "/telegram/digest/latest",
    response_model=DigestLatest | None,
    operation_id="get_latest_telegram_digest",
    summary="Latest cross-channel stock digest",
    description=(
        "가장 최근 (date_kst, session)의 분석완료 종목요약을 반환한다. "
        "신규(new)를 먼저, 그다음 언급 채널 수 내림차순으로 정렬. "
        "분석완료 데이터가 하나도 없으면 null."
    ),
)
def get_latest_telegram_digest() -> DigestLatest | None:
    with telegram_cursor() as con:
        latest = con.execute(
            "SELECT date_kst, session FROM telegram_stock_insights "
            f"WHERE analysis IS NOT NULL GROUP BY date_kst, session "
            f"ORDER BY date_kst DESC, {_SESSION_RANK} DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            return None
        date_kst, session = latest
        rows = con.execute(
            "SELECT ticker, name, mention_channels, analysis "
            "FROM telegram_stock_insights "
            "WHERE date_kst=? AND session=? AND analysis IS NOT NULL",
            (date_kst, session),
        ).fetchall()

    items: list[DigestItem] = []
    for ticker, name, mention_channels, analysis in rows:
        a = _loads(analysis, {})
        channels = _loads(mention_channels, [])
        items.append(DigestItem(
            ticker=ticker, name=name,
            channels=len(channels) if isinstance(channels, list) else 0,
            change_type=a.get("change_type"),
            change_summary=a.get("change_summary"),
            themes=a.get("themes") or [],
        ))
    # 신규 먼저, 그다음 채널 수 많은 순 (digest 전송 스크립트와 동일 정렬)
    order = {"new": 0, "continued": 1}
    items.sort(key=lambda i: (order.get(i.change_type, 2), -i.channels))
    return DigestLatest(date=date_kst, session=session, count=len(items), items=items)


@router.get(
    "/telegram/mentions/{ticker}",
    response_model=list[TelegramMention],
    operation_id="get_telegram_mentions",
    summary="Per-stock telegram mention history",
    description=(
        "한 종목의 텔레그램 언급 이력을 최신순(date desc, session 시간순 desc)으로 반환. "
        "from/to(date_kst)·session 으로 필터. 미분석 행도 언급이력으로 포함."
    ),
)
def get_telegram_mentions(
    ticker: str,
    from_: str | None = Query(None, alias="from", description="date_kst >= (YYYY-MM-DD)"),
    to: str | None = Query(None, description="date_kst <= (YYYY-MM-DD)"),
    session: str | None = Query(None, description="morning|close|evening"),
) -> list[TelegramMention]:
    clauses = ["ticker=?"]
    args: list[Any] = [ticker]
    if from_:
        clauses.append("date_kst>=?")
        args.append(from_)
    if to:
        clauses.append("date_kst<=?")
        args.append(to)
    if session:
        clauses.append("session=?")
        args.append(session)
    where = " AND ".join(clauses)

    with telegram_cursor() as con:
        rows = con.execute(
            "SELECT date_kst, session, mention_channels, source_post_refs, analysis "
            f"FROM telegram_stock_insights WHERE {where} "
            f"ORDER BY date_kst DESC, {_SESSION_RANK} DESC",
            args,
        ).fetchall()

    out: list[TelegramMention] = []
    for date_kst, session_v, mention_channels, source_post_refs, analysis in rows:
        a = _loads(analysis, {})
        out.append(TelegramMention(
            date_kst=date_kst,
            session=session_v,
            channels=_loads(mention_channels, []),
            post_refs=_loads(source_post_refs, []),
            change_type=a.get("change_type"),
            change_summary=a.get("change_summary"),
            themes=a.get("themes") or [],
        ))
    return out


@router.get(
    "/telegram/theme-peers/{ticker}",
    response_model=list[ThemePeer],
    operation_id="get_telegram_theme_peers",
    summary="Same-theme peer stocks",
    description=(
        "대상 종목의 텔레그램 테마(전 기간 합집합)와 겹치는 다른 종목을 "
        "공유 테마 수 내림차순으로 반환. 테마 출처가 텔레그램뿐이라 미언급 종목은 빠진다."
    ),
)
def get_telegram_theme_peers(ticker: str, limit: int = 10) -> list[ThemePeer]:
    with telegram_cursor() as con:
        rows = con.execute(
            "SELECT ticker, name, analysis FROM telegram_stock_insights "
            "WHERE analysis IS NOT NULL"
        ).fetchall()

    # ticker → 테마 합집합 / 표시명 (데이터 작아 전량 스캔)
    ticker_themes: dict[str, set[str]] = {}
    ticker_name: dict[str, str] = {}
    for tk, name, analysis in rows:
        themes = _loads(analysis, {}).get("themes") or []
        if not isinstance(themes, list):
            continue
        ticker_themes.setdefault(tk, set()).update(themes)
        if name:
            ticker_name[tk] = name

    target = ticker_themes.get(ticker, set())
    if not target:
        return []

    peers: list[ThemePeer] = []
    for tk, themes in ticker_themes.items():
        if tk == ticker:
            continue
        shared = sorted(target & themes)
        if shared:
            peers.append(ThemePeer(ticker=tk, name=ticker_name.get(tk, ""), themes=shared))

    peers.sort(key=lambda p: -len(p.themes))
    return peers[:limit]
