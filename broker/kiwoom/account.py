"""잔고조회 — holdings + deposit/eval summary."""

from __future__ import annotations

from typing import Any

from . import tr
from .client import request
from .config import Config, load_config

_cfg: Config | None = None


def _config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def get_balance() -> dict[str, Any]:
    """Return 계좌평가잔고내역 (holdings list + valuations), all pages merged.

    Body fields per kt00018: account number + query/exchange type flags.
    ``qry_tp`` 1=합산, ``dmst_stex_tp`` KRX. Adjust in tr.py/here if your
    account's spec differs.
    """
    cfg = _config()
    body = {
        "acnt_no": cfg.account_no,
        "qry_tp": "1",
        "dmst_stex_tp": "KRX",
    }
    res = request(tr.TR_BALANCE, tr.EP_ACNT, body)
    data = dict(res.data)

    # Drain continuation pages so the caller sees the full holdings list.
    while res.cont_yn == "Y" and res.next_key:
        res = request(
            tr.TR_BALANCE, tr.EP_ACNT, body, cont_yn="Y", next_key=res.next_key
        )
        for key, val in res.data.items():
            if isinstance(val, list) and isinstance(data.get(key), list):
                data[key].extend(val)
    return data


def get_deposit() -> dict[str, Any]:
    """Return 예수금상세현황 (cash available for orders)."""
    cfg = _config()
    res = request(tr.TR_DEPOSIT, tr.EP_ACNT, {"acnt_no": cfg.account_no})
    return res.data
