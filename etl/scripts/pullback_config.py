"""눌림목 전략 설정 로더. 필수 JSON이 없거나 잘못되면 주문을 중단한다."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_KEYS = {
    "budget_per_stock", "max_new_positions", "tp", "sl",
    "max_wait_days", "max_hold_days",
}


def path() -> Path:
    return Path(__file__).resolve().parent / "pullback.json"


def _integer(value: Any, name: str, minimum: int, maximum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")


def validate(config: dict[str, Any]) -> None:
    missing = REQUIRED_KEYS - set(config)
    if missing:
        raise ValueError(f"pullback config missing keys: {', '.join(sorted(missing))}")
    _integer(config["budget_per_stock"], "budget_per_stock", 1)
    _integer(config["max_new_positions"], "max_new_positions", 1, 3)
    _integer(config["max_wait_days"], "max_wait_days", 1, 5)
    _integer(config["max_hold_days"], "max_hold_days", 1, 5)
    for name in ("tp", "sl"):
        value = config[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
            raise ValueError(f"{name} must be a number > 0 and <= 1")


def load(config_path: Path | None = None) -> dict[str, Any]:
    target = config_path or path()
    config = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("pullback config must be a JSON object")
    validate(config)
    return config
