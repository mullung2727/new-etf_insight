"""Early-morning US daily load for signal use (KST 07:30 year-round).

07:30 stays 90+ min after the US close all year: after DST ends (first
Sunday of November) the close is KST 06:00, so 06:30 would collide with
the broker cutoff (NY 16:30).

Fetches the previous US session bars for a small set of tickers from Kiwoom
usa06012 via the broker (/us/daily) and VIX/VIX3M from CBOE CSVs (Yahoo fallback),
upserting into us_ohlcv.duckdb (etf_ohlcv/ohlcv) and us_macro.duckdb
(index_ohlcv). The 11:00 Yahoo batch (us-daily) later overwrites with full
history whenever its values are healthy.

Usage (from etl/):
    uv run python scripts/build_us_early.py
    uv run python scripts/build_us_early.py --extra-tickers-file path/to/tickers.txt
"""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd
import requests

from build_us_macro import DEFAULT_DB_PATH as MACRO_DB_PATH
from build_us_macro import ensure_schema as ensure_macro_schema
from build_us_ohlcv import DEFAULT_DB_PATH as OHLCV_DB_PATH
from build_us_ohlcv import ETF_TICKERS
from build_us_ohlcv import _default_download, fetch_batch
from build_us_ohlcv import ensure_schema as ensure_ohlcv_schema

CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"
CBOE_TICKERS = (("VIX", "^VIX"), ("VIX3M", "^VIX3M"))
# Cloudflare rejects the default python-requests UA (measured) — send a browser string.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch_daily(
    broker_url: str,
    code: str,
    from_date: str,
    today_ny: str | None = None,
    get: Callable[..., requests.Response] = requests.get,
) -> list[dict]:
    """Return one ticker's recent daily bars (ascending) from the broker."""
    params = {"code": code, "from": from_date}
    if today_ny:
        params["today_ny"] = today_ny
    resp = get(f"{broker_url.rstrip('/')}/us/daily", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def load_extra_tickers(path: str | Path) -> list[str]:
    """Read one ticker per line; blank lines and '#' comments are ignored."""
    tickers = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tickers.append(line.upper())
    return tickers


def upsert_bars(
    con: duckdb.DuckDBPyConnection,
    table: str,
    ticker: str,
    bars: list[dict],
) -> tuple[int, int]:
    """Upsert broker bars into etf_ohlcv/ohlcv. Returns (inserted, updated).

    Existing rows are UPDATEd only when an OHLCV value differs. ohlcv INSERTs
    leave market/trading_value/market_cap/list_shrs NULL for the 11:00 batch.
    """
    if table == "etf_ohlcv":
        select = "SELECT open, high, low, close, volume FROM etf_ohlcv WHERE date=? AND ticker=?"
        insert = "INSERT INTO etf_ohlcv VALUES (?,?,?,?,?,?,?)"
        update = (
            "UPDATE etf_ohlcv SET open=?, high=?, low=?, close=?, volume=? "
            "WHERE date=? AND ticker=?"
        )
    elif table == "ohlcv":
        select = "SELECT open, high, low, close, volume FROM ohlcv WHERE date=? AND ticker=?"
        insert = (
            "INSERT INTO ohlcv (date, ticker, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,?)"
        )
        update = (
            "UPDATE ohlcv SET open=?, high=?, low=?, close=?, volume=? "
            "WHERE date=? AND ticker=?"
        )
    else:
        raise ValueError(f"unknown table {table!r}")
    inserted = updated = 0
    for bar in bars:
        values = [bar[key] for key in ("open", "high", "low", "close")]
        volume = bar.get("volume")
        stored = con.execute(select, [bar["date"], ticker]).fetchone()
        if stored is None:
            con.execute(insert, [bar["date"], ticker, *values, volume])
            inserted += 1
        elif [float(value) for value in stored[:4]] != [float(value) for value in values] \
                or stored[4] != volume:
            con.execute(update, [*values, volume, bar["date"], ticker])
            updated += 1
    return inserted, updated


def parse_cboe(text: str) -> list[dict]:
    """Parse a CBOE daily-prices CSV into [{date: YYYYMMDD, open, high, low, close}]."""
    rows: list[dict] = []
    header: list[str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = [field.strip() for field in line.split(",")]
        if header is None:
            header = [field.upper() for field in fields]
            continue
        record = dict(zip(header, fields))
        try:
            month, day, year = record["DATE"].split("/")
            rows.append({
                "date": f"{year}{month.zfill(2)}{day.zfill(2)}",
                "open": float(record["OPEN"]),
                "high": float(record["HIGH"]),
                "low": float(record["LOW"]),
                "close": float(record["CLOSE"]),
            })
        except (KeyError, ValueError):
            raise ValueError(f"CBOE row has unexpected shape: {line!r}")
    if header is None:
        raise ValueError("CBOE response is empty")
    return rows


def fetch_cboe(symbol: str, get: Callable[..., requests.Response] = requests.get) -> str:
    """Download one CBOE history CSV (redirects followed). Browser UA required."""
    resp = get(CBOE_URL.format(symbol=symbol), headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def fetch_yahoo_index(
    tickers: list[str],
    days: int,
    download: Callable[[list[str], str, str], pd.DataFrame] | None = None,
) -> dict[str, list[dict]]:
    """Return recent Yahoo bars per index ticker, ascending by date.

    Each row is {date: YYYYMMDD, open, high, low, close}. Rows with any OHLC
    NaN are dropped (e.g. Yahoo's not-yet-open latest day).
    """
    start = (datetime.now().date() - timedelta(days=days * 2)).strftime("%Y%m%d")
    end = (datetime.now().date() + timedelta(days=1)).strftime("%Y%m%d")
    frames, _missing = fetch_batch(list(tickers), start, end, download or _default_download)
    out: dict[str, list[dict]] = {ticker: [] for ticker in tickers}
    for ticker, frame in frames.items():
        rows: list[dict] = []
        for index, values in frame.iterrows():
            ohlc = [values.get(key) for key in ("Open", "High", "Low", "Close")]
            if any(pd.isna(value) for value in ohlc):
                continue
            rows.append({
                "date": pd.Timestamp(index).strftime("%Y%m%d"),
                "open": float(ohlc[0]),
                "high": float(ohlc[1]),
                "low": float(ohlc[2]),
                "close": float(ohlc[3]),
            })
        rows.sort(key=lambda row: row["date"])
        out[ticker] = rows
    return out


def upsert_index_bars(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    bars: list[dict],
    fetched_at: datetime,
) -> tuple[int, int]:
    """Upsert CBOE bars into index_ohlcv. Returns (inserted, updated)."""
    inserted = updated = 0
    for bar in bars:
        values = [bar["open"], bar["high"], bar["low"], bar["close"]]
        stored = con.execute(
            "SELECT open, high, low, close FROM index_ohlcv WHERE date=? AND ticker=?",
            [bar["date"], ticker],
        ).fetchone()
        if stored is None:
            con.execute(
                "INSERT INTO index_ohlcv VALUES (?,?,?,?,?,?,?)",
                [bar["date"], ticker, *values, fetched_at],
            )
            inserted += 1
        elif [float(value) for value in stored] != [float(value) for value in values]:
            con.execute(
                "UPDATE index_ohlcv SET open=?, high=?, low=?, close=?, fetched_at=? "
                "WHERE date=? AND ticker=?",
                [*values, fetched_at, bar["date"], ticker],
            )
            updated += 1
    return inserted, updated


def db_latest(con: duckdb.DuckDBPyConnection, table: str, ticker: str) -> str | None:
    row = con.execute(f"SELECT max(date) FROM {table} WHERE ticker=?", [ticker]).fetchone()
    return row[0] if row else None


def check_completion(
    con: duckdb.DuckDBPyConnection,
    mcon: duckdb.DuckDBPyConnection,
    etf_tickers: tuple[str, ...] | list[str],
    extra_tickers: list[str],
    expected_latest: str,
) -> list[str]:
    """Tickers whose stored latest day is older than the broker's QQQ latest."""
    missing = []
    for ticker in etf_tickers:
        if (db_latest(con, "etf_ohlcv", ticker) or "") < expected_latest:
            missing.append(ticker)
    for ticker in extra_tickers:
        if (db_latest(con, "ohlcv", ticker) or "") < expected_latest:
            missing.append(ticker)
    for _, ticker in CBOE_TICKERS:
        if (db_latest(mcon, "index_ohlcv", ticker) or "") < expected_latest:
            missing.append(ticker)
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Early US daily load from Kiwoom broker + CBOE")
    parser.add_argument("--broker-url", default="http://localhost:8001")
    parser.add_argument("--extra-tickers-file", default=None)
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--today-ny", default=None)
    args = parser.parse_args(argv)
    if args.days < 1:
        raise SystemExit("error: --days must be >= 1")

    extra_tickers = load_extra_tickers(args.extra_tickers_file) if args.extra_tickers_file else []
    from_date = (datetime.now().date() - timedelta(days=args.days * 2)).strftime("%Y%m%d")

    OHLCV_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    MACRO_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(OHLCV_DB_PATH))
    mcon = duckdb.connect(str(MACRO_DB_PATH))
    try:
        ensure_ohlcv_schema(con)
        ensure_macro_schema(mcon)
        inserted = updated = 0
        expected_latest: str | None = None

        for ticker in ETF_TICKERS:
            try:
                bars = fetch_daily(args.broker_url, ticker, from_date, args.today_ny)
            except Exception as exc:
                print(f"[us-early] {ticker} broker fetch failed: {type(exc).__name__}", flush=True)
                continue
            if not bars:
                print(f"[us-early] {ticker} broker returned no bars", flush=True)
                continue
            if ticker == "QQQ":
                expected_latest = bars[-1]["date"]
            ins, upd = upsert_bars(con, "etf_ohlcv", ticker, bars[-args.days:])
            inserted += ins
            updated += upd

        for ticker in extra_tickers:
            try:
                bars = fetch_daily(args.broker_url, ticker, from_date, args.today_ny)
            except Exception as exc:
                print(f"[us-early] {ticker} broker fetch failed: {type(exc).__name__}", flush=True)
                continue
            if not bars:
                print(f"[us-early] {ticker} broker returned no bars", flush=True)
                continue
            ins, upd = upsert_bars(con, "ohlcv", ticker, bars[-args.days:])
            inserted += ins
            updated += upd

        fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
        yahoo: dict[str, list[dict]] = {}
        if expected_latest is not None:
            try:
                yahoo = fetch_yahoo_index(
                    [ticker for _, ticker in CBOE_TICKERS], args.days
                )
            except Exception as exc:
                print(f"[us-early] 야후 fetch 실패: {type(exc).__name__}", flush=True)
        for symbol, ticker in CBOE_TICKERS:
            try:
                rows = parse_cboe(fetch_cboe(symbol))[-args.days:]
            except Exception as exc:
                print(f"[us-early] {ticker} CBOE fetch failed: {type(exc).__name__}", flush=True)
                rows = []
            ins, upd = upsert_index_bars(mcon, ticker, rows, fetched_at)
            if expected_latest is not None:
                try:
                    have = {
                        row[0]
                        for row in mcon.execute(
                            "SELECT date FROM index_ohlcv WHERE ticker=?", [ticker]
                        ).fetchall()
                    }
                    fill = [
                        row for row in yahoo.get(ticker, [])
                        if row["date"] <= expected_latest and row["date"] not in have
                    ]
                    if fill:
                        fins, fupd = upsert_index_bars(mcon, ticker, fill, fetched_at)
                        inserted += fins
                        updated += fupd
                        dates = " ".join(row["date"] for row in fill)
                        print(f"[us-early] {ticker} 야후로 채움 {dates}", flush=True)
                except Exception as exc:
                    print(f"[us-early] {ticker} 야후 보충 실패: {type(exc).__name__}", flush=True)
            inserted += ins
            updated += upd

        if expected_latest is None:
            print("MISSING QQQ (no broker data)", flush=True)
            print(
                f"us-early latest=unknown etf=0/{len(ETF_TICKERS)} "
                f"extra=0/{len(extra_tickers)} vix=missing "
                f"updated={updated} inserted={inserted}",
                flush=True,
            )
            return 1

        missing = check_completion(con, mcon, ETF_TICKERS, extra_tickers, expected_latest)
        if missing:
            print("MISSING " + " ".join(missing), flush=True)
        missing_set = set(missing)
        etf_ok = sum(1 for ticker in ETF_TICKERS if ticker not in missing_set)
        extra_ok = sum(1 for ticker in extra_tickers if ticker not in missing_set)
        vix_ok = all(ticker not in missing_set for _, ticker in CBOE_TICKERS)
        print(
            f"us-early latest={expected_latest} etf={etf_ok}/{len(ETF_TICKERS)} "
            f"extra={extra_ok}/{len(extra_tickers)} vix={'ok' if vix_ok else 'missing'} "
            f"updated={updated} inserted={inserted}",
            flush=True,
        )
        return 1 if missing else 0
    finally:
        con.close()
        mcon.close()


if __name__ == "__main__":
    raise SystemExit(main())
