"""Build/refresh the US listed-common-stock daily OHLCV cache.

Source universe: Nasdaq Trader ``nasdaqtraded.txt``.
Source bars/shares: yfinance. Prices are split-adjusted but not dividend-adjusted.

Usage (from etl/):
    uv run python scripts/build_us_ohlcv.py --from 20240101
    uv run python scripts/build_us_ohlcv.py
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import duckdb
import pandas as pd


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "us_ohlcv.duckdb"
UNIVERSE_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
DEFAULT_FROM_DATE = "20240101"
BATCH_SIZE = 200
BENCHMARKS = ("SPY", "QQQ", "IWM")
NEW_YORK = ZoneInfo("America/New_York")

_EXCLUDED_NAME = re.compile(
    r"\b(?:Warrants?|Units?|Rights?|Preferred|Depositary)\b", re.IGNORECASE
)
_EXCLUDED_SYMBOL = re.compile(r"[$^=]")
# 클래스주만 점을 허용한다 (BRK.B). 워런트·유닛·우선주는 _EXCLUDED_NAME 이 이미 뺀다.
_CLASS_SHARE = re.compile(r"^[A-Z]+\.[A-Z]$")
_MARKETS = {
    "Q": "NASDAQ",
    "N": "NYSE",
    "A": "AMEX",
    "P": "ARCA",
    "Z": "BATS",
    "V": "IEX",
}

_CREATE_OHLCV = """
CREATE TABLE IF NOT EXISTS ohlcv (
    date VARCHAR, ticker VARCHAR, market VARCHAR,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume BIGINT, trading_value DOUBLE, market_cap DOUBLE, list_shrs BIGINT,
    PRIMARY KEY (date, ticker)
)
"""
_CREATE_SPLITS = """
CREATE TABLE IF NOT EXISTS splits (
    ticker VARCHAR, date VARCHAR, ratio DOUBLE,
    PRIMARY KEY (ticker, date)
)
"""
_CREATE_NAMES = """
CREATE TABLE IF NOT EXISTS stock_names (
    code VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, updated_at VARCHAR NOT NULL
)
"""


@dataclass(frozen=True)
class UniverseItem:
    ticker: str
    name: str
    market: str


def _yahoo_symbol(ticker: str) -> str | None:
    """나스닥 심볼 → 야후 심볼. 클래스주 BRK.B 는 BRK-B, 그 밖의 점 포함 심볼은 제외."""
    if "." not in ticker:
        return ticker
    return ticker.replace(".", "-") if _CLASS_SHARE.match(ticker) else None


def _default_text_fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8")


def load_universe(
    text_fetch: Callable[[str], str] = _default_text_fetch,
) -> list[UniverseItem]:
    """Return current US common-stock universe plus three benchmark ETFs."""
    frame = pd.read_csv(io.StringIO(text_fetch(UNIVERSE_URL)), sep="|", dtype=str)
    required = {"Nasdaq Traded", "Symbol", "Security Name", "Listing Exchange", "ETF", "Test Issue"}
    if not required.issubset(frame.columns):
        raise RuntimeError("Nasdaq Trader universe response has unexpected columns")
    out: dict[str, UniverseItem] = {}
    for row in frame.to_dict("records"):
        ticker = str(row.get("Symbol") or "").strip()
        name = str(row.get("Security Name") or "").strip()
        if (
            row.get("Nasdaq Traded") != "Y"
            or row.get("ETF") != "N"
            or row.get("Test Issue") != "N"
            or not ticker
            or ticker.lower() == "nan"
            or _EXCLUDED_NAME.search(name)
            or _EXCLUDED_SYMBOL.search(ticker)
        ):
            continue
        symbol = _yahoo_symbol(ticker)
        if symbol is None:
            continue
        market = _MARKETS.get(str(row.get("Listing Exchange") or "").strip(), "UNKNOWN")
        out[symbol] = UniverseItem(symbol, name, market)
    for ticker in BENCHMARKS:
        out.setdefault(ticker, UniverseItem(ticker, ticker, "ARCA"))
    return [out[ticker] for ticker in sorted(out)]


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_CREATE_OHLCV)
    con.execute(_CREATE_SPLITS)
    con.execute(_CREATE_NAMES)


def upsert_names(con: duckdb.DuckDBPyConnection, universe: Iterable[UniverseItem]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    con.executemany(
        "INSERT OR REPLACE INTO stock_names VALUES (?,?,?)",
        [(item.ticker, item.name, now) for item in universe],
    )


def plan_fetch(
    con: duckdb.DuckDBPyConnection,
    tickers: Iterable[str],
    from_date: str,
) -> dict[str, list[str]]:
    """Group tickers by inclusive fetch start (last held day, or from_date)."""
    held = dict(con.execute("SELECT ticker, max(date) FROM ohlcv GROUP BY ticker").fetchall())
    groups: dict[str, list[str]] = {}
    for ticker in sorted(set(tickers)):
        start = held.get(ticker) or from_date
        groups.setdefault(start, []).append(ticker)
    return groups


def _default_download(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        tickers,
        start=datetime.strptime(start, "%Y%m%d").strftime("%Y-%m-%d"),
        end=datetime.strptime(end, "%Y%m%d").strftime("%Y-%m-%d"),
        auto_adjust=False,
        actions=True,
        group_by="ticker",
        threads=True,
        progress=False,
        timeout=30,
    )


def _ticker_frame(data: pd.DataFrame, ticker: str, requested: int) -> pd.DataFrame | None:
    if data.empty:
        return None
    if isinstance(data.columns, pd.MultiIndex):
        level0 = {str(value) for value in data.columns.get_level_values(0)}
        level1 = {str(value) for value in data.columns.get_level_values(1)}
        if ticker in level0:
            return data[ticker]
        if ticker in level1:
            return data.xs(ticker, axis=1, level=1)
        return None
    return data if requested == 1 else None


def fetch_batch(
    tickers: list[str],
    start: str,
    end: str,
    download: Callable[[list[str], str, str], pd.DataFrame] = _default_download,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    data = download(tickers, start, end)
    returned: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        frame = _ticker_frame(data, ticker, len(tickers))
        if frame is not None and frame.notna().any().any():
            returned[ticker] = frame
    return returned, sorted(set(tickers) - set(returned))


def _day_string(value: object) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def to_rows(
    frames: dict[str, pd.DataFrame],
    markets: dict[str, str],
    today_ny: str,
) -> tuple[list[tuple], list[tuple]]:
    rows: list[tuple] = []
    splits: list[tuple] = []
    for ticker, frame in frames.items():
        for index, values in frame.iterrows():
            day = _day_string(index)
            if day >= today_ny:
                continue
            ratio = values.get("Stock Splits", 0)
            if pd.notna(ratio) and float(ratio) != 0:
                splits.append((ticker, day, float(ratio)))
            prices = [values.get(key) for key in ("Open", "High", "Low", "Close")]
            if any(pd.isna(value) for value in prices):
                continue
            volume_value = values.get("Volume")
            volume = None if pd.isna(volume_value) else int(volume_value)
            close = float(prices[3])
            rows.append(
                (
                    day, ticker, markets.get(ticker, "UNKNOWN"),
                    float(prices[0]), float(prices[1]), float(prices[2]), close,
                    volume, None if volume is None else close * volume, None, None,
                )
            )
    return rows, splits


def _insert_rows(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
    if not rows:
        return
    con.execute("CREATE OR REPLACE TEMP TABLE _us_staging AS SELECT * FROM ohlcv LIMIT 0")
    con.executemany("INSERT INTO _us_staging VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO ohlcv SELECT * FROM _us_staging")


def _replace_split_history(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    rows: list[tuple],
    splits: list[tuple],
) -> None:
    """Atomically replace one ticker after a split-triggered full refetch."""
    con.execute("BEGIN TRANSACTION")
    try:
        stored_first = con.execute(
            "SELECT min(date) FROM ohlcv WHERE ticker=?", [ticker]
        ).fetchone()[0]
        first = min((row[0] for row in rows), default=None)
        # 잘린 재조회(응답이 비어있지만 않으면 통과)로 과거 구간을 통째로 날리는 걸 막는다.
        if stored_first is not None and (first is None or first > stored_first):
            raise RuntimeError(
                f"{ticker} 재조회가 기존 구간을 못 덮는다 (저장 {stored_first} < 응답 {first})"
            )
        con.execute("DELETE FROM ohlcv WHERE ticker=?", [ticker])
        con.execute("DELETE FROM splits WHERE ticker=?", [ticker])
        _insert_rows(con, rows)
        if splits:
            con.executemany("INSERT INTO splits VALUES (?,?,?)", splits)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def _split_factor(day: str, ticker_splits: list[tuple[str, float]]) -> float:
    factor = 1.0
    for split_day, ratio in ticker_splits:
        if split_day > day:
            factor *= ratio
    return factor


def apply_share_history(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    shares: pd.Series | None,
) -> int:
    """Apply point-in-time raw shares and raw-price market cap to one ticker."""
    if shares is None or shares.empty:
        return 0
    history = sorted((_day_string(idx), int(value)) for idx, value in shares.items() if pd.notna(value))
    split_rows = con.execute(
        "SELECT date, ratio FROM splits WHERE ticker=? ORDER BY date", [ticker]
    ).fetchall()
    bars = con.execute(
        "SELECT date, close FROM ohlcv WHERE ticker=? ORDER BY date", [ticker]
    ).fetchall()
    updates: list[tuple[int, float, str, str]] = []
    pos = -1
    for day, close in bars:
        while pos + 1 < len(history) and history[pos + 1][0] <= day:
            pos += 1
        if pos < 0:
            continue
        count = history[pos][1]
        market_cap = float(close) * _split_factor(day, split_rows) * count
        updates.append((count, market_cap, ticker, day))
    if updates:
        con.executemany(
            "UPDATE ohlcv SET list_shrs=?, market_cap=? WHERE ticker=? AND date=?", updates
        )
    return len(updates)


def _default_shares_fetch(ticker: str, start: str) -> pd.Series | None:
    import yfinance as yf

    return yf.Ticker(ticker).get_shares_full(
        start=datetime.strptime(start, "%Y%m%d").strftime("%Y-%m-%d")
    )


def ensure_shares(
    con: duckdb.DuckDBPyConnection,
    tickers: Iterable[str],
    from_date: str,
    shares_fetch: Callable[[str, str], pd.Series | None] = _default_shares_fetch,
    refresh_all: bool = False,
) -> dict[str, object]:
    """Fetch initial share history; daily rows normally use carried prior values."""
    updated_rows = 0
    empty: list[str] = []
    failed: list[str] = []
    known_tickers = {row[0] for row in con.execute(
        "SELECT DISTINCT ticker FROM ohlcv WHERE list_shrs IS NOT NULL"
    ).fetchall()}
    pending = [
        ticker for ticker in sorted(set(tickers))
        if refresh_all or ticker not in known_tickers
    ]
    total = len(pending)
    if total:
        print(f"[shares] start tickers={total}", flush=True)
    for index, ticker in enumerate(pending, start=1):
        try:
            shares = shares_fetch(ticker, from_date)
            if shares is None or shares.empty:
                empty.append(ticker)
            else:
                updated_rows += apply_share_history(con, ticker, shares)
        except Exception as exc:
            failed.append(ticker)
            print(f"[shares {index}/{total}] failed ticker={ticker} error={exc}", flush=True)
        if index % 25 == 0 or index == total:
            print(
                f"[shares {index}/{total}] updated_rows={updated_rows} "
                f"empty={len(empty)} failed={len(failed)}",
                flush=True,
            )
    carried = carry_forward_shares(con)
    return {
        "updated_rows": updated_rows,
        "carried_rows": carried,
        "empty_tickers": empty,
        "failed_tickers": failed,
    }


def carry_forward_shares(con: duckdb.DuckDBPyConnection) -> int:
    """Fill new NULL share rows from the ticker's latest earlier known value."""
    result = con.execute("""
        WITH filled AS (
            SELECT date, ticker,
                   last_value(list_shrs IGNORE NULLS) OVER (
                       PARTITION BY ticker ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                   ) AS shares
            FROM ohlcv
        )
        UPDATE ohlcv AS o
        SET list_shrs=f.shares,
            -- apply_share_history 와 같은 식: 분할 반영 종가 × 이후 분할비 × 당시 주식수
            market_cap=o.close*f.shares*COALESCE(
                (SELECT product(s.ratio) FROM splits s
                 WHERE s.ticker=o.ticker AND s.date > o.date), 1)
        FROM filled AS f
        WHERE o.date=f.date AND o.ticker=f.ticker
          AND o.list_shrs IS NULL AND f.shares IS NOT NULL
        RETURNING o.date
    """).fetchall()
    return len(result)


