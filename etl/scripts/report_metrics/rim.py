"""RIM(잔여이익모형) 입력 조립 + 계산.

  V0 = B0 + Σ_t (ROE_t - r)·B_{t-1} / (1+r)^t + TV
  TV = RI_T · ω / (1 + r - ω) / (1+r)^T      (잔여이익이 매년 ω 비율로 지속)

리포트 추정표에는 자기자본이 없어서 B_t = 순이익_t / ROE_t 로 역산한다(PLAN §2.5).
금액 단위는 억원. 주식수를 주면 주당가치(원)도 낸다.
r/ω 는 사용자 확정 전 기본값이다 — 결과에 params 를 같이 돌려준다.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from .models import RimInputs, YearEstimate

DEFAULT_R = 0.08
DEFAULT_OMEGA = 0.8


def _equity(e: YearEstimate) -> Optional[float]:
    if e.net_profit is None or not e.roe:
        return None
    return e.net_profit / (e.roe / 100.0)


def build_rim_inputs(
    estimates: Sequence[YearEstimate],
    r: float = DEFAULT_R,
    omega: float = DEFAULT_OMEGA,
) -> Optional[RimInputs]:
    """추정표 → RIM 입력. 추정연도(F/E)가 없거나 쓸 수 있는 연도가 0개면 None."""
    forecasts = sorted((e for e in estimates if e.is_forecast), key=lambda e: e.fiscal_year)
    if not forecasts:
        return None
    base_year = forecasts[0].fiscal_year - 1
    warnings: list[str] = []

    base = next((e for e in estimates if e.fiscal_year == base_year), None)
    b0 = _equity(base) if base is not None else None
    if b0 is None:
        # 기준연도 자본을 못 구하면 첫 추정연도 자본에서 그 해 순이익을 뺀다(배당 무시 근사).
        first_b = _equity(forecasts[0])
        if first_b is None:
            return None
        b0 = first_b - forecasts[0].net_profit
        warnings.append(f"{base_year}: 기준자본 없음 → {forecasts[0].fiscal_year} 자본-순이익으로 근사")

    inputs = RimInputs(base_equity=b0, base_year=base_year, r=r, omega=omega, warnings=warnings)
    for e in forecasts:
        b = _equity(e)
        if b is None:
            warnings.append(f"{e.fiscal_year}: ROE 또는 순이익 없음 → 제외")
            continue
        inputs.years.append(e.fiscal_year)
        inputs.roes.append(e.roe)
        inputs.equities.append(b)
    return inputs if inputs.years else None


def rim_value(
    inputs: Optional[RimInputs], shares: Optional[int] = None
) -> Optional[dict[str, Any]]:
    """RIM 자기자본가치(억원) + 주당가치(원, shares 있을 때) + 사용 파라미터 + 경고."""
    if inputs is None or not inputs.years:
        return None
    r, omega = inputs.r, inputs.omega
    prev_b = inputs.base_equity
    pv_ri = 0.0
    last_ri, last_t = 0.0, 0
    for year, roe, b in zip(inputs.years, inputs.roes, inputs.equities):
        t = year - inputs.base_year
        ri = (roe / 100.0 - r) * prev_b
        pv_ri += ri / (1 + r) ** t
        prev_b, last_ri, last_t = b, ri, t
    terminal = last_ri * omega / (1 + r - omega) / (1 + r) ** last_t
    value = inputs.base_equity + pv_ri + terminal
    return {
        "equity_value": value,
        "value_per_share": value * 1e8 / shares if shares else None,
        "base_equity": inputs.base_equity,
        "pv_residual_income": pv_ri,
        "terminal_value": terminal,
        "params": {"r": r, "omega": omega, "base_year": inputs.base_year,
                   "years": list(inputs.years)},
        "warnings": list(inputs.warnings),
    }
