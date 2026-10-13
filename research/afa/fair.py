"""적정가 회귀 (OLS·Theil-Sen 변형)."""

from __future__ import annotations

import warnings

import numpy as np


def _with_const(X: np.ndarray) -> np.ndarray:
    """상수항 추가."""
    Xa = np.asarray(X, dtype=float)
    if Xa.ndim == 1:
        Xa = Xa[:, None]
    return np.column_stack([np.ones(len(Xa)), Xa])


def ols_fair(X: np.ndarray, v: np.ndarray) -> np.ndarray:
    """전표본 OLS 예측값."""
    X1 = _with_const(X)
    y = np.asarray(v, dtype=float).ravel()
    beta, *_ = np.linalg.lstsq(X1, y, rcond=None)
    return X1 @ beta


def ts_fair(X: np.ndarray, v: np.ndarray, draws: int = 5000,
            m: int = 100, seed: int = 0) -> np.ndarray:
    """부분표본 회귀 중앙값 (부분표본만의 랭크 부족 제외)."""
    X1 = _with_const(X)
    y = np.asarray(v, dtype=float).ravel()
    n = len(y)
    if n < m:
        return ols_fair(X, v)
    rng = np.random.default_rng(seed)
    preds = np.full((int(draws), n), np.nan, dtype=np.float32)
    full_rank = np.linalg.matrix_rank(X1)
    for d in range(int(draws)):
        idx = rng.choice(n, size=int(m), replace=False)
        Xs, ys = X1[idx], y[idx]
        if np.linalg.matrix_rank(Xs) < full_rank:
            continue
        beta, *_ = np.linalg.lstsq(Xs, ys, rcond=None)
        preds[d] = (X1 @ beta).astype(np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmedian(preds, axis=0)


def mispricing(pred: np.ndarray, v: np.ndarray) -> np.ndarray:
    """괴리율 (적정가−시총)/시총."""
    return (np.asarray(pred, dtype=float) - np.asarray(v, dtype=float)) / np.asarray(v, dtype=float)