def audit_gaps(
    con: duckdb.DuckDBPyConnection,
    universe: Iterable[UniverseItem],
) -> dict[str, list[str]]:
    """Find internal missing dates against SPY; never classify edge gaps as errors."""
    calendar = [row[0] for row in con.execute(
        "SELECT date FROM ohlcv WHERE ticker='SPY' ORDER BY date"
    ).fetchall()]
    if not calendar:
        raise RuntimeError("SPY calendar is empty")
    latest = con.execute("SELECT max(date) FROM ohlcv WHERE ticker='SPY'").fetchone()[0]
    expected_latest = max(row[0] for row in con.execute("SELECT DISTINCT date FROM ohlcv").fetchall())
    if latest != expected_latest:
        raise RuntimeError("SPY calendar is not current")
    allowed = {item.ticker for item in universe}
    missing_rows = con.execute("""
        WITH bounds AS (
            SELECT ticker, min(date) AS lo, max(date) AS hi
            FROM ohlcv GROUP BY ticker
        ), calendar AS (
            SELECT date FROM ohlcv WHERE ticker='SPY'
        )
        SELECT b.ticker, c.date
        FROM bounds AS b
        JOIN calendar AS c ON c.date>b.lo AND c.date<b.hi
        LEFT JOIN ohlcv AS o ON o.ticker=b.ticker AND o.date=c.date
        WHERE o.ticker IS NULL
        ORDER BY b.ticker, c.date
    """).fetchall()
    result: dict[str, list[str]] = {}
    for ticker, day in missing_rows:
        if ticker in allowed:
            result.setdefault(ticker, []).append(day)
    return result


