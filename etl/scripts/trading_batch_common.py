"""자동매매 배치 공용 broker REST·주문시간·수량 유틸리티."""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from new_etf_insight.data_paths import db_dir


REQUEST_TIMEOUT = 15

# 전략별 자금 예약. {"host:port": {"strategy": {"reserve", "capital_total", ...}}}.
# 각 전략 order·verify 가 자기 항목 갱신, available_cash 는 남의 reserve 합 차감. 파일 없으면 기존 동작 그대로.
RESERVATION_PATH = db_dir() / "cash_reservations.json"

# 청산이 끝난 것으로 간주하는 sell_status. 청산 워커의 미청산 조회와 주문 배치의
# 중복매수 가드가 같은 집합을 봐야 한다 — 한쪽만 알면 유령 포지션이 매수를 영구 차단한다.
#   filled  = 실제 매도 체결
#   missing = 매수 기록은 있으나 계좌 잔고에 없음(모의계좌 리셋 등). 매도할 물량이
#             없으므로 청산 불가 — 조용히 건너뛰지 말고 종료로 확정한다.
CLOSED_SELL_STATUSES = ("filled", "missing")


def now_seoul() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))


def in_order_window(now: datetime, start: str, deadline: str) -> bool:
    def at(value: str) -> datetime:
        hour, minute, second = (int(part) for part in value.split(":"))
        return now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    return at(start) <= now < at(deadline)


