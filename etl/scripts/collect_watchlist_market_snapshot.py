"""D일 15:00 watchlist 후보의 키움 ka10001 시세 스냅샷을 저장한다."""
from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

try:
    from scripts.build_intraday_ranking import ENV_PATH, _HOSTS, _kiwoom_env, get_token
    from scripts.wl_sqlite import connect_ro, connect_rw
except ImportError:  # 직접 실행: python scripts/collect_watchlist_market_snapshot.py
    from build_intraday_ranking import ENV_PATH, _HOSTS, _kiwoom_env, get_token
    from wl_sqlite import connect_ro, connect_rw


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"
SEOUL = ZoneInfo("Asia/Seoul")
REQUEST_TIMEOUT = 20
TR_STOCK_INFO = "ka10001"
EP_STOCK_INFO = "/api/dostk/stkinfo"

CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS watchlist_market_snapshots (
    date          TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    snapshot_at   TEXT NOT NULL,
    current_price INTEGER,
    open_price    INTEGER,
    high_price    INTEGER,
    volume        INTEGER,
    change_rate   REAL,
    source        TEXT NOT NULL,
    PRIMARY KEY (date, ticker)
)
"""


def _abs_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return abs(int(str(value).replace(",", "").replace("+", "").strip()))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def parse_snapshot(ticker: str, raw: dict[str, Any], observed_at: datetime) -> dict[str, Any]:
    snapshot = {
        "ticker": ticker,
        "snapshot_at": observed_at.astimezone(SEOUL).isoformat(timespec="seconds"),
        "current_price": _abs_int(raw.get("cur_prc")),
        "open_price": _abs_int(raw.get("open_pric")),
        "high_price": _abs_int(raw.get("high_pric")),
        "volume": _abs_int(raw.get("trde_qty")),
        "change_rate": _float(raw.get("flu_rt")),
        "source": TR_STOCK_INFO,
    }
    prices = [snapshot[key] for key in ("current_price", "open_price", "high_price")]
    if any(value is None or value <= 0 for value in prices) or snapshot["volume"] is None:
        raise ValueError(f"{ticker}: ka10001 필수 시세 누락")
    if snapshot["high_price"] < max(snapshot["current_price"], snapshot["open_price"]):
        raise ValueError(f"{ticker}: 고가가 현재가/시가보다 작음")
    return snapshot


def fetch_snapshot(
    token: str,
    host: str,
    ticker: str,
    clock: Callable[[], datetime] = lambda: datetime.now(SEOUL),
    retries: int = 3,
) -> dict[str, Any]:
    for attempt in range(retries + 1):
        response = requests.post(
            f"{host}{EP_STOCK_INFO}",
            json={"stk_cd": ticker},
            headers={
                "content-type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token}",
                "api-id": TR_STOCK_INFO,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 429 and attempt < retries:
            time.sleep(attempt + 1)
            continue
        break
    response.raise_for_status()
    raw = response.json()
    if raw.get("return_code") not in (None, 0, "0"):
        raise RuntimeError(f"{TR_STOCK_INFO} {ticker}: {raw.get('return_msg', '')}")
    return parse_snapshot(ticker, raw, clock())


def is_capture_window(value: datetime) -> bool:
    local = value.astimezone(SEOUL)
    return local.hour == 15 and 0 <= local.minute <= 2


def upsert_snapshots(db_path: Path, date: str, snapshots: list[dict[str, Any]]) -> int:
    with connect_rw(db_path) as con:
        con.execute(CREATE_SNAPSHOTS)
        con.executemany("""
            INSERT OR REPLACE INTO watchlist_market_snapshots (
                date, ticker, snapshot_at, current_price, open_price,
                high_price, volume, change_rate, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [(
            date, row["ticker"], row["snapshot_at"], row["current_price"],
            row["open_price"], row["high_price"], row["volume"],
            row["change_rate"], row["source"],
        ) for row in snapshots])
    return len(snapshots)


def run(
    db_path: Path,
    date: str,
    clock: Callable[[], datetime] = lambda: datetime.now(SEOUL),
    fetcher: Callable[[str, str, str, Callable[[], datetime]], dict[str, Any]] = fetch_snapshot,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    started_at = clock().astimezone(SEOUL)
    if date != started_at.strftime("%Y%m%d"):
        raise RuntimeError("요청 날짜와 현재 KST 날짜가 다름")
    target_at = datetime.strptime(date + "150000", "%Y%m%d%H%M%S").replace(tzinfo=SEOUL)
    if started_at < target_at:
        wait_seconds = (target_at - started_at).total_seconds()
        if wait_seconds > 120:
            raise RuntimeError(f"15:00까지 대기시간이 너무 김: {wait_seconds:.0f}초")
        sleep_fn(wait_seconds)
        started_at = clock().astimezone(SEOUL)
    if not is_capture_window(started_at):
        raise RuntimeError(f"15:00 스냅샷 허용시간 아님: {started_at.isoformat(timespec='seconds')}")

    with connect_ro(db_path) as con:
        tickers = [row[0] for row in con.execute(
            "SELECT stock_code FROM watchlist WHERE date=? ORDER BY stock_code", [date]
        )]
    if not tickers:
        print(f"[market_snapshot] {date}: watchlist 후보 없음")
        return 0

    load_dotenv(ENV_PATH)
    env = _kiwoom_env()
    host = _HOSTS[env]
    token = get_token(host, env)
    snapshots = []
    failures = []
    for index, ticker in enumerate(tickers):
        try:
            snapshots.append(fetcher(token, host, ticker, clock))
        except (requests.RequestException, RuntimeError, ValueError, TypeError) as exc:
            failures.append(ticker)
            print(f"[warn] market_snapshot_failed ticker={ticker} error={type(exc).__name__}: {exc}")
        if index + 1 < len(tickers):
            sleep_fn(0.2)
    count = upsert_snapshots(db_path, date, snapshots)
    print(
        f"[market_snapshot] env={env} date={date} requested={len(tickers)} "
        f"saved={count} failed={len(failures)} -> {db_path}"
    )
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="D일 15:00 watchlist ka10001 시세 저장")
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    run(args.db_path, args.date)


if __name__ == "__main__":
    main()
