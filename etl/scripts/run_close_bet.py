"""종가배팅 주문 배치 (15:19 실행).

llm_scores에서 score >= score_threshold 종목을 score 내림차순 / max_order_count 제한으로
선별해 키움 시장가 1주 매수 주문을 넣고 close_bet_orders에 결과를 기록한다.

전제:
  - 15:10 scoring 배치(build_intraday_ranking + run_watchlist_research)가 먼저 돌아야 함.
    check_precondition()으로 llm_scores 행 수를 확인하고 0이면 abort.
  - 키움 토큰은 broker/.token_cache.json 공유 (build_intraday_ranking.py 와 동일 패턴).
  - 기본 환경: 모의투자(paper), dry_run=True.

Usage (from etl/):
    .venv/Scripts/python.exe scripts/run_close_bet.py --date 20260615
    .venv/Scripts/python.exe scripts/run_close_bet.py --dry-run false --kiwoom-env paper
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import duckdb
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
DEFAULT_WATCHLIST_DB = Path(__file__).resolve().parents[1] / "db" / "watchlist.duckdb"
TOKEN_CACHE_PATH = ROOT / "broker" / ".token_cache.json"

_HOSTS = {
    "paper": "https://mockapi.kiwoom.com",
    "real": "https://api.kiwoom.com",
}

EP_STKINFO = "/api/dostk/stkinfo"   # ka10001
EP_ORDR    = "/api/dostk/ordr"      # kt10000
TR_QUOTE   = "ka10001"
TR_BUY     = "kt10000"

REQUEST_TIMEOUT   = 15
_TOKEN_REFRESH_SKEW = 600


# ── DDL ──────────────────────────────────────────────────────────────────────

def create_close_bet_orders_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS close_bet_orders (
            date        VARCHAR,
            ticker      VARCHAR,
            score       INTEGER,
            qty         INTEGER,
            order_type  VARCHAR,
            status      VARCHAR,
            order_no    VARCHAR,
            message     TEXT,
            raw         TEXT,
            created_at  TIMESTAMP,
            cntr_price  INTEGER,
            cntr_qty    INTEGER,
            verified_at TIMESTAMP,
            PRIMARY KEY (date, ticker)
        )
    """)


# ── DB 조회/저장 ──────────────────────────────────────────────────────────────

def check_precondition(watchlist_db: Path, date: str) -> int:
    """오늘 llm_scores 행 수 반환. 0이면 scoring 배치 미실행."""
    with duckdb.connect(str(watchlist_db), read_only=True) as con:
        return con.execute(
            "SELECT COUNT(*) FROM llm_scores WHERE date=?", [date]
        ).fetchone()[0]


def load_order_candidates(
    watchlist_db: Path,
    date: str,
    score_threshold: int,
    max_order_count: int,
) -> list[dict]:
    """score >= threshold 종목을 score DESC / max_order_count 제한으로 반환.

    이미 close_bet_orders에 (date, ticker) 행이 있는 종목은 제외한다.
    """
    with duckdb.connect(str(watchlist_db), read_only=True) as con:
        # close_bet_orders가 없을 수도 있으므로 LEFT JOIN 전에 테이블 존재 확인
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name='close_bet_orders'"
        ).fetchall()}
        if "close_bet_orders" in tables:
            rows = con.execute("""
                SELECT l.ticker, l.score, l.name, l.close
                FROM llm_scores l
                LEFT JOIN close_bet_orders c
                    ON l.date = c.date AND l.ticker = c.ticker
                WHERE l.date = ?
                  AND l.score >= ?
                  AND c.ticker IS NULL
                ORDER BY l.score DESC
                LIMIT ?
            """, [date, score_threshold, max_order_count]).fetchall()
        else:
            rows = con.execute("""
                SELECT ticker, score, name, close
                FROM llm_scores
                WHERE date = ? AND score >= ?
                ORDER BY score DESC
                LIMIT ?
            """, [date, score_threshold, max_order_count]).fetchall()
    return [
        {"ticker": r[0], "score": r[1], "name": r[2], "close": r[3]}
        for r in rows
    ]