def current_price(broker_url: str, ticker: str) -> int | None:
    try:
        response = requests.get(f"{broker_url}/quotes/{ticker}", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        value = response.json().get("price")
        return int(value) if value is not None else None
    except Exception:
        return None


def quote_snapshot(broker_url: str, ticker: str) -> dict[str, int] | None:
    try:
        response = requests.get(f"{broker_url}/quotes/{ticker}", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        raw = data.get("raw") or {}
        price = data.get("price")
        day_open = raw.get("open_pric")
        day_low = raw.get("low_pric")
        if any(value is None for value in (price, day_open, day_low)):
            return None
        return {"current_price": abs(int(price)), "open": abs(int(day_open)), "low": abs(int(day_low))}
    except Exception:
        return None


def require_profile(broker_url: str, expected: str) -> bool:
    """broker ``/health`` 의 계좌 구분(profile)이 expected 인가.

    계좌마다 broker 를 따로 띄우므로, 러너가 다른 계좌 broker 에 주문하지 않게 주문 전에 확인한다.
    조회 실패도 False — 어느 계좌인지 모르면 주문하지 않는다.
    """
    try:
        response = requests.get(f"{broker_url}/health", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json().get("profile") == expected
    except Exception as error:
        print(f"[profile] /health 조회 실패: {error}")
        return False


def _broker_key(url: str) -> str:
    """broker_url → 예약 키 (host:port). localhost ≡ 127.0.0.1."""
    try:
        parts = urlsplit(str(url))
        host = (parts.hostname or "").lower()
        if host == "localhost":
            host = "127.0.0.1"
        port = parts.port
        return f"{host}:{port}" if port else host
    except Exception:
        return str(url)


def _load_reservation_file() -> tuple[dict | None, bool]:
    """예약 파일 전체 → (dict | None, 깨짐 여부). 파일 없음 → (None, False)."""
    try:
        raw = RESERVATION_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, False
    except OSError as error:
        print(f"[reserve] 예약 파일 읽기 실패 — fail closed ({RESERVATION_PATH}: {error})")
        return None, True
    try:
        data = json.loads(raw)
    except ValueError as error:
        print(f"[reserve] 예약 파일 깨짐 — fail closed ({RESERVATION_PATH}: {error})")
        return None, True
    if not isinstance(data, dict):
        print(f"[reserve] 예약 파일 형식 오류 — fail closed ({RESERVATION_PATH})")
        return None, True
    return data, False


def _valid_amount(value: object) -> bool:
    """예약 금액 검증 — 0 이상 정수, bool 제외."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _account_entries(account_value: object) -> dict[str, dict] | None:
    """계좌 값 → {전략: 항목}. 계좌 키 없음(None) → {}. 모양이 이상하면 None (fail closed)."""
    if account_value is None:
        return {}
    if not isinstance(account_value, dict):
        return None
    if "reserve" in account_value:
        name = account_value.get("strategy") or "rights_dip"
        return {name: account_value}
    for value in account_value.values():
        if not isinstance(value, dict):
            return None
    return dict(account_value)


@contextmanager
def _reservation_lock():
    """예약 파일 읽기→수정→교체를 직렬화하는 프로세스 간 잠금.

    잠금 파일은 RESERVATION_PATH 옆 ``cash_reservations.json.lock`` —
    매번 RESERVATION_PATH 로부터 계산하므로 테스트의 임시 경로 패치가 그대로 적용된다.
    """
    lock_path = RESERVATION_PATH.with_name(RESERVATION_PATH.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def read_reservation(broker_url: str, strategy: str = "rights_dip") -> dict | None:
    """그 전략 예약 1건(기본 rights_dip). 파일 없음·읽기 실패·깨짐·키 없음 → None."""
    data, _ = _load_reservation_file()
    if data is None:
        return None
    entries = _account_entries(data.get(_broker_key(broker_url)))
    if entries is None:
        print(f"[reserve] 예약 파일 형식 오류 — fail closed ({RESERVATION_PATH})")
        return None
    entry = entries.get(strategy)
    return entry if isinstance(entry, dict) else None


def read_reservations(broker_url: str) -> dict[str, dict]:
    """그 계좌의 전략별 예약 전부. 파일 없음·깨짐·키 없음 → {}."""
    data, _ = _load_reservation_file()
    if data is None:
        return {}
    entries = _account_entries(data.get(_broker_key(broker_url)))
    if entries is None:
        print(f"[reserve] 예약 파일 형식 오류 — fail closed ({RESERVATION_PATH})")
        return {}
    return entries


def available_cash(broker_url: str, exclude_reserve: bool = True, strategy: str | None = None) -> int | None:
    """주문가능금액 — exclude_reserve 면 자기 제외 다른 전략 reserve 합 차감.
    strategy=None 이면 전부 차감(보수적). 파일 없음·계좌 키 없음 → 원값.
    파일이 깨지거나 차감 대상 예약값이 이상하면 0 (fail closed, A-10)."""
    cash = _deposit_cash(broker_url)
    if cash is None or not exclude_reserve:
        return cash
    data, corrupt = _load_reservation_file()
    if corrupt:
        return 0
    if data is None:
        return cash
    entries = _account_entries(data.get(_broker_key(broker_url)))
    if entries is None:
        print(f"[reserve] 예약 파일 형식 오류 — fail closed ({RESERVATION_PATH})")
        return 0
    if not entries:
        return cash
    total = 0
    others = {k: v for k, v in entries.items() if k != strategy}

    for item in others.values():
        reserve = item.get("reserve")
        capital_total = item.get("capital_total")
        if not _valid_amount(reserve) or not _valid_amount(capital_total):
            print(f"[reserve] 예약값 오류 — fail closed ({RESERVATION_PATH})")
            return 0
        total += reserve
    return max(0, cash - total)


def _deposit_cash(broker_url: str) -> int | None:
    """GET /account/deposit → ord_alow_amt. 실패 시 None."""
    try:
        response = requests.get(f"{broker_url}/account/deposit", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        value = response.json().get("ord_alow_amt")
        return int(value) if value is not None else None
    except Exception:
        return None


def write_reservation(broker_url: str, reserve: int, capital_total: int, strategy: str) -> None:
    """예약 1건 저장 — tmp → os.replace 원자적, 다른 계좌 키 보존. 호출부는 P3(b).

    기존 파일이 있는데 읽기 실패·JSON 깨짐·dict 아님 → 덮어쓰지 않고 RuntimeError.
    reserve·capital_total 이 0 이상 정수(bool 제외) 아님 → ValueError.
    """
    if not _valid_amount(reserve) or not _valid_amount(capital_total):
        raise ValueError(f"예약값 오류: reserve={reserve!r} capital_total={capital_total!r}")
    with _reservation_lock():
        try:
            raw = RESERVATION_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            data = {}
        except OSError as error:
            raise RuntimeError(
                f"예약 파일 읽기 실패 — 덮어쓰지 않음 ({RESERVATION_PATH}: {error})") from error
        else:
            try:
                data = json.loads(raw)
            except ValueError as error:
                raise RuntimeError(
                    f"예약 파일 깨짐 — 덮어쓰지 않음 ({RESERVATION_PATH}: {error})") from error
            if not isinstance(data, dict):
                raise RuntimeError(f"예약 파일 형식 오류 — 덮어쓰지 않음 ({RESERVATION_PATH})")
        entries = _account_entries(data.get(_broker_key(broker_url)))
        if entries is None:
            raise RuntimeError(f"예약 파일 형식 오류 — 덮어쓰지 않음 ({RESERVATION_PATH})")
        entries[strategy] = {"reserve": reserve, "capital_total": capital_total,
            "strategy": strategy, "updated": now_seoul().isoformat()}
        data[_broker_key(broker_url)] = entries
        RESERVATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 여러 전략이 같은 시각에 쓸 수 있다 — 읽기~교체를 잠금 파일로 직렬화
        tmp: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=str(RESERVATION_PATH.parent),
                prefix=RESERVATION_PATH.name + ".", delete=False,
            ) as handle:
                tmp = handle.name
                handle.write(json.dumps(data, ensure_ascii=False))
            os.replace(tmp, RESERVATION_PATH)
        except BaseException:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise


def fetch_realized(broker_url: str, ticker: str) -> dict | None:
    """GET /orders/realized/{ticker} → net 실현손익(키움 권위값). 실패/미발견 시 None.

    당일 매도 체결 후 호출해 수수료·세금 차감된 pnl_pct·손익금을 받는다.
    키움 pnl_pct는 %(예: -4.84) — 분수 규약 DB에 넣을 땐 호출부에서 /100.
    """
    try:
        response = requests.get(
            f"{broker_url}/orders/realized/{ticker}", timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return data if data.get("found") else None
    except Exception as error:
        print(f"[realized] /orders/realized 조회 실패({ticker}): {error}")
        return None


def market_order(
    broker_url: str, ticker: str, qty: int, side: str, source: str, dry_run: bool,
    *, now: datetime | None = None,
) -> dict:
    if dry_run:
        timestamp = (now or now_seoul()).strftime("%Y%m%d%H%M%S")
        return {"order_no": f"DRY_{ticker}_{timestamp}", "status": "dry_run",
                "message": "dry_run — 실제 주문 없음"}
    try:
        response = requests.post(
            f"{broker_url}/orders/strategy",
            json={"symbol": ticker, "side": side, "qty": qty,
                  "order_type": "market", "source": source},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 422:
            detail = response.json().get("detail", "rejected")
            return {"order_no": "", "status": "rejected", "message": detail}
        response.raise_for_status()
        data = response.json()
        if not data.get("accepted"):
            return {"order_no": "", "status": "failed",
                    "message": data.get("message", "accepted=False")}
        return {"order_no": str(data.get("order_no") or ""), "status": "submitted",
                "message": data.get("message", "")}
    except Exception as error:
        return {"order_no": "", "status": "failed", "message": str(error)}


def quantity_for_budget(budget: int, price: int) -> int:
    return budget // price if budget > 0 and price > 0 else 0


def _strip_ticker(code: object) -> str:
    """키움 잔고의 ``A`` + 6자리 종목코드 접두를 벗긴다."""
    text = str(code).strip()
    return text[1:] if len(text) == 7 and text[:1] == "A" else text


def _padint(value: object) -> int:
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0


def held_quantities(balance: dict) -> dict[str, int] | None:
    """잔고 응답 → {종목코드: 매도가능수량}.

    조회 실패·응답 이상이면 None. 이걸 빈 dict로 뭉개면 호출부가 '전 종목 미보유'로
    읽어서 멀쩡한 포지션까지 유령으로 마감해버린다. 보유 0건인 정상 응답({})과
    반드시 구분해야 한다.
    """
    if not isinstance(balance, dict):
        return None
    rows = balance.get("acnt_evlt_remn_indv_tot")
    if not isinstance(rows, list):
        return None
    return {_strip_ticker(r.get("stk_cd", "")): _padint(r.get("trde_able_qty")) for r in rows}


def run_live_batch(
    tag: str,
    body: Callable[[], dict | None],
    *,
    broker_url: str | None,
    profile: str | None,
    notify_fn: Callable[[str], bool] | None = None,
    trading_day: bool = True,
    today: str | None = None,
) -> int:
    """실매매 배치 공용 시작 절차: 거래일 확인 → 계좌 확인 → body → 결과 알림.

    종료 코드: 0 정상(휴장일 포함), 1 중단(계좌 불일치·body 실패·body가 nonzero 반환),
    2 body 실패 + 알림 성공(러너는 중복 알림 생략 — run_swing_pick.py와 같은 규약).
    """
    try:
        from scripts.check_krx_trading_day import is_krx_trading_day
    except ImportError:  # etl/scripts 직접 실행 시
        from check_krx_trading_day import is_krx_trading_day  # noqa: E402
    try:
        from scripts.notify import notify
    except ImportError:  # etl/scripts 직접 실행 시
        from notify import notify  # noqa: E402
    import traceback

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if today is None:
        today = now_seoul().strftime("%Y%m%d")
    if notify_fn is None:
        def notify_fn(message: str) -> bool:
            return notify(message, channel="batch")
    if trading_day and not is_krx_trading_day(today):
        print(f"{tag} {today} 거래일 아님 — 종료")
        return 0
    if broker_url and not require_profile(broker_url, profile):
        try:
            notify_fn(f"{tag} {today} {broker_url} 계좌 확인 실패 — 중단")
        except Exception as error:
            print(f"[{tag}] 계좌 확인 실패 알림 실패: {error}")
        return 1
    try:
        res = body()
    except Exception as error:
        traceback.print_exc()
        try:
            alerted = notify_fn(f"{tag} {today} 실패: {type(error).__name__}: {error}")
        except Exception as notify_error:
            print(f"[{tag}] 실패 알림 실패: {notify_error}")
            return 1
        return 2 if alerted else 1
    if isinstance(res, dict):
        lines = res.get("lines")
        if lines:
            try:
                notify_fn(f"{tag} {today}\n" + "\n".join(lines))
            except Exception as error:
                print(f"[{tag}] 결과 알림 실패: {error}")
        return int(res.get("code", 0))
    return 0


def classify_unknown(
    order_no: object,
    unfilled_rows: list[dict] | None,
    fill_rows: list[dict] | None,
) -> tuple[str, int]:
    """broker 결과 불명(unknown) 주문의 다음 실행 재판정 → (상태, 체결수량).

    lookup_failed = 조회 실패(완료 아님, 손대지 말 것), no_order_no = 주문번호 없음,
    open = 미체결에 살아 있음, filled = 체결 반영, not_found = 접수 안 됨(재시도 대상).
    """
    try:
        from scripts.run_verify import normalize_order_no
    except ImportError:  # etl/scripts 직접 실행 시
        from run_verify import normalize_order_no  # noqa: E402
    if unfilled_rows is None or fill_rows is None:
        return ("lookup_failed", 0)
    key = normalize_order_no(order_no)
    if not key:
        return ("no_order_no", 0)
    filled = 0
    for row in fill_rows:
        if normalize_order_no(row.get("order_no")) != key:
            continue
        filled += int(row.get("cntr_qty") or 0)
    for row in unfilled_rows:
        if normalize_order_no(row.get("order_no")) == key:
            return ("open", filled)
    if filled > 0:
        return ("filled", filled)
    return ("not_found", 0)


def retry_qty(ledger_qty: int, held_qty: int | None) -> int:
    """재주문 수량 = min(원장수량, 보유수량). 잔고 조회 실패(None)·음수는 0."""
    if held_qty is None:
        return 0
    return max(0, min(ledger_qty, held_qty))