def repair_gaps(
    con: duckdb.DuckDBPyConnection,
    universe: list[UniverseItem],
    end_date: str,
    download: Callable[[list[str], str, str], pd.DataFrame] = _default_download,
    today_ny: str | None = None,
) -> dict[str, object]:
    """Refetch internal-gap ranges; unresolved exchange suspensions remain untouched."""
    gaps = audit_gaps(con, universe)
    if not gaps:
        return {"candidate_tickers": 0, "inserted_rows": 0, "unresolved": {}}
    today_ny = today_ny or datetime.now(NEW_YORK).strftime("%Y%m%d")
    markets = {item.ticker: item.market for item in universe}
    groups: dict[str, list[str]] = {}
    for ticker, days in gaps.items():
        groups.setdefault(min(days), []).append(ticker)
    inserted = 0
    for start, tickers in groups.items():
        for offset in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[offset:offset + BATCH_SIZE]
            try:
                frames, _missing = fetch_batch(batch, start, end_date, download)
            except Exception:
                continue
            rows, split_rows = to_rows(frames, markets, today_ny)
            split_tickers = {row[0] for row in split_rows}
            normal_rows = [row for row in rows if row[1] not in split_tickers]
            _insert_rows(con, normal_rows)
            inserted += len(normal_rows)
            for ticker in sorted(split_tickers):
                try:
                    full_frames, missing = fetch_batch(
                        [ticker], min(row[0] for row in con.execute(
                            "SELECT date FROM ohlcv WHERE ticker=?", [ticker]
                        ).fetchall()), end_date, download
                    )
                    if missing:
                        continue
                    full_rows, full_splits = to_rows(full_frames, markets, today_ny)
                    if full_rows:
                        _replace_split_history(con, ticker, full_rows, full_splits)
                        inserted += len(full_rows)
                except Exception:
                    continue
    unresolved = audit_gaps(con, universe)
    return {
        "candidate_tickers": len(gaps),
        "inserted_rows": inserted,
        "unresolved": unresolved,
    }


