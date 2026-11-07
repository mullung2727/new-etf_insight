"""로지스틱/부스팅 모델 + 재생용 decide."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from .dataset import EXTRA_COLS
from .oracle import BUY, HOLD, K_MAX, SELL


def make(name: str):
    """logit → LogisticRegression, hgb → HistGradientBoostingClassifier."""
    if name == "logit":
        return LogisticRegression(max_iter=2000, class_weight="balanced")
    if name == "hgb":
        return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05,
                                              class_weight="balanced", random_state=0)
    raise ValueError(f"unknown model: {name}")


def decide_fn(model, scaler, feat: np.ndarray, names: list[str]) -> Callable[[int, int, int], int]:
    """칸 t, pos, k → 가능한 행동 중 predict_proba 최대.

    names = feat 열 이름(F 개), [pos, k/5] 는 안에서 붙인다.
    칸별 확률은 pos·k 조합별로 미리 한 번에 계산(캐시)해 재생 속도 확보.
    """
    feat = np.asarray(feat, dtype=np.float64)
    n = feat.shape[0]
    full = list(names) + list(EXTRA_COLS)
    nk = K_MAX + 1
    X_all = np.zeros((n * 2 * nk, feat.shape[1] + 2))
    r = 0
    for t in range(n):
        for pos in (0, 1):
            for k in range(nk):
                X_all[r, :-2] = feat[t]
                X_all[r, -2] = pos
                X_all[r, -1] = k / 5
                r += 1
    proba = model.predict_proba(scaler.transform(X_all, full)).reshape(n, 2, nk, -1)
    col = {int(a): j for j, a in enumerate(model.classes_)}

    def decide(t: int, pos: int, k: int) -> int:
        if t >= n - 1:
            return SELL if pos == 1 else HOLD
        feas = [HOLD, SELL] if pos == 1 else ([HOLD] if k <= 0 else [HOLD, BUY])
        best, a_best = -1.0, feas[0]
        for a in (HOLD, SELL, BUY):
            if a not in feas:
                continue
            p = proba[t, pos, k, col[a]] if a in col else 0.0
            if p > best:
                best, a_best = p, a
        return int(a_best)

    return decide
