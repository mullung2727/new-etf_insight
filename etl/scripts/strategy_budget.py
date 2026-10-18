import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401

"""전략 예산 조회·리셋 도구 (PLAN_STRATEGY_BUDGET D4).

같은 계좌(:8001) 전략마다 config `capital`(시드)과 `capital_since`(이 날짜부터
손익 반영)를 둔다. 각 전략이 매매 배치에서 `예산 = capital + since 이후 손익` 을
계산하고 안 쓴 몫을 예약 파일에 쓴다. 이 도구는 사람이(스킬로) 예산을 보고,
시드를 다시 정하는(리셋) 용도다. 리셋은 예약 파일을 건드리지 않는다.
예산은 각 전략 배치가 마지막으로 계산한 값(예약 파일) — 리셋 직후엔 다음 배치 전까지 옛 값.

Usage (from etl/):
    uv run python scripts/strategy_budget.py status
    uv run python scripts/strategy_budget.py reset rebound=6000000 envelope=1000000 [--note "..."]
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import requests

from check_krx_trading_day import is_krx_trading_day
from trading_batch_common import now_seoul, read_reservations
from wl_sqlite import connect_ro, connect_rw
from new_etf_insight.data_paths import db_dir

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

REPO_ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = {
    "msig":       ("http://127.0.0.1:8001", "research/private/afa/live/config.json"),
    "rights_dip": ("http://127.0.0.1:8001", "research/private/rights_issue/live/config.json"),
    "rebound":    ("http://127.0.0.1:8001", "research/private/intraday_rebound/live/config.json"),
    "envelope":   ("http://127.0.0.1:8001", "research/private/envelope/live/config.json"),
    "jevq":       ("http://127.0.0.1:8001", "research/private/jev_quant_live/config.json"),
}
DB = db_dir() / "watchlist.sqlite3"

REQUEST_TIMEOUT = 15
_BLOCK_START_SEC = 8 * 3600 + 20 * 60  # 08:20
_BLOCK_END_SEC = 15 * 3600 + 35 * 60  # 15:35

_RESET_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS strategy_budget_resets("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, strategy TEXT, "
    "old_capital INTEGER, new_capital INTEGER, since TEXT, note TEXT, created_at TEXT)"
)

_ASSIGN_RE = re.compile(r"([A-Za-z0-9_]+)=(\d+)")


def parse_assignments(items: list[str]) -> dict[str, int]:
    """`이름=금액` 목록 → {전략: 금액}. 이름 미등록·금액 비정수·음수 → ValueError."""
    result: dict[str, int] = {}
    for item in items:
        text = item.strip()
        match = _ASSIGN_RE.fullmatch(text)
        if not match or match.group(1) not in STRATEGIES:
            raise ValueError(
                f"잘못된 지정: {item!r} (형식 이름=0이상정수, 전략: {','.join(STRATEGIES)})",
            )
        result[match.group(1)] = int(match.group(2))
    if not result:
        raise ValueError("지정이 비어 있음 (이름=금액 1개 이상 필요)")
    return result


def _day_seconds(now) -> int:
    return now.hour * 3600 + now.minute * 60 + now.second


def blocked_now(now, is_trading: bool) -> bool:
    """장중(거래일 08:20 ≤ 시각 < 15:35)이면 True."""
    if not is_trading:
        return False
    return _BLOCK_START_SEC <= _day_seconds(now) < _BLOCK_END_SEC


def pick_since(now, is_trading_fn) -> str:
    """리셋 효력일(YYYYMMDD). 08:20 전+오늘 거래일 → 오늘, 그 외 → 내일 이후 첫 거래일.

    15일 안에 못 찾으면 RuntimeError (호출부는 exit 2).
    """
    today = now.strftime("%Y%m%d")
    if _day_seconds(now) < _BLOCK_START_SEC and is_trading_fn(today):
        return today
    base = now.date()
    for delta in range(1, 16):
        candidate = (base + timedelta(days=delta)).strftime("%Y%m%d")
        if is_trading_fn(candidate):
            return candidate
    raise RuntimeError("향후 15일 내 거래일 없음 — since 결정 실패")


def _resolve_config(rel: str | Path) -> Path:
    path = Path(rel)
    return path if path.is_absolute() else REPO_ROOT / rel


def write_config(path: str | Path, capital: int, since: str) -> int:
    """전략 config 원자적 갱신 → 이전 capital 반환. 다른 키 보존, 임시파일 잔류 없음."""
    cfg_path = Path(path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    try:
        old = int(data.get("capital", 0))
    except (ValueError, TypeError):
        old = 0
    data["capital"] = int(capital)
    data["capital_since"] = since
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(cfg_path.parent),
            prefix=cfg_path.name + ".", delete=False,
        ) as handle:
            tmp = handle.name
            handle.write(payload)
        os.replace(tmp, cfg_path)
    except BaseException:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    return old


def _restore(path: str | Path, text: str) -> None:
    """원문 텍스트로 config 원자적 복구 (임시파일+os.replace)."""
    cfg_path = Path(path)
    tmp: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(cfg_path.parent),
            prefix=cfg_path.name + ".", delete=False,
        ) as handle:
            tmp = handle.name
            handle.write(text)
        os.replace(tmp, cfg_path)
    except BaseException:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def _rollback(originals: list[tuple[Path, str]]) -> str | None:
    """쓴 config들을 원문으로 되돌린다. 전부 성공 시 None, 실패 시 실패 경로."""
    failed: str | None = None
    for cfg_path, text in reversed(originals):
        try:
            _restore(cfg_path, text)
        except OSError as error:
            print(f"⚠️ 되돌리기 실패 — 수동 확인: {cfg_path} ({error})")
            if failed is None:
                failed = str(cfg_path)
    return failed


def record_reset(db: str | Path, rows) -> None:
    """리셋 이력 1건씩 적재. 행은 dict(date/strategy/old_capital/new_capital/since/note/created_at) 또는 동순 7-tuple."""
    normalized = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append((
                row.get("date"), row.get("strategy"), row.get("old_capital"),
                row.get("new_capital"), row.get("since"), row.get("note", ""),
                row.get("created_at"),
            ))
        else:
            normalized.append(tuple(row))
    db_path = Path(db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_rw(db_path) as con:
        con.execute(_RESET_SCHEMA)
        con.executemany(
            "INSERT INTO strategy_budget_resets"
            "(date, strategy, old_capital, new_capital, since, note, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            normalized,
        )


def fetch_total_assets(url: str) -> int | None:
    """GET {url}/account/balance → prsm_dpst_aset_amt. 실패 시 None."""
    try:
        response = requests.get(f"{url}/account/balance", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        value = response.json().get("prsm_dpst_aset_amt")
        if value is None:
            return None
        return int(str(value).replace(",", "").strip())
    except Exception:
        return None


def _extra_sum(config: dict | None) -> int:
    """rights_dip config `extra` dict 각 값의 `amount` 합."""
    extra = (config or {}).get("extra")
    if not isinstance(extra, dict):
        return 0
    total = 0
    for value in extra.values():
        if isinstance(value, dict):
            try:
                total += int(value.get("amount", 0))
            except (ValueError, TypeError):
                pass
    return total


def _fmt_num(value) -> str:
    """숫자면 천 단위 콤마, 아니면 원값 그대로 문자열로."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    try:
        return f"{int(str(value).replace(',', '').strip()):,}"
    except (ValueError, TypeError, AttributeError):
        return str(value)


