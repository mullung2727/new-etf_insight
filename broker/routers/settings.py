from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from kiwoom import config as cfg_mod

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsResponse(BaseModel):
    env: str
    account_tail: str       # 계좌번호 뒤 4자리 — 실전/모의 계좌 오인 방지용
    max_order_amount: int   # 1회 주문 금액 상한(원). 표시 전용 — 여기서 못 바꾼다


@router.get("", operation_id="get_settings", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    """현재 매매 환경(paper/실전)·접속 계좌 뒷자리·주문 금액 상한. 읽기 전용.

    환경은 프로세스 시작 시 ``KIWOOM_ENV`` 로 고정된다. 런타임 전환 API는 없다 —
    주소만 바뀌고 자격증명·계좌·실시간 체결 연결은 그대로 남아 혼합 상태가 되기 때문.
    바꾸려면 루트 ``.env`` 를 수정하고 broker 를 재기동한다.

    ``max_order_amount`` 도 같은 이유로 읽기 전용이다. 이 값은 전략 예산(웹에서 편집하는
    pullback.json / close_bet.json)이 틀렸을 때 막는 최후 방어선이라, 예산과 같은 화면에서
    고칠 수 있으면 오타 한 번에 둘 다 뚫린다. 표시만 하고 변경은 .env + 재기동.
    """
    cfg = cfg_mod.load_config()
    return SettingsResponse(
        env=cfg_mod.get_current_env(),
        account_tail=cfg.account_no[-4:] if cfg.account_no else "",
        max_order_amount=cfg.max_order_amount,
    )
