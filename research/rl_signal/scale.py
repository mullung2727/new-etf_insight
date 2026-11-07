"""표준화: ÷vol20 → 학습 구간 z → ±5 clip. BOUNDED·미등록 이름은 그대로."""

from __future__ import annotations

import numpy as np

BOUNDED = ["imb_1", "imb_5", "buy_ratio", "t_frac", "hl_pos"]           # 그대로
LOG1P = ["spread_bp", "vol_rel"]                                         # log1p 후 z
BY_VOL = ["ret_1", "ret_2", "ret_10", "ret_30", "rv_10", "rv_30",
          "ret_open", "ret_prev", "vwap_gap", "ma30_gap", "ma20_gap", "ret5"]  # ÷ vol20 후 z
# vol20 자체는 z 만
VOL_FALLBACK = 0.02  # vol20 NaN·0 이면


class Scaler:
    """fit(학습 X) 통계만 저장, transform 은 저장값 사용. 미등록 이름(pos·k/5 등 0~1)은 그대로."""

    def __init__(self) -> None:
        self.names_: list[str] | None = None
        self.mean_: np.ndarray | None = None
        self.std_: np.ndarray | None = None

    @staticmethod
    def _pre(X: np.ndarray, names: list[str]) -> np.ndarray:
        X = np.asarray(X, dtype=np.float64)
        P = X.copy()
        idx = {nm: j for j, nm in enumerate(names)}
        if "vol20" in idx:
            vol = X[:, idx["vol20"]].copy()
        else:
            vol = np.full(X.shape[0], np.nan)
        vol[np.isnan(vol) | (vol == 0)] = VOL_FALLBACK
        for j, nm in enumerate(names):
            if nm in BY_VOL:
                P[:, j] = X[:, j] / vol
            elif nm in LOG1P:
                with np.errstate(divide="ignore", invalid="ignore"):
                    P[:, j] = np.log1p(X[:, j])
        return P

    def fit(self, X: np.ndarray, names: list[str]) -> "Scaler":
        """학습 X 로만 평균·표준편차(nanmean/nanstd, std 0 → 1)."""
        names = list(names)
        P = self._pre(X, names)
        with np.errstate(all="ignore"):
            mean = np.nanmean(P, axis=0)
            std = np.nanstd(P, axis=0)
        self.names_ = names
        self.mean_ = np.where(np.isfinite(mean), mean, 0.0)
        self.std_ = np.where(np.isfinite(std) & (std != 0), std, 1.0)
        return self

    def transform(self, X: np.ndarray, names: list[str]) -> np.ndarray:
        """위 규칙 → z → ±5 clip → NaN 0. float32."""
        names = list(names)
        P = self._pre(X, names)
        Z = P.copy()
        stat = dict(zip(self.names_, zip(self.mean_, self.std_)))
        for j, nm in enumerate(names):
            if nm in BOUNDED or nm not in stat:
                continue
            if nm not in LOG1P and nm not in BY_VOL and nm != "vol20":
                continue
            mu, sd = stat[nm]
            Z[:, j] = (P[:, j] - mu) / sd
        Z = np.clip(Z, -5, 5)
        Z[np.isnan(Z)] = 0.0
        return Z.astype(np.float32)