def status_lines(strategies=None, balance_fn=None, db_path=None) -> list[str]:
    """status 출력 한 줄씩. 전략별 capital/since/extra/예약 → 계좌별 총자산·시드합 → 최근 리셋 5건."""
    strategies = STRATEGIES if strategies is None else strategies
    balance_fn = fetch_total_assets if balance_fn is None else balance_fn
    db = DB if db_path is None else Path(db_path)
    lines: list[str] = []
    per_account: dict[str, dict[str, int]] = {}
    for name, entry in strategies.items():
        url, rel = entry
        cfg_path = _resolve_config(rel)
        try:
            config = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = None
        if config is None:
            lines.append(f"{name}: config 없음 ({cfg_path}) · 예약 없음")
            continue
        capital = config.get("capital")
        since = config.get("capital_since")
        line = f"{name}: 시드 {_fmt_num(capital)} (since {since})"
        extra = _extra_sum(config) if name == "rights_dip" else 0
        if name == "rights_dip":
            line += f" · extra {_fmt_num(extra)}"
        try:
            all_reservations = read_reservations(url) or {}
        except Exception:
            all_reservations = {}
        slot = all_reservations.get(name)
        if isinstance(slot, dict) and "reserve" in slot:
            line += (
                f" · 예산 {_fmt_num(slot.get('capital_total'))}"
                f" · 안 쓴 몫 {_fmt_num(slot.get('reserve'))}"
                f" (마지막 배치 {slot.get('updated')})"
            )
        else:
            line += " · 예산 미기록 (다음 배치 후 표시)"
        lines.append(line)
        account = per_account.setdefault(url, {"seed": 0, "extra": 0})
        try:
            account["seed"] += int(capital)
        except (ValueError, TypeError):
            pass
        account["extra"] += extra
    for url, account in per_account.items():
        total = balance_fn(url)
        seed_total = account["seed"] + account["extra"]
        lines.append(
            f"[계좌 {url}] 총자산={total} "
            f"시드합={seed_total}(capital {account['seed']}+extra {account['extra']})",
        )
        if total is not None and seed_total > total:
            lines.append("⚠️ 전략 시드 합이 총자산 초과")
    history = None
    if Path(db).exists():
        try:
            with connect_ro(db) as con:
                try:
                    history = con.execute(
                        "SELECT date, strategy, old_capital, new_capital, since, note "
                        "FROM strategy_budget_resets ORDER BY id DESC LIMIT 5",
                    ).fetchall()
                except sqlite3.Error:
                    history = None
        except sqlite3.Error:
            history = None
    if history:
        lines.append("[리셋 이력 최근 5건]")
        for date, strategy, old, new, since, note in history:
            lines.append(f"{date} {strategy} {old}->{new} since={since}{f' {note}' if note else ''}")
    return lines


