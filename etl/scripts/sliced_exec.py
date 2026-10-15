"""여러 회차로 나눠 지정가 주문을 실행한다."""
from __future__ import annotations
from datetime import timedelta

try:
    from scripts import chase_engine as ch
except ImportError:
    import chase_engine as ch


def slice_qty(remaining: int, slices_left: int, cap: int | None = None) -> int:
    if remaining <= 0 or slices_left <= 0:
        return 0
    qty = remaining if slices_left == 1 else remaining // slices_left
    return min(qty, cap) if cap is not None else qty


def passive_price_fn(api):
    def price_fn(ticker, side):
        quote_side = "sell" if side == "buy" else "buy"
        if hasattr(api, "best_quote"):
            return api.best_quote(ticker, quote_side)
        return None
    return price_fn


def run_sliced(today, orders, api, clock, *,
               slices=("09:30:00", "11:00:00", "13:00:00", "14:30:00"),
               last_cut="15:10:00", final_market_at="15:15:00",
               end_at="15:20:00", step_seconds=30, cash_fn=None,
               on_event=None) -> dict[str, int]:
    remaining = {o["key"]: int(o["qty"]) for o in orders}
    blocked = set()
    starts = list(slices)
    for i, start in enumerate(starts):
        start_dt = ch.at(today, start)
        if clock.now() > start_dt:
            continue
        next_cut = (ch.at(today, starts[i + 1]) - timedelta(minutes=1)
                    if i + 1 < len(starts) else ch.at(today, last_cut))
        cut = next_cut.strftime("%H:%M:%S")
        wants = {}
        for o in orders:
            q = (0 if o["key"] in blocked else
                 slice_qty(remaining[o["key"]], len(starts) - i, o.get("cap_qty")))
            if q:
                wants[o["key"]] = q
        buys = [o for o in orders if o["key"] in wants and o["side"] == "buy"]
        if buys and cash_fn is not None:
            cash = cash_fn()
            if cash is None:
                for o in buys:
                    wants[o["key"]] = 0
            else:
                asks = ({o["key"]: api.best_quote(o["ticker"], "buy")
                         for o in buys} if hasattr(api, "best_quote") else
                        {o["key"]: None for o in buys})
                if any(not p for p in asks.values()):
                    for o in buys:
                        wants[o["key"]] = 0
                else:
                    weights = {o["key"]: wants[o["key"]] * asks[o["key"]] for o in buys}
                    total = sum(weights.values())
                    for o in buys:
                        budget = int(cash * weights[o["key"]] / total) if total else 0
                        wants[o["key"]] = min(wants[o["key"]], budget // asks[o["key"]])
        states = []
        by_key = {}
        for side in ("sell", "buy"):
            for o in orders:
                q = wants.get(o["key"], 0)
                if o["side"] != side or q <= 0:
                    continue
                final_market = i == len(starts) - 1 and o.get("final", False)
                state = {"p": {"ticker": o["ticker"]}, "qty": q, "nos": [],
                         "order_no": "", "status": "pending", "rejects": 0,
                         "modifies": 0, "side": side,
                         "price_fn": lambda ticker, _side=side: _passive_quote(api, ticker, _side),
                         "start_at": start, "market_at": final_market_at if final_market else cut}
                if not final_market:
                    state["cancel_only_at"] = cut
                states.append(state)
                by_key[o["key"]] = state
        if not states:
            continue
        ch.run_chase(today, states, api, clock, first_at=start,
                     market_at=cut, end_at=end_at, step_seconds=step_seconds,
                     on_event=on_event)
        for o in orders:
            state = by_key.get(o["key"])
            if state is None:
                continue
            if state.get("status") == "carried":
                remaining[o["key"]] = state["carry_qty"] + (remaining[o["key"]] - state["qty"])
            elif state.get("status") == "unknown":
                blocked.add(o["key"])
                continue
            elif state.get("status") == "done":
                fills = api.fills(today, o["side"])
                if fills is not None:
                    filled = ch._fills(state["nos"], fills)
                    remaining[o["key"]] -= min(remaining[o["key"]], filled)
    return remaining


def _passive_quote(api, ticker, side):
    quote_side = "sell" if side == "buy" else "buy"
    return (api.best_quote(ticker, quote_side) if hasattr(api, "best_quote")
            else None)
