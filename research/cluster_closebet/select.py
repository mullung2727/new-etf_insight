"""선정 로직 — Top30 + Count + N(no-trade) + Top3.

가정(PLAN 미정의분):
- 동점 시 cluster id 오름차순.
- 배정 없는 종목은 집계에서 제외.
- 선택 Cluster 내 종목이 k개 미만이면 있는 만큼만 반환.
"""
from __future__ import annotations

TOP_N = 30
PICK_K = 3


def top_by_turnover(rows: list[dict], n: int = TOP_N) -> list[dict]:
    """거래대금 내림차순 상위 n행. 동점은 ticker 오름차순."""
    return sorted(rows, key=lambda r: (-r["turnover"], r["ticker"]))[:n]


def cluster_counts(top_rows: list[dict], assignment: dict[str, int]) -> dict[int, int]:
    """Top30 내 Cluster별 종목 수."""
    counts: dict[int, int] = {}
    for r in top_rows:
        c = assignment.get(r["ticker"])
        if c is None:
            continue
        counts[c] = counts.get(c, 0) + 1
    return counts


def pick_cluster(counts: dict[int, int], n_min: int) -> int | None:
    """최다 Cluster 선택. 최대 개수 < n_min이면 None(no-trade)."""
    if not counts:
        return None
    best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return best[0] if best[1] >= n_min else None


def pick_stocks(top_rows: list[dict], cluster: int, assignment: dict[str, int],
                k: int = PICK_K, predicate=None) -> list[str]:
    """선택 Cluster ∩ Top30 중 거래대금 상위 k 종목.

    predicate가 주어지면 후보 행에 적용하고 통과분만 남긴다
    (0개 통과 → no-trade, 1~2개 → 있는 만큼만).
    """
    in_cluster = [r for r in top_rows if assignment.get(r["ticker"]) == cluster]
    if predicate is not None:
        in_cluster = [r for r in in_cluster if predicate(r)]
    return [r["ticker"] for r in top_by_turnover(in_cluster, k)]
