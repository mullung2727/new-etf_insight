from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kiwoom import auth, client
from kiwoom import config as cfg_mod
from kiwoom import account, conditions, orders

router = APIRouter(prefix="/settings", tags=["settings"])

_CFG_MODULES = [orders, account, conditions]


class SettingsResponse(BaseModel):
    env: str


class SettingsUpdate(BaseModel):
    env: str


@router.get("", operation_id="get_settings", response_model=SettingsResponse)
def get_settings() -> SettingsResponse:
    return SettingsResponse(env=cfg_mod.get_current_env())


@router.post("", operation_id="update_settings", response_model=SettingsResponse)
def update_settings(body: SettingsUpdate) -> SettingsResponse:
    if body.env not in ("paper", "real"):
        raise HTTPException(status_code=400, detail="env must be 'paper' or 'real'")
    cfg_mod.set_runtime_env(body.env)
    auth.clear_cache()
    client.clear_cache()
    for mod in _CFG_MODULES:
        if hasattr(mod, "_cfg"):
            mod._cfg = None  # type: ignore[attr-defined]
    return SettingsResponse(env=body.env)