def upsert_order_result(watchlist_db: Path, row: dict) -> None:
    """close_bet_orders에 주문 결과 삽입. (date, ticker) PK 중복 시 기존 행 유지."""
    with duckdb.connect(str(watchlist_db)) as con:
        create_close_bet_orders_table(con)
        existing = con.execute(
            "SELECT COUNT(*) FROM close_bet_orders WHERE date=? AND ticker=?",
            [row["date"], row["ticker"]],
        ).fetchone()[0]
        if existing:
            return
        con.execute(
            """INSERT INTO close_bet_orders
               (date, ticker, score, qty, order_type, status, order_no, message, raw, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            [
                row["date"], row["ticker"], row["score"], row["qty"],
                row["order_type"], row["status"], row["order_no"],
                row["message"], row["raw"],
            ],
        )


# ── 시각 유틸 ─────────────────────────────────────────────────────────────────

def _now_seoul() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))


def _parse_hms(hms: str) -> tuple[int, int, int]:
    h, m, s = (int(x) for x in hms.split(":"))
    return h, m, s


def _is_in_order_window(order_time: str, order_deadline_time: str, allow_outside: bool) -> bool:
    if allow_outside:
        return True
    now = _now_seoul()
    h1, m1, s1 = _parse_hms(order_time)
    h2, m2, s2 = _parse_hms(order_deadline_time)
    start = now.replace(hour=h1, minute=m1, second=s1, microsecond=0)
    end   = now.replace(hour=h2, minute=m2, second=s2, microsecond=0)
    return start <= now < end


# ── 키움 API ──────────────────────────────────────────────────────────────────

def _kiwoom_env() -> str:
    return os.getenv("KIWOOM_ENV", "paper").strip().lower()


def _load_keys() -> tuple[str, str]:
    appkey = (os.getenv("KIWOOM_APPKEY") or os.getenv("KIWOON_MOCK_TR_APP_KEY") or "").strip()
    secret = (os.getenv("KIWOOM_SECRETKEY") or os.getenv("KIWOON_MOCK_TR_APP_SECRET") or "").strip()
    if not appkey or not secret:
        raise RuntimeError("키움 앱키 없음 — .env의 KIWOOM_APPKEY 확인")
    return appkey, secret


def get_token(host: str, env: str) -> str:
    if TOKEN_CACHE_PATH.exists():
        try:
            data = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
            if (
                data.get("env") == env
                and data.get("token")
                and float(data.get("expires_at", 0)) - _TOKEN_REFRESH_SKEW > time.time()
            ):
                return data["token"]
        except (ValueError, OSError):
            pass
    appkey, secret = _load_keys()
    resp = requests.post(
        f"{host}/oauth2/token",
        json={"grant_type": "client_credentials", "appkey": appkey, "secretkey": secret},
        headers={"content-type": "application/json;charset=UTF-8"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RuntimeError(f"키움 토큰 발급 실패: {data}")
    expires_at = time.time() + 12 * 3600
    raw = str(data.get("expires_dt") or "").strip()
    if len(raw) == 14 and raw.isdigit():
        try:
            expires_at = datetime.strptime(raw, "%Y%m%d%H%M%S").timestamp()
        except ValueError:
            pass
    try:
        TOKEN_CACHE_PATH.write_text(
            json.dumps({"env": env, "token": token, "expires_at": expires_at}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return token


def fetch_current_price(token: str, host: str, ticker: str, retries: int = 3) -> int | None:
    """ka10001로 cur_prc 조회. 실패 또는 0이면 None. 429는 백오프 재시도."""
    for attempt in range(retries + 1):
        resp = requests.post(
            f"{host}{EP_STKINFO}",
            json={"stk_cd": ticker},
            headers={
                "content-type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token}",
                "api-id": TR_QUOTE,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 429 and attempt < retries:
            time.sleep(1.0 * (attempt + 1))
            continue
        break
    resp.raise_for_status()
    data = resp.json()
    if data.get("return_code") not in (None, 0, "0"):
        return None
    raw = str(data.get("cur_prc", "0")).replace("+", "").replace(",", "").strip()
    try:
        price = abs(int(raw))
        return price if price > 0 else None
    except (ValueError, TypeError):
        return None


def place_market_order(token: str, host: str, ticker: str, qty: int, dry_run: bool) -> dict:
    """kt10000 시장가 매수. dry_run=True면 실제 전송 없이 dict 반환."""
    if dry_run:
        return {"order_no": "DRY_RUN", "status": "dry_run", "message": "dry_run — 실제 주문 없음"}
    resp = requests.post(
        f"{host}{EP_ORDR}",
        json={
            "dmst_stex_tp": "KRX",
            "stk_cd": ticker,
            "ord_qty": str(qty),
            "ord_uv": "",
            "trde_tp": "3",   # 시장가
            "cond_uv": "",
        },
        headers={
            "content-type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "api-id": TR_BUY,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    code = data.get("return_code")
    if code not in (None, 0, "0"):
        return {
            "order_no": "",
            "status": "failed",
            "message": f"return_code={code}: {data.get('return_msg', '')}",
        }
    return {
        "order_no": str(data.get("ord_no", "")),
        "status": "submitted",
        "message": "",
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv(ENV_PATH)

    parser = argparse.ArgumentParser(description="종가배팅 주문 배치")
    parser.add_argument("--date", help="대상 날짜 YYYYMMDD; 기본: 오늘(Asia/Seoul)")
    parser.add_argument("--score-threshold", type=int, default=80)
    parser.add_argument("--max-order-count", type=int, default=5)
    parser.add_argument("--qty-per-symbol", type=int, default=1)
    parser.add_argument("--order-time", default="15:19:00")
    parser.add_argument("--order-deadline-time", default="15:20:00")
    parser.add_argument("--allow-order-outside-close-window", action="store_true")
    parser.add_argument("--dry-run", default="true")
    parser.add_argument("--kiwoom-env", default=None)
    args = parser.parse_args()

    dry_run = args.dry_run.lower() not in ("false", "0", "no")
    date = args.date or _now_seoul().strftime("%Y%m%d")
    kiwoom_env = args.kiwoom_env or _kiwoom_env()
    watchlist_db = DEFAULT_WATCHLIST_DB

    # precondition
    cnt = check_precondition(watchlist_db, date)
    if cnt == 0:
        print(f"[close_bet] ABORT: {date} llm_scores 없음 — scoring 배치 미실행")
        sys.exit(1)
    print(f"[close_bet] precondition OK: llm_scores={cnt}건")

    # 시간창
    if not _is_in_order_window(args.order_time, args.order_deadline_time, args.allow_order_outside_close_window):
        now_str = _now_seoul().strftime("%H:%M:%S")
        print(f"[close_bet] ABORT: 주문 시간창 밖 (now={now_str}, window={args.order_time}~{args.order_deadline_time})")
        sys.exit(1)

    candidates = load_order_candidates(
        watchlist_db, date, args.score_threshold, args.max_order_count
    )
    print(f"[close_bet] 주문 후보: {len(candidates)}건 (threshold={args.score_threshold}, max={args.max_order_count})")

    if not candidates:
        print(f"[close_bet] 주문 대상 없음")
        return

    host = _HOSTS.get(kiwoom_env, _HOSTS["paper"])
    token = get_token(host, kiwoom_env)

    submitted = skipped = failed = 0
    for cand in candidates:
        if not _is_in_order_window(args.order_time, args.order_deadline_time, args.allow_order_outside_close_window):
            print(f"[close_bet] 마감 시각 초과 — 남은 종목 중단")
            break

        ticker = cand["ticker"]
        score  = cand["score"]

        cur_prc = fetch_current_price(token, host, ticker)
        if cur_prc is None:
            print(f"[close_bet] {ticker}: 현재가 조회 실패 — skip")
            upsert_order_result(watchlist_db, {
                "date": date, "ticker": ticker, "score": score,
                "qty": args.qty_per_symbol, "order_type": "market",
                "status": "skipped", "order_no": "",
                "message": "현재가 조회 실패", "raw": "{}",
            })
            skipped += 1
            continue

        result = place_market_order(token, host, ticker, args.qty_per_symbol, dry_run)
        upsert_order_result(watchlist_db, {
            "date": date, "ticker": ticker, "score": score,
            "qty": args.qty_per_symbol, "order_type": "market",
            "status": result["status"], "order_no": result["order_no"],
            "message": result["message"], "raw": json.dumps(result),
        })
        print(f"[close_bet] {ticker} score={score} cur={cur_prc} → {result['status']} ord={result['order_no']}")

        if result["status"] == "submitted":
            submitted += 1
        elif result["status"] == "dry_run":
            submitted += 1
        else:
            failed += 1

        time.sleep(float(os.getenv("ORDER_INTERVAL_SEC", "0.5")))

    print(f"[close_bet] 완료: submitted={submitted} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    main()