def _cmd_reset(assignments: list[str], note: str) -> int:
    try:
        mapping = parse_assignments(assignments)
    except ValueError as error:
        print(f"인자 오류: {error}")
        return 2
    now = now_seoul()
    today = now.strftime("%Y%m%d")
    if blocked_now(now, is_krx_trading_day(today)):
        print("장중 리셋 금지 (거래일 08:20~15:35)")
        return 2
    try:
        since = pick_since(now, is_krx_trading_day)
    except RuntimeError as error:
        print(str(error))
        return 2
    paths = {name: _resolve_config(STRATEGIES[name][1]) for name in mapping}
    originals: dict[str, str] = {}
    for name, cfg_path in paths.items():
        try:
            raw = cfg_path.read_text(encoding="utf-8")
            json.loads(raw)
        except (OSError, ValueError) as error:
            print(f"{name} config 읽기 실패 ({cfg_path}: {error})")
            return 2
        originals[name] = raw
    rows = []
    written: list[tuple[Path, str]] = []
    for name, amount in mapping.items():
        cfg_path = paths[name]
        try:
            old = write_config(cfg_path, amount, since)
        except (OSError, ValueError) as error:
            print(f"{name} config 저장 실패 ({cfg_path}: {error})")
            _rollback(written)
            return 2
        written.append((cfg_path, originals[name]))
        rows.append({
            "date": today, "strategy": name, "old_capital": old,
            "new_capital": amount, "since": since, "note": note or "",
            "created_at": now.isoformat(),
        })
    try:
        record_reset(DB, rows)
    except sqlite3.Error as error:
        print(f"이력 저장 실패: {error}")
        _rollback(written)
        return 2
    print(f"리셋 완료: since={since} ({', '.join(f'{k}={v}' for k, v in mapping.items())})")
    print("예약은 각 전략 다음 배치에서 갱신")
    for line in status_lines():
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="전략 예산 조회·리셋")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="전략별 예산·예약·총자산 조회")
    reset_parser = sub.add_parser("reset", help="전략 시드 리셋")
    reset_parser.add_argument("assignments", nargs="+", help="이름=금액 1개 이상")
    reset_parser.add_argument("--note", default="", help="리셋 사유 메모")
    args = parser.parse_args(argv)
    if args.cmd == "status":
        for line in status_lines():
            print(line)
        return 0
    return _cmd_reset(args.assignments, args.note)


if __name__ == "__main__":
    raise SystemExit(main())