def ensure_ohlcv(
    con: duckdb.DuckDBPyConnection,
    universe: list[UniverseItem],
    from_date: str,
    end_date: str,
    download: Callable[[list[str], str, str], pd.DataFrame] = _default_download,
    today_ny: str | None = None,
) -> dict[str, object]:
    ensure_schema(con)
    upsert_names(con, universe)
    today_ny = today_ny or datetime.now(NEW_YORK).strftime("%Y%m%d")
    markets = {item.ticker: item.market for item in universe}
    failed: list[str] = []
    inserted = 0
    split_refetched: list[str] = []
    fetch_plan = plan_fetch(con, (item.ticker for item in universe), from_date)
    total_batches = sum(
        (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
        for tickers in fetch_plan.values()
    )
    batch_no = 0
    for start, tickers in fetch_plan.items():
        for offset in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[offset:offset + BATCH_SIZE]
            batch_no += 1
            started_at = time.perf_counter()
            print(
                f"[ohlcv {batch_no}/{total_batches}] start={start} "
                f"end={end_date} tickers={len(batch)}",
                flush=True,
            )
            try:
                frames, missing = fetch_batch(batch, start, end_date, download)
            except Exception as exc:
                failed.extend(batch)
                print(
                    f"[ohlcv {batch_no}/{total_batches}] failed "
                    f"elapsed={time.perf_counter() - started_at:.1f}s error={exc}",
                    flush=True,
                )
                continue
            failed.extend(missing)
            rows, split_rows = to_rows(frames, markets, today_ny)
            split_tickers = {row[0] for row in split_rows}
            normal_rows = [row for row in rows if row[1] not in split_tickers]
            _insert_rows(con, normal_rows)
            inserted += len(normal_rows)
            for ticker in sorted(split_tickers):
                try:
                    ticker_rows = [row for row in rows if row[1] == ticker]
                    ticker_splits = [row for row in split_rows if row[0] == ticker]
                    if start == from_date and ticker_rows:
                        _replace_split_history(con, ticker, ticker_rows, ticker_splits)
                        inserted += len(ticker_rows)
                        split_refetched.append(ticker)
                        continue
                    full_frames, full_missing = fetch_batch([ticker], from_date, end_date, download)
                    if full_missing:
                        failed.append(ticker)
                        continue
                    full_rows, full_splits = to_rows(full_frames, markets, today_ny)
                    if not full_rows:
                        failed.append(ticker)
                        continue
                    _replace_split_history(con, ticker, full_rows, full_splits)
                    inserted += len(full_rows)
                    split_refetched.append(ticker)
                except Exception:
                    failed.append(ticker)
            print(
                f"[ohlcv {batch_no}/{total_batches}] done "
                f"elapsed={time.perf_counter() - started_at:.1f}s "
                f"received={len(batch) - len(missing)} missing={len(missing)} "
                f"rows={len(rows)} cumulative_rows={inserted}",
                flush=True,
            )
    carried = carry_forward_shares(con)
    return {
        "inserted_rows": inserted,
        "failed_tickers": sorted(set(failed)),
        "split_refetched": split_refetched,
        "shares_carried": carried,
    }


def _validate_date(label: str, value: str) -> None:
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise SystemExit(f"error: {label} must be YYYYMMDD, got {value!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build US common-stock OHLCV cache")
    parser.add_argument("--from", dest="from_date", default=DEFAULT_FROM_DATE)
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--refresh-shares", action="store_true")
    args = parser.parse_args()
    today_ny = datetime.now(NEW_YORK).strftime("%Y%m%d")
    to_date = args.to_date or today_ny
    _validate_date("--from", args.from_date)
    _validate_date("--to", to_date)
    if args.from_date >= to_date:
        raise SystemExit("error: --from must be before --to (exclusive)")
    universe = load_universe()
    args.db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(args.db_path)) as con:
        stats = ensure_ohlcv(con, universe, args.from_date, to_date, today_ny=today_ny)
        gap_stats = repair_gaps(con, universe, to_date, today_ny=today_ny)
        share_stats = ensure_shares(
            con,
            (item.ticker for item in universe),
            args.from_date,
            refresh_all=args.refresh_shares,
        )
    print(
        f"universe={len(universe)} inserted={stats['inserted_rows']} "
        f"failed={len(stats['failed_tickers'])} splits={len(stats['split_refetched'])} "
        f"gap_candidates={gap_stats['candidate_tickers']} "
        f"gap_unresolved={len(gap_stats['unresolved'])} "
        f"shares_updated={share_stats['updated_rows']} "
        f"shares_empty={len(share_stats['empty_tickers'])} "
        f"shares_failed={len(share_stats['failed_tickers'])} -> {args.db_path}"
    )


if __name__ == "__main__":
    main()
