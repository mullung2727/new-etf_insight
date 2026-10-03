"""선정 로직 TDD — Top30 + Count + N + Top3.

실행: repo root에서
`etl\\.venv\\Scripts\\python.exe -m unittest research.cluster_closebet.tests.test_select -v`
"""
import unittest

from research.cluster_closebet.select import (
    cluster_counts,
    pick_cluster,
    pick_stocks,
    top_by_turnover,
)


def _row(ticker, turnover):
    return {"ticker": ticker, "turnover": turnover}


class TestTopByTurnover(unittest.TestCase):
    def test_truncates_to_30(self):
        rows = [_row(f"{i:06d}", i) for i in range(40)]
        self.assertEqual(len(top_by_turnover(rows)), 30)

    def test_descending(self):
        rows = [_row("A", 1), _row("B", 3), _row("C", 2)]
        self.assertEqual([r["ticker"] for r in top_by_turnover(rows)], ["B", "C", "A"])


class TestClusterCounts(unittest.TestCase):
    def test_counts_and_unassigned_ignored(self):
        rows = [_row("A", 3), _row("B", 2), _row("C", 1)]
        assign = {"A": 17, "B": 17}  # C는 배정 없음
        self.assertEqual(cluster_counts(rows, assign), {17: 2})


class TestPickCluster(unittest.TestCase):
    def test_no_trade_below_n(self):
        self.assertIsNone(pick_cluster({17: 2, 42: 1}, 3))

    def test_trades_at_n(self):
        self.assertEqual(pick_cluster({17: 3, 42: 1}, 3), 17)

    def test_tie_break_is_deterministic(self):
        # 동점이면 cluster id 오름차순
        self.assertEqual(pick_cluster({42: 3, 17: 3}, 3), 17)


class TestPickStocks(unittest.TestCase):
    def test_top3_in_cluster(self):
        rows = [_row("A", 5), _row("B", 4), _row("C", 3), _row("D", 2)]
        assign = {"A": 17, "B": 42, "C": 17, "D": 17}
        self.assertEqual(pick_stocks(rows, 17, assign, 3), ["A", "C", "D"])

    def test_fewer_than_k_returns_available(self):
        rows = [_row("A", 5), _row("B", 4)]
        assign = {"A": 17, "B": 42}
        self.assertEqual(pick_stocks(rows, 17, assign, 3), ["A"])


class TestPickStocksPredicate(unittest.TestCase):
    def _setup(self):
        rows = [_row(t, turn) for t, turn in
                [("A", 5), ("B", 4), ("C", 3), ("D", 2), ("E", 1)]]
        assign = {t: 17 for t in ("A", "B", "C", "D", "E")}
        return rows, assign

    def test_zero_pass_returns_empty(self):
        rows, assign = self._setup()
        self.assertEqual(
            pick_stocks(rows, 17, assign, 3, predicate=lambda r: False), [])

    def test_one_pass_returns_one(self):
        rows, assign = self._setup()
        got = pick_stocks(rows, 17, assign, 3,
                          predicate=lambda r: r["ticker"] == "D")
        self.assertEqual(got, ["D"])

    def test_five_pass_returns_top3_by_turnover(self):
        rows, assign = self._setup()
        got = pick_stocks(rows, 17, assign, 3, predicate=lambda r: True)
        self.assertEqual(got, ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
