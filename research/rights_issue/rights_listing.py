"""순수 주주배정 신주 상장 후 물량소화 매수 백테스트 (SPEC: research/rights_issue/SPEC.md R1~R8).

실행: cd etl && PYTHONPATH=.. uv run python ../research/rights_issue/rights_listing.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import analyze as A
from research.backtest_daily import data, guards, stats

ROOT = Path(__file__).parent
OUT = ROOT / "out"
APPLIED = ("적용값: 대상=일정목록 증자구분==주주배정(R1) · 비율=주당신주배정주식수≥0.15(R2) · "
           "L=납입일후 30행내 첫 shares(market_cap/close)+0.5%초과(R3) · "
           "t=L+1~5 첫 저가>min저가·거래량≤절반·종가>MA5(R4) · "
           "진입=t종가·상한가제외(R5) · 청산=t+1~ 익절+10%/손절min저가×0.97/20일만기·동시손절우선(R6) · "
           "비용 0.35%(R7) · tp_sl_exit 내장(R8)")
COST = 0.0035
assert COST in stats.COSTS


def parse_ratio(v):
    """'주당신주배정주식수(주)' → float. 실패 시 None."""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def find_listing_date(sub, payday):
    """R3: 납입일 이후 30행 안 첫 shares 증가일. (날짜, iloc) / (None, None)."""
    dates = np.array([A.norm_date(d) or "" for d in sub["date"].to_numpy()])
    close = sub["close"].to_numpy(float)
    mc = pd.to_numeric(sub["market_cap"], errors="coerce").to_numpy(dtype=float)
    shares = np.full(len(sub), np.nan)
    ok = np.isfinite(mc) & (mc > 0) & np.isfinite(close) & (close > 0)
    shares[ok] = mc[ok] / close[ok]
    for i in np.where(dates > payday)[0][:30]:
        if i == 0:
            continue
        prev, cur = shares[i - 1], shares[i]
        if np.isfinite(prev) and prev > 0 and np.isfinite(cur) and cur / prev - 1 > 0.005:
            return (dates[i], int(i))
    return (None, None)


def is_signal_t(low_t, min_low_prev, vol_t, vol_L, close_t, closes):
    """R4 3조건 모두 참이면 True. closes=t 포함 직전 5행 종가."""
    if not len(closes):
        return False
    return (low_t > min_low_prev
            and vol_t <= 0.5 * vol_L
            and close_t > sum(closes) / len(closes))


def find_t(sub, lpos):
    """R4: L 다음 1~5행 중 첫 신호. (날짜, iloc, offset) / (None, None, None)."""
    dates = np.array([A.norm_date(d) or "" for d in sub["date"].to_numpy()])
    low = sub["low"].to_numpy(float)
    vol = sub["volume"].to_numpy(float)
    close = sub["close"].to_numpy(float)
    for k in range(1, 6):
        tpos = lpos + k
        if tpos >= len(sub):
            break
        w = close[max(0, tpos - 4):tpos + 1]
        if is_signal_t(float(low[tpos]), float(np.min(low[lpos:tpos])),
                       float(vol[tpos]), float(vol[lpos]),
                       float(close[tpos]), [float(c) for c in w]):
            return (dates[tpos], int(tpos), k)
    return (None, None, None)


def tp_sl_exit(rows, entry, tp, sl, max_hold=20):
    """R6 순수 함수. rows=t+1부터 date/open/high/low/close 매핑 리스트(entry는 호출부 명확성용).

    반환 (청산일, 청산가, 사유, 보유일). 사유: 익절/손절/만기/데이터끝/데이터없음."""
    if not rows:
        return (None, None, "데이터없음", 0)
    for i, r in enumerate(rows[:max_hold]):
        o, h, l = r["open"], r["high"], r["low"]
        if o <= sl:
            return (r["date"], o, "손절", i + 1)
        if o >= tp:
            return (r["date"], o, "익절", i + 1)
        if l <= sl:
            return (r["date"], sl, "손절", i + 1)
        if h >= tp:
            return (r["date"], tp, "익절", i + 1)
    if len(rows) >= max_hold:
        r = rows[max_hold - 1]
        return (r["date"], r["close"], "만기", max_hold)
    r = rows[-1]
    return (r["date"], r["close"], "데이터끝", len(rows))


def sub_rows(sub, pos):
    """pos 다음 행부터 date/open/high/low/close dict 리스트."""
    a = sub.iloc[pos + 1:]
    d = [A.norm_date(x) or "" for x in a["date"].to_numpy()]
    o = a["open"].to_numpy(float)
    h = a["high"].to_numpy(float)
    l = a["low"].to_numpy(float)
    c = a["close"].to_numpy(float)
    return [{"date": dd, "open": float(oo), "high": float(hh),
             "low": float(ll), "close": float(cc)}
            for dd, oo, hh, ll, cc in zip(d, o, h, l, c)]


def main():
    with_public = "--with-public" in sys.argv[1:]
    kinds = ("주주배정", "주주배정후실권주일반공모") if with_public else ("주주배정",)
    applied = (APPLIED.replace("증자구분==주주배정(R1)",
                               "증자구분==주주배정+주주배정후실권주일반공모(R1)")
               if with_public else APPLIED)
    print(applied)
    raw = A.load_cache()
    px = data.load_px()
    tk = px["ticker"].to_numpy()

    recs, trades, base, skip = [], [], [], {}
    base_by_kind = {}

    def drop(reason):
        skip[reason] = skip.get(reason, 0) + 1

    evs = []
    for ac in sorted(raw):
        _kind, pr, _fr, vw = raw[ac]
        if pr is None:
            continue
        if (pr.get("증자구분") or "").strip() not in kinds:
            continue
        evs.append((ac, pr, vw))
    n_list = len(evs)
    n_ratio = n_code = n_L = n_sig = n_entry = 0

    for ac, pr, vw in evs:
        kind = (pr.get("증자구분") or "").strip()
        rec = {"acptno": ac, "회사": pr.get("회사명") or "", "코드": "",
               "증자구분": kind,
               "증자비율": np.nan, "납입일": "", "L": "", "t": "", "t_offset": "",
               "진입가": np.nan, "청산일": "", "청산가": np.nan, "청산사유": "",
               "보유일": "", "gross": np.nan, "net": np.nan, "상태": ""}
        ratio = parse_ratio(pr.get("주당신주배정주식수(주)"))
        if ratio is None:
            rec["상태"] = "비율결측"
            drop("비율결측")
            recs.append(rec)
            continue
        rec["증자비율"] = ratio
        if ratio < 0.15:
            rec["상태"] = "비율미달"
            drop("비율미달")
            recs.append(rec)
            continue
        n_ratio += 1
        code = (vw or {}).get("stock_code") or ""
        if not code:
            rec["상태"] = "코드없음"
            drop("코드없음")
            recs.append(rec)
            continue
        rec["코드"] = code
        n_code += 1
        payday = A.norm_date(pr.get("납입일"))
        if not payday:
            rec["상태"] = "납입일없음"
            drop("납입일없음")
            recs.append(rec)
            continue
        rec["납입일"] = payday
        lo = int(np.searchsorted(tk, code, side="left"))
        hi = int(np.searchsorted(tk, code, side="right"))
        if lo == hi:
            rec["상태"] = "일봉없음"
            drop("일봉없음")
            recs.append(rec)
            continue
        sub = px.iloc[lo:hi]
        Ldate, lpos = find_listing_date(sub, payday)
        if Ldate is None:
            rec["상태"] = "L없음"
            drop("L없음")
            recs.append(rec)
            continue
        rec["L"] = Ldate
        n_L += 1
        close = sub["close"].to_numpy(float)
        low = sub["low"].to_numpy(float)
        entry_L = float(close[lpos])
        b = tp_sl_exit(sub_rows(sub, lpos), entry_L, entry_L * 1.10,
                       float(low[lpos]) * 0.97, 20)
        if b[0] is not None:
            bn = b[1] / entry_L - 1 - COST
            base.append(bn)
            base_by_kind.setdefault(kind, []).append(bn)
        tdate, tpos, off = find_t(sub, lpos)
        if tdate is None:
            rec["상태"] = "신호없음"
            drop("신호없음")
            recs.append(rec)
            continue
        rec["t"] = tdate
        rec["t_offset"] = off
        n_sig += 1
        g = guards.limit_up_close(sub, tpos)
        if g is not None:
            rec["상태"] = g
            drop(g)
            recs.append(rec)
            continue
        entry = float(close[tpos])
        rec["진입가"] = entry
        sl = float(np.min(low[lpos:tpos + 1])) * 0.97
        edate, epx, reason, hold = tp_sl_exit(sub_rows(sub, tpos), entry,
                                             entry * 1.10, sl, 20)
        if edate is None:
            rec["상태"] = "청산불가"
            drop("청산불가")
            recs.append(rec)
            continue
        n_entry += 1
        gross = epx / entry - 1
        rec.update({"청산일": edate, "청산가": epx, "청산사유": reason,
                    "보유일": hold, "gross": gross, "net": gross - COST,
                    "상태": "거래완료"})
        recs.append(rec)
        trades.append(rec)

    funnel = "대상" if with_public else "주주배정"
    print(f"깔때기: {funnel}={n_list} → 비율통과={n_ratio} → 코드있음={n_code} "
          f"→ L찾음={n_L} → 신호있음={n_sig} → 진입={n_entry}")
    if skip:
        print("제외: " + ", ".join(f"{k}={v}" for k, v in skip.items()))

    print("회사 코드 증자구분 증자비율 납입일 L t 진입가 청산일 청산사유 보유일 net%")
    for r in trades:
        print(f"{r['회사']} {r['코드']} {r['증자구분']} {r['증자비율']:.4f} {r['납입일']} {r['L']} "
              f"{r['t']}(L+{r['t_offset']}) {r['진입가']:,.0f} {r['청산일']} "
              f"{r['청산사유']} {r['보유일']} {r['net'] * 100:+.1f}%")
    if not trades:
        print("(없음)")

    def pct(v):
        return "-" if pd.isna(v) else f"{float(v) * 100:.1f}%"

    if trades:
        net = np.array([r["net"] for r in trades], float)
        print(f"요약: n={len(trades)} 승률={pct(float(np.mean(net > 0)))} "
              f"평균net={pct(float(np.mean(net)))} 중앙net={pct(float(np.median(net)))}")
        for k in kinds:
            s = np.array([r["net"] for r in trades if r["증자구분"] == k], float)
            if len(s):
                print(f"요약[{k}]: n={len(s)} 승률={pct(float(np.mean(s > 0)))} "
                      f"평균net={pct(float(np.mean(s)))} 중앙net={pct(float(np.median(s)))}")
        parts = []
        for rs in ("익절", "손절", "만기", "데이터끝"):
            s = net[[r["청산사유"] == rs for r in trades]]
            parts.append(f"{rs}={len(s)}건·{pct(float(np.mean(s))) if len(s) else '-'}")
        print("사유별: " + " ".join(parts))
        print("연도별(진입연도):")
        for y in sorted({str(r["t"])[:4] for r in trades if r["t"]}):
            s = np.array([r["net"] for r in trades if str(r["t"])[:4] == y], float)
            print(f"{y}: n={len(s)} 승률={pct(float(np.mean(s > 0)))} "
                  f"평균net={pct(float(np.mean(s)))}")
    else:
        print("요약: n=0 (거래 없음)")

    if base:
        b = np.array(base, float)
        print(f"기준선(L종가매수·조건없음): n={len(b)} 승률={pct(float(np.mean(b > 0)))} "
              f"평균net={pct(float(np.mean(b)))} 중앙net={pct(float(np.median(b)))}")
        for k in kinds:
            s = np.array(base_by_kind.get(k, []), float)
            if len(s):
                print(f"기준선[{k}](L종가매수·조건없음): n={len(s)} 승률={pct(float(np.mean(s > 0)))} "
                      f"평균net={pct(float(np.mean(s)))} 중앙net={pct(float(np.median(s)))}")
    else:
        print("기준선(L종가매수·조건없음): n=0")

    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["acptno", "회사", "코드", "증자구분", "증자비율", "납입일", "L", "t", "t_offset",
            "진입가", "청산일", "청산가", "청산사유", "보유일", "gross", "net", "상태"]
    fname = "listing_trades_public.csv" if with_public else "listing_trades.csv"
    pd.DataFrame(recs, columns=cols).to_csv(OUT / fname,
                                            index=False, encoding="utf-8")


def selfcheck():
    def mk(date, o, h, l, c):
        return {"date": date, "open": o, "high": h, "low": l, "close": c}

    entry, tp, sl = 1000.0, 1100.0, 900.0
    rows = [mk("D1", 1000, 1050, 950, 1000),
            mk("D2", 1000, 1060, 960, 1005),
            mk("D3", 1005, 1100, 1000, 1080)]
    rows += [mk(f"D{i}", 1080, 1090, 1070, 1080) for i in range(4, 21)]
    assert tp_sl_exit(rows, entry, tp, sl, 20) == ("D3", tp, "익절", 3)
    assert tp_sl_exit([mk("D1", 1000, 1200, 800, 1050)],
                      entry, tp, sl, 20) == ("D1", sl, "손절", 1)
    assert tp_sl_exit([mk("D1", 850, 860, 840, 855)],
                      entry, tp, sl, 20) == ("D1", 850, "손절", 1)
    assert tp_sl_exit([mk("D1", 1150, 1160, 1140, 1155)],
                      entry, tp, sl, 20) == ("D1", 1150, "익절", 1)
    rows = [mk(f"D{i}", 1000, 1050, 950, 1000 + i) for i in range(1, 21)]
    d, p, r, n = tp_sl_exit(rows, entry, tp, sl, 20)
    assert (d, p, r, n) == ("D20", 1020, "만기", 20), (d, p, r, n)
    rows = [mk(f"D{i}", 1000, 1050, 950, 1000) for i in range(1, 6)]
    assert tp_sl_exit(rows, entry, tp, sl, 20)[2:] == ("데이터끝", 5)
    assert tp_sl_exit([], entry, tp, sl, 20) == (None, None, "데이터없음", 0)

    good = dict(low_t=105, min_low_prev=100, vol_t=50, vol_L=100,
                close_t=110, closes=[100, 101, 102, 103, 110])
    assert is_signal_t(**good) is True
    assert is_signal_t(**dict(good, low_t=100)) is False
    assert is_signal_t(**dict(good, vol_t=51)) is False
    assert is_signal_t(**dict(good, close_t=101,
                              closes=[100, 101, 102, 103, 101])) is False

    df = pd.DataFrame({
        "date": [f"2024010{d}" for d in range(1, 9)],
        "open": 100.0, "high": 110.0,
        "low": [100, 100, 100, 99, 101, 90, 90, 90],
        "close": [100, 100, 100, 100, 120, 100, 100, 100],
        "volume": [1000, 1000, 1000, 400, 400, 1000, 1000, 1000],
    })
    assert find_t(df, 2) == ("20240105", 4, 2)
    assert find_t(df, 5) == (None, None, None)


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        selfcheck()
        print("selfcheck passed")
    else:
        main()
