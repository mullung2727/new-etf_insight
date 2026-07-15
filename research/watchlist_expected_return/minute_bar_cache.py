"""키움 분봉을 파일 캐시에 저장하고 정규화해 재사용하는 공통 모듈."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = ROOT / "research" / "watchlist_pullback_strategy" / "minute_cache"


def _price(value: Any) -> int | None:
    try:
        return abs(int(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def normalize_minute_bar(raw: dict[str, Any]) -> dict[str, Any] | None:
    timestamp = str(raw.get("cntr_tm", "")).strip()
    if len(timestamp) != 14 or not timestamp.isdigit():
        return None
    bar = {
        "timestamp": timestamp,
        "date": timestamp[:8],
        "time": timestamp[8:],
        "open": _price(raw.get("open_pric")),
        "high": _price(raw.get("high_pric")),
        "low": _price(raw.get("low_pric")),
        "close": _price(raw.get("cur_prc")),
        "volume": _price(raw.get("trde_qty")),
    }
    if not all(bar[key] for key in ("open", "high", "low", "close")):
        return None
    return bar


def cache_path(cache_dir: Path, symbol: str, base_dt: str, scope: str = "1") -> Path:
    return cache_dir / f"{symbol}_{base_dt}_{scope}m.json"


def _covers_session_start(bars: list[dict[str, Any]], earliest_dt: str) -> bool:
    earliest_day = [bar for bar in bars if bar["date"] == earliest_dt]
    return bool(
        earliest_day
        and (earliest_day[0]["time"] <= "090000" or any(bar["date"] < earliest_dt for bar in bars))
    )


def load_or_fetch_minutes(
    symbol: str,
    base_dt: str,
    earliest_dt: str,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    scope: str = "1",
    fetch_page: Callable[..., dict[str, Any]] | None = None,
    max_pages: int = 5,
) -> dict[str, Any]:
    """기준일에서 earliest_dt까지 연속조회하고 종목별 JSON 캐시를 반환한다."""
    path = cache_path(cache_dir, symbol, base_dt, scope)
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("cache_version") == 2 and _covers_session_start(cached.get("bars", []), earliest_dt):
            cached["complete"] = True
            return cached
    if fetch_page is None:
        from broker.kiwoom.quotes import get_minute_chart

        fetch_page = get_minute_chart

    bars_by_time: dict[str, dict[str, Any]] = {}
    cont_yn, next_key = "N", ""
    page_count = 0
    while page_count < max_pages:
        result = fetch_page(
            symbol, scope, base_dt, cont_yn=cont_yn, next_key=next_key
        )
        page_count += 1
        for raw in result["bars"]:
            bar = normalize_minute_bar(raw)
            if bar:
                bars_by_time[bar["timestamp"]] = bar
        if _covers_session_start(list(bars_by_time.values()), earliest_dt):
            break
        cont_yn, next_key = result["cont_yn"], result["next_key"]
        if cont_yn != "Y" or not next_key:
            break

    bars = sorted(bars_by_time.values(), key=lambda item: item["timestamp"])
    payload = {
        "cache_version": 2,
        "symbol": symbol,
        "scope_minutes": int(scope),
        "base_dt": base_dt,
        "earliest_requested_dt": earliest_dt,
        "page_count": page_count,
        "complete": _covers_session_start(bars, earliest_dt),
        "bars": bars,
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload
