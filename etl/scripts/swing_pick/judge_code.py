"""스윙 후보 코드 판정: 1차 컷 + 3번 실적·4번 수급·5번 차트 점수 + 6번 PER 참고값.

설계: docs/done/PLAN_SWING_PICK.md §0-1(1차 컷), §0-2(점수표), §2-2(데이터 소스).
이 모듈은 코드 판정까지만 한다. 순위·Jev(1·2번)·LangGraph·DB 저장은 다음 단계라
여기서 만들지 않는다.

데이터 시점 배경 (아래 함수들의 공통 전제):
- 19:00엔 KRX DB(krx_ohlcv.duckdb)에 당일 치가 없다. KRX OpenAPI가 하루 늦게
  공개하고 다음 날 08:00 배치가 적재하기 때문이다. 그래서 거래대금 컷·PER 시총은
  D-1 값을 쓴다. 5번 차트의 오늘 종가만 broker 일괄시세로 채운다.
- 5번 오늘 종가는 broker 일괄시세(ka10095, 50종목/콜)로 러프하게 채운다.
  19:00 현재가엔 시간외(NXT) 가격이 섞일 수 있지만 정성 검토 전 1차 선별용이라
  수용한다(사용자 결정 2026-09-28). 분봉 15:30 봉은 후보당 1콜이라 부담이 커서
  쓰지 않는다.
- DART accounts.amount의 11013(1Q)·11012(2Q)·11014(3Q)는 3개월치, 11011은
  연간치다 (삼성전자 2024 영업이익: 연 32.7조 − 1~3Q 26.2조 = 4Q 6.5조, 발표치
  일치로 실측). 그래서 4Q = 연간 − 1~3Q 로 역산한다.
- ka10061 순매수 금액 단위는 스펙에 없다 (broker도 부호만 신뢰). 그래서 4번은
  금액 크기가 아니라 순매수/순매도 부호만 판정에 쓴다.
- 정리매매·관리종목·7번 테마는 여기서 안 본다. 후보 전부 조회하면 키움 호출
  부담이 크다. 순위 단계에서 상위 후보만 확인한다.
- 점수를 3단계(0/1/2)로 나누는 이유: O/X는 0.51과 0.95를 같게 취급해 정보 손실과
  동점 과다가 생긴다. 점수와 별도로 원값(영업이익·순매수액·이격·상승률)도 전부
  반환한다. 나중에 항목별 예측력을 검증하기 위해서다.

구조: 순수 함수(계산)와 I/O 함수(DB·HTTP)를 분리한다. I/O는 전부 인자로 주입
가능하게 해서 (db_path·broker_url·get) 테스트에서 가짜로 대체한다.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import requests

from scripts.trading_batch_common import REQUEST_TIMEOUT

# 1차 컷: 전일(D-1) 거래대금 하한. 19:00엔 당일 치가 KRX DB에 없으므로 D-1 기준.
MIN_TRADING_VALUE = 5_000_000_000

# 5번 차트 급등 필터 경계. 20일선 위라도 5일 +30% 이상은 따라잡기 늦었다고 보고 0점,
# 15~30%는 과열 구간이라 1점. 경계값(0.30/0.15)은 "이상" 쪽에 포함 — PLAN §0-2 표.
SURGE_HARD = 0.30
SURGE_SOFT = 0.15

# 5번 차트에 필요한 최소 D-1 종가 개수. 20일선 계산에 20개 + 5일 상승률에 5개 + 여유.
MIN_CLOSES = 25

# DART reprt_code → 분기. 11011은 연간치라 여기서 4Q를 역산한다 (위 배경 참조).
_QUARTER_CODES = {"11013": 1, "11012": 2, "11014": 3}
_ANNUAL_CODE = "11011"

# 종목마다 계정명이 다르다 ('영업이익' vs '영업이익(손실)'). 둘 다 영업이익으로 본다.
_OP_NAMES = ("영업이익", "영업이익(손실)")
_NET_NAME = "당기순이익(손실)"


def prefilter(candidates, ohlcv_prev):
    """1차 컷. (통과 목록, 제외 목록)을 반환한다.

    제외 사유는 첫 번째로 걸린 것 하나만 붙인다. 순서는 데이터 유무 → 종목 속성 →
    거래 상태 → 금액 순이다. D-1 행 자체가 없으면(no_prev_data) 뒤 조건을 볼 수
    없으니 먼저, 스팩은 금액과 무관하게 제외이므로 거래대금보다 먼저 본다.
    """
    passed = []
    cut = []
    for cand in candidates:
        ticker = cand.get("ticker")
        prev = ohlcv_prev.get(ticker)
        if prev is None:
            reason = "no_prev_data"
        elif "스팩" in str(cand.get("name", "")):
            reason = "spac"
        elif prev.get("volume") == 0:
            # 전일 거래량 0 = 거래정지. 종가·호가가 멈춘 종목은 차트 판정 불가.
            reason = "halted"
        elif (prev.get("trading_value") or 0) < MIN_TRADING_VALUE:
            reason = "low_value"
        else:
            passed.append(cand)
            continue
        cut.append({**cand, "cut_reason": reason})
    return passed, cut


def quarterly_series(rows):
    """한 종목의 accounts 행들 → {(year, q): {"op", "net"}} 분기 시계열.

    - fs_div: 그 종목에 CFS(연결) 행이 하나라도 있으면 CFS만 쓴다. 연결이 본체
      실적이라 우선이고, 없으면 OFS(별도)로 떨어진다. 둘을 섞으면 같은 분기가
      두 번 잡힌다.
    - 4분기: DART에 4Q 보고서가 따로 없고 연간치만 있다. 11011 연간 − (1+2+3Q).
      셋 중 하나라도 없으면 역산 불가라 4분기를 만들지 않는다 (추정 금지).
    - op/net은 독립 계산: 한쪽 계정만 있어도 있는 쪽은 채우고 없는 쪽은 None.
    """
    use_cfs = any(str(r.get("fs_div", "")).strip() == "CFS" for r in rows)
    fs_want = "CFS" if use_cfs else "OFS"
    quarterly = {}  # (year, q) -> {"op", "net"}
    annual = {}  # year -> {"op", "net"}
    for r in rows:
        if str(r.get("fs_div", "")).strip() != fs_want:
            continue
        try:
            year = int(str(r.get("bsns_year", "")).strip())
        except (ValueError, TypeError):
            continue  # bsns_year 파싱 불가 행은 버린다
        code = str(r.get("reprt_code", "")).strip()
        name = str(r.get("account_nm", "")).strip()
        amount = r.get("amount")
        value = None if amount is None else float(amount)
        if name in _OP_NAMES:
            key = "op"
        elif name == _NET_NAME:
            key = "net"
        else:
            continue
        if code in _QUARTER_CODES:
            quarterly.setdefault((year, _QUARTER_CODES[code]), {"op": None, "net": None})
            quarterly[(year, _QUARTER_CODES[code])][key] = value
        elif code == _ANNUAL_CODE:
            annual.setdefault(year, {"op": None, "net": None})
            annual[year][key] = value
    for year, total in annual.items():
        for key in ("op", "net"):
            parts = [quarterly.get((year, q), {}).get(key) for q in (1, 2, 3)]
            if total[key] is not None and all(p is not None for p in parts):
                quarterly.setdefault((year, 4), {"op": None, "net": None})
                quarterly[(year, 4)][key] = total[key] - sum(parts)
    return quarterly


def score_earnings(series):
    """3번 실적. 최신 분기 영업이익으로 0/1/2점. (점수|None, raw) 반환.

    최신 분기는 op가 있는 가장 최근 분기다. DART 적재 시차로 최신 보고서에
    영업이익이 비어 있을 수 있어, 키가 아니라 op 존재로 최신을 잡는다.
    전년 동기 비교가 안 되면(상장 첫해 등) 흑자라는 사실만으로 1점을 준다.
    """
    with_op = [k for k, v in series.items() if v.get("op") is not None]
    if not with_op:
        return None, {"quarter": None, "op": None, "op_yoy": None}
    year, q = max(with_op)
    op = series[(year, q)]["op"]
    yoy = series.get((year - 1, q), {}).get("op")
    raw = {"quarter": f"{year}Q{q}", "op": op, "op_yoy": yoy}
    if op <= 0:
        return 0, raw
    if yoy is None:
        return 1, raw
    return (2, raw) if op > yoy else (1, raw)


def ttm_net(series):
    """최근 연속 4분기 당기순이익 합 (6번 PER 분모). 끊기면 None.

    최신 분기부터 거꾸로 4개가 하나라도 비면 합산하지 않는다. 중간 분기를 건너뛰고
    더하면 12개월치가 아니라 의미가 깨지기 때문이다.
    """
    if not series:
        return None
    year, q = max(series.keys())
    total = 0.0
    for _ in range(4):
        net = series.get((year, q), {}).get("net")
        if net is None:
            return None
        total += net
        q -= 1
        if q == 0:
            year, q = year - 1, 4
    return total


def per_value(market_cap, ttm):
    """6번 PER 참고값. 점수 없음 — (값|None, 비고|None)만 반환.

    ttm ≤ 0이면 나눗셈이 무의미(음수 PER은 비교 불가)라 "적자"로 표시한다.
    시총은 D-1 KRX 값이다 (19:00엔 당일 시총이 DB에 없음).
    """
    if ttm is None:
        return None, "재무 부족"
    if ttm <= 0:
        return None, "적자"
    if market_cap is None:
        return None, "시총 없음"
    return market_cap / ttm, None


def supply_window(trading_days, today):
    """4번 수급 조회 구간. 오늘 포함 최근 5거래일의 (시작일, today).

    trading_days는 KRX DB의 D-1까지 거래일(오름차순)이다. DB에 오늘은 없으니
    today를 직접 붙여 5거래일을 만든다. 휴일 직후처럼 D-1까지 4개가 안 되면
    5거래일을 못 채우니 ValueError — 호출측(judge_code)이 종목별 error로 기록한다.
    """
    if len(trading_days) < 4:
        raise ValueError(f"need 4 trading days before today, got {len(trading_days)}")
    return trading_days[-4], today


def score_supply(frgn, orgn):
    """4번 수급. 외인·기관 중 순매수(>0)인 쪽 수 → 2/1/0점.

    ka10061 금액 단위가 스펙에 없어 크기 비교가 무의미하다. 부호만 본다.
    0은 순매수가 아니라 중립이므로 0점으로 센다. 둘 중 하나라도 None이면
    집계 실패로 보고 None (호출측이 error 처리).
    """
    if frgn is None or orgn is None:
        return None
    return sum(1 for v in (frgn, orgn) if v > 0)


def score_chart(closes_prev, today_price):
    """5번 차트. D-1까지 종가 + 오늘 가격으로 0/1/2점. (점수|None, raw) 반환.

    today_price는 19:00 broker 일괄시세(ka10095)다. 시간외(NXT) 가격이 섞일 수
    있지만 1차 선별용 러프값이라 수용한다 (위 배경). 없으면 error로 기록하고
    D-1 종가로 대체하지 않는다 — 어제 차트로 오늘 점수를 내면 거짓 확신이 된다.
    판정 순서: 20일선 아래면 급등 여부와 무관하게 0점 (추세 이탈이 먼저다).
    """
    raw = {"today_price": today_price, "ma20": None, "ma20_gap": None, "ret_5d": None}
    if len(closes_prev) < MIN_CLOSES:
        return None, raw
    if today_price is None or today_price <= 0:
        return None, raw
    closes = [*closes_prev, today_price]
    close = closes[-1]
    base = closes[-6]
    ma20 = sum(closes[-20:]) / 20
    raw["ma20"] = ma20
    raw["ma20_gap"] = close / ma20 - 1
    raw["ret_5d"] = close / base - 1
    if close < ma20:
        return 0, raw
    # 급등 경계는 정수 연산으로 정확히 비교한다 (SURGE_HARD=0.30, SURGE_SOFT=0.15).
    # 부동소수점으론 수학적으로 정확히 15%인 115/100-1이 0.1499999999999999가 돼
    # 1점이 아니라 2점이 나온다. 종가는 정수라 비율을 교차곱셈으로 판정한다.
    # raw의 ret_5d는 표시·검증용 float 그대로 둔다.
    if close * 10 >= base * 13:  # ret_5d >= 0.30
        return 0, raw
    if close * 20 >= base * 23:  # ret_5d >= 0.15
        return 1, raw
    return 2, raw


def load_ohlcv(db_path, tickers, prev_date, lookback=40):
    """KRX DB에서 D-1 행·종목별 종가·거래일을 읽는다. 읽기 전용 연결.

    반환: (D-1 행 {ticker: {close, volume, trading_value, market_cap}},
           종목별 종가 {ticker: [오름차순 최근 lookback개]},
           거래일 [D-1까지 오름차순 최근 lookback개]).
    tickers는 파라미터 바인딩으로 IN 조회한다 (문자열 이어붙이기 금지).
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        days = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT date FROM ohlcv WHERE date <= ? "
                "ORDER BY date DESC LIMIT ?",
                [prev_date, lookback],
            ).fetchall()
        ][::-1]
        if not tickers:
            return {}, {}, days
        holders = ",".join("?" for _ in tickers)
        prev_rows = {}
        for ticker, close, volume, tv, mcap in con.execute(
            "SELECT ticker, close, volume, trading_value, market_cap FROM ohlcv "
            f"WHERE date = ? AND ticker IN ({holders})",
            [prev_date, *tickers],
        ).fetchall():
            prev_rows[ticker] = {
                "close": close,
                "volume": volume,
                "trading_value": tv,
                "market_cap": mcap,
            }
        closes_map: dict[str, list] = {t: [] for t in tickers}
        for ticker, _date, close in con.execute(
            "SELECT ticker, date, close FROM ohlcv "
            f"WHERE date <= ? AND ticker IN ({holders}) ORDER BY ticker, date",
            [prev_date, *tickers],
        ).fetchall():
            closes_map.setdefault(ticker, []).append(close)
        closes_map = {t: closes[-lookback:] for t, closes in closes_map.items()}
        return prev_rows, closes_map, days
    finally:
        con.close()


