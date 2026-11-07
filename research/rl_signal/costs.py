"""수수료·세금 비용. 스프레드는 1호가 체결가에 이미 포함이라 여기선 제외."""

FEE = 0.00015        # 편도 수수료 — BACKTEST_DATA.md 6-1 실측(왕복 0.2306% − 세금 0.1998%)/2 ≈ 0.015%
SELL_TAX = 0.0020    # 매도 거래세+농특세 — BACKTEST_DATA.md 6-1 실측 0.1998%
# ponytail: 녹화기(scripts/run_orderbook_research_recorder.ETF_CODES)와 같은 6종. 탑30 안의 다른 ETF 는 종목 비용(보수적 과대). ETF 목록 DB 생기면 교체
ETF_CODES = frozenset({"069500", "122630", "114800", "229200", "233740", "251340"})
UNIT_KRW = 1_000_000   # 1단위 주문 금액 — 잔량 검사용 (내 판단, PLAN §10)


def tick(code: str, price: float) -> int:
    """ETF(ETF_CODES): 2000 미만 1, 이상 5. 종목: research.backtest_minute.ticks.tick_size(price)."""
    if code in ETF_CODES:
        return 1 if float(price) < 2000 else 5
    from research.backtest_minute.ticks import tick_size
    return tick_size(price)


def buy_cost(code: str) -> float:
    return FEE


def sell_cost(code: str) -> float:
    return FEE + (0.0 if code in ETF_CODES else SELL_TAX)
