"""투자노트 손익 계산 — note_events 자체 기반(kiwoom 계좌 집계 안 씀).

kiwoom의 평가손익(kt00018)·실현손익(ka10077/72)은 종목 단위 집계라, 같은
종목에 노트가 여러 개 열려있으면(실제로 그런 경우가 있다) 노트별로 못 쪼갠다.
그래서 각 노트가 자기 이벤트(가격×수량)만으로 가중평균원가를 굴려 계산한다.

수수료·세금은 kt00007에 없어 note_events에도 없다. 요율을 환경변수로 받아
적용하고, 요율이 0(미설정)이면 gross 손익만 나온다 — 호출측이 ``fee_applied``
로 구분해서 사용자에게 알린다.
"""
from __future__ import annotations

import os

from .models import EventType, NoteEvent, NotePnl

_BUY_TYPES = {EventType.buy.value, EventType.add_buy.value}


def _rate(name: str) -> float:
    return float(os.getenv(name, "0") or "0")


def compute(events: list[NoteEvent], current_price: int | None) -> NotePnl:
    """이벤트를 시간순으로 굴려 가중평균원가·순손익을 계산한다.

    events는 이미 executed_at, id 순 정렬돼 온다고 가정(store.get_note과 동일 정렬).
    """
    buy_fee_rate = _rate("NOTE_BUY_FEE_RATE")
    sell_fee_rate = _rate("NOTE_SELL_FEE_RATE")
    sell_tax_rate = _rate("NOTE_SELL_TAX_RATE")

    qty_held = 0
    cost_held = 0.0  # 현재 보유분의 총원가(가중평균 기준)
    bought_amt = 0
    sold_amt = 0

    for e in sorted(events, key=lambda x: (x.executed_at, x.id)):
        notional = e.price * e.qty
        if e.event_type.value in _BUY_TYPES:
            cost_held += notional
            qty_held += e.qty
            bought_amt += notional
        else:
            avg_cost = cost_held / qty_held if qty_held else 0.0
            sell_qty = min(e.qty, qty_held)  # 보유분 초과 매도 방어(데이터 이상 시 음수 방지)
            cost_held -= avg_cost * sell_qty
            qty_held -= sell_qty
            sold_amt += notional

    buy_fee = round(bought_amt * buy_fee_rate)
    sell_fee_tax = round(sold_amt * (sell_fee_rate + sell_tax_rate))

    needs_price = qty_held > 0 and current_price is None
    eval_amt = 0.0
    eval_fee_tax = 0.0
    if qty_held > 0 and current_price is not None:
        eval_amt = qty_held * current_price
        eval_fee_tax = eval_amt * (sell_fee_rate + sell_tax_rate)  # 지금 청산 가정

    invested_amt = round(bought_amt + buy_fee)
    recovered_amt = round(sold_amt - sell_fee_tax + eval_amt - eval_fee_tax)
    net_pnl = recovered_amt - invested_amt
    net_pnl_pct = round(net_pnl / invested_amt * 100, 2) if invested_amt else None

    return NotePnl(
        remaining_qty=qty_held,
        avg_cost=round(cost_held / qty_held, 2) if qty_held else None,
        invested_amt=invested_amt,
        recovered_amt=recovered_amt,
        net_pnl=net_pnl,
        net_pnl_pct=net_pnl_pct,
        fee_applied=bool(buy_fee_rate or sell_fee_rate or sell_tax_rate),
        needs_price=needs_price,
    )