def load_accounts(db_path, tickers):
    """재무 DB에서 종목별 accounts 행들을 읽는다. 읽기 전용 연결.

    반환: {ticker: [행 dict]}. 행은 quarterly_series 입력 형태
    (bsns_year, reprt_code, fs_div, account_nm, amount).
    """
    if not tickers:
        return {}
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        holders = ",".join("?" for _ in tickers)
        out: dict[str, list] = {}
        for row in con.execute(
            "SELECT stock_code, bsns_year, reprt_code, fs_div, account_nm, amount "
            f"FROM accounts WHERE stock_code IN ({holders})",
            tickers,
        ).fetchall():
            out.setdefault(row["stock_code"], []).append(dict(row))
        return out
    finally:
        con.close()


def fetch_today_prices(broker_url, tickers, get=requests.get):
    """오늘 종가 일괄 조회 1회. {ticker: 가격} 반환, 실패 시 빈 dict.

    broker가 50종목씩 나눠 ka10095를 호출하므로 이쪽은 1콜이면 된다.
    실패(네트워크·HTTP·파싱)를 삼키고 빈 dict를 돌린다 — None이 아니라 빈 dict인
    이유: 호출측이 종목별로 "가격 누락" error를 기록해야지 전체를 중단하면 안 된다.
    cur_prc 파싱 실패 종목은 누락 취급(스킵)한다. broker가 abs int로 주지만
    방어적으로 int() 변환한다.
    """
    if not tickers:
        return {}
    try:
        resp = get(
            f"{broker_url}/quotes",
            params={"codes": ",".join(tickers)},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    out = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        code = item.get("stk_cd")
        try:
            price = int(item.get("cur_prc"))
        except (TypeError, ValueError, OverflowError):
            continue
        if code:
            out[str(code)] = price
    return out


def fetch_investor_sum(broker_url, ticker, start, end, get=requests.get):
    """종목 1개의 기간 투자자 순매수 합계 1회 조회. 실패 시 None.

    성공 시 broker 응답 dict 그대로 (frgnr_invsr·orgn 키 사용). 금액 단위는 스펙에
    없어 크기 비교 금지 — score_supply에서 부호만 본다.
    """
    try:
        resp = get(
            f"{broker_url}/quotes/{ticker}/investor-sum",
            params={"start": start, "end": end},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def judge_code(tickers, *, today, prev_date, broker_url, ohlcv_db, fin_db,
               get=requests.get):
    """종목별 코드 판정 조립. {ticker: 결과 dict} 반환.

    HTTP 호출 순서 (부담 최소화): 오늘 가격 일괄 1회 → 종목마다 투자자 합계 1회.
    그 외 HTTP는 없다. 2번 탈락 종목의 키움 조회 생략은 상위 파이프라인이
    tickers에서 빼는 방식으로 처리한다 (여기선 받은 전부를 판정).

    결과 키: s3·s4·s5(점수|None), 원값(op_profit·op_profit_yoy·quarter·
    frgn_net_5d·orgn_net_5d·today_price·ma20·ma20_gap·ret_5d·per·per_note·
    market_cap_prev), errors(항목→사유). 점수가 None이면 errors에 사유를 남긴다.
    한 종목의 예외가 다른 종목을 막지 않는다 — 종목 단위 try/except로 잡아
    errors["exception"]에 예외 클래스명만 남긴다.
    """
    prev_rows, closes_map, trading_days = load_ohlcv(ohlcv_db, tickers, prev_date)
    accounts = load_accounts(fin_db, tickers)
    try:
        win_start, win_end = supply_window(trading_days, today)
    except ValueError:
        win_start = win_end = None
    prices = fetch_today_prices(broker_url, tickers, get=get)

    result = {}
    for ticker in tickers:
        res = {
            "s3": None, "s4": None, "s5": None,
            "op_profit": None, "op_profit_yoy": None, "quarter": None,
            "frgn_net_5d": None, "orgn_net_5d": None,
            "today_price": None, "ma20": None, "ma20_gap": None, "ret_5d": None,
            "per": None, "per_note": None, "market_cap_prev": None,
            "errors": {},
        }
        try:
            series = quarterly_series(accounts.get(ticker, []))
            s3, raw3 = score_earnings(series)
            res["s3"] = s3
            res["op_profit"] = raw3["op"]
            res["op_profit_yoy"] = raw3["op_yoy"]
            res["quarter"] = raw3["quarter"]
            if s3 is None:
                res["errors"]["s3"] = "no_financials"

            market_cap = prev_rows.get(ticker, {}).get("market_cap")
            res["market_cap_prev"] = market_cap
            res["per"], res["per_note"] = per_value(market_cap, ttm_net(series))

            if win_start is None:
                res["errors"]["s4"] = "no_trading_days"
            else:
                inv = fetch_investor_sum(broker_url, ticker, win_start, win_end, get=get)
                if not isinstance(inv, dict):
                    res["errors"]["s4"] = "investor_sum_failed"
                else:
                    res["frgn_net_5d"] = inv.get("frgnr_invsr")
                    res["orgn_net_5d"] = inv.get("orgn")
                    res["s4"] = score_supply(res["frgn_net_5d"], res["orgn_net_5d"])
                    if res["s4"] is None:
                        res["errors"]["s4"] = "investor_sum_failed"

            closes = closes_map.get(ticker, [])
            res["today_price"] = prices.get(ticker)
            s5, raw5 = score_chart(closes, res["today_price"])
            res["s5"] = s5
            res["ma20"] = raw5["ma20"]
            res["ma20_gap"] = raw5["ma20_gap"]
            res["ret_5d"] = raw5["ret_5d"]
            if s5 is None:
                if len(closes) < MIN_CLOSES:
                    res["errors"]["s5"] = "short_history"
                else:  # 길이 충분한데 None이면 오늘 가격 문제 확정
                    res["errors"]["s5"] = "no_today_price"
        except Exception as exc:
            res["errors"]["exception"] = type(exc).__name__
        result[ticker] = res
    return result
