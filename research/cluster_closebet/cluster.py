"""표준화 + K-Means — PLAN §4.3/§5 확정값 내장.

Euclidean + k-means++ + n_init=20 + seed=42.
std=0은 None(호출측에서 제외), 커버리지는 80% 하한.
"""
from __future__ import annotations

import math
import statistics

SEED = 42
N_INIT = 20
MIN_WEEKS = 52
MIN_COVERAGE = math.ceil(MIN_WEEKS * 0.8)  # 42


def enough_coverage(n: int, expected: int = MIN_WEEKS) -> bool:
    """결측 20% 초과 제외."""
    return n >= math.ceil(expected * 0.8)


def standardize(series: list[float]) -> list[float] | None:
    """Z-score. std=0이면 None."""
    if len(series) < 2:
        return None
    sd = statistics.pstdev(series)
    if sd == 0:
        return None
    m = statistics.mean(series)
    return [(x - m) / sd for x in series]


def cluster_labels(matrix: list[list[float]], k: int) -> list[int]:
    """K-Means 라벨. 고정 seed라 동일 입력은 동일 출력."""
    from sklearn.cluster import KMeans

    km = KMeans(n_clusters=k, init="k-means++", n_init=N_INIT, random_state=SEED)
    return list(km.fit_predict(matrix))
