"""이득 회귀(주 모델)·분류 래퍼 + 재생용 decide."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression

from .dataset import EXTRA_COLS
from .oracle import BUY, HOLD, K_MAX, SELL


def make(name: str):
    """logit/hgb → ClassModel, hgb_adv → AdvModel."""
    # 정답 대부분이 관망 — 균형 가중은 매수를 과대 예측해 매일 상한 5회 매매 (11-07 전진 검증)
    if name == "logit":
        return ClassModel(LogisticRegression(max_iter=2000))
    if name == "hgb":
        return ClassModel(HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05,
                                                         early_stopping=False, random_state=0))
    if name == "hgb_adv":
        return AdvModel()
    raise ValueError(f"unknown model: {name}")


class ClassModel:
    """분류기 얇은 래퍼. fit(X, y, adv, pos_col) + decide_fn — 내부는 기존 predict_proba 최대 그대로."""

    def __init__(self, est) -> None:
        self.est = est

    def fit(self, X, y, adv, pos_col: int) -> "ClassModel":
        self.est.fit(X, y)
        return self

    def decide_fn(self, scaler, feat: np.ndarray, names: list[str]) -> Callable[[int, int, int], int]:
        return decide_fn(self.est, scaler, feat, names)


class AdvModel:
    """이득 회귀: pos=0 행 adv → 매수 회귀, pos=1 행 → 매도 회귀. 예측 이득 > 0 일 때만 매매."""

    def __init__(self) -> None:
        self._buy: HistGradientBoostingRegressor | None = None
        self._sell: HistGradientBoostingRegressor | None = None

    @staticmethod
    def _regressor() -> HistGradientBoostingRegressor:
        return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05,
                                             early_stopping=False, random_state=0)

    def fit(self, X, y, adv, pos_col: int) -> "AdvModel":
        X = np.asarray(X)
        adv = np.asarray(adv, dtype=np.float64)
        pos = X[:, pos_col]
        mb = (pos == 0) & np.isfinite(adv)
        ms = (pos == 1) & np.isfinite(adv)
        self._buy = self._regressor().fit(X[mb], adv[mb])
        self._sell = self._regressor().fit(X[ms], adv[ms])
        return self

    def decide_fn(self, scaler, feat: np.ndarray, names: list[str]) -> Callable[[int, int, int], int]:
        """pos=0: 예측 매수이득 > 0 이고 k>0 이면 BUY, 아니면 HOLD. pos=1: 예측 매도이득 > 0 이면 SELL.

        names = feat 열 이름(F 개), [pos, k/5] 는 안에서 붙인다.
        칸별 예측은 pos·k 조합 한 번에 미리 계산. 마지막 칸 규칙·불가능 처리는 replay 가 한다.
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
        Xs = scaler.transform(X_all, full)
        assert self._buy is not None and self._sell is not None, "fit first"
        buy = self._buy.predict(Xs).reshape(n, 2, nk)
        sell = self._sell.predict(Xs).reshape(n, 2, nk)

        def decide(t: int, pos: int, k: int) -> int:
            if pos == 0:
                return BUY if (k > 0 and buy[t, 0, k] > 0) else HOLD
            return SELL if sell[t, 1, k] > 0 else HOLD

        return decide


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
