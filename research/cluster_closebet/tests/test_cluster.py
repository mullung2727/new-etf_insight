"""표준화+KMeans TDD.

실행: repo root에서 `python -m unittest research.cluster_closebet.tests.test_cluster`
"""
import unittest

from research.cluster_closebet.cluster import (
    MIN_COVERAGE,
    cluster_labels,
    enough_coverage,
    standardize,
)


class TestStandardize(unittest.TestCase):
    def test_zscore(self):
        out = standardize([1.0, 2.0, 3.0])
        self.assertAlmostEqual(sum(out) / 3, 0.0)
        self.assertAlmostEqual(out[0], -1.2247449)

    def test_zero_std_returns_none(self):
        self.assertIsNone(standardize([0.5, 0.5, 0.5]))

    def test_coverage(self):
        self.assertTrue(enough_coverage(MIN_COVERAGE))
        self.assertFalse(enough_coverage(MIN_COVERAGE - 1))


class TestCluster(unittest.TestCase):
    MATRIX = [
        [3.0, 3.0], [3.1, 2.9], [2.9, 3.1],
        [-3.0, -3.0], [-3.1, -2.9], [-2.9, -3.1],
    ]

    def test_deterministic(self):
        first = cluster_labels(self.MATRIX, 2)
        second = cluster_labels(self.MATRIX, 2)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 6)

    def test_separates_groups(self):
        labels = cluster_labels(self.MATRIX, 2)
        self.assertEqual(len(set(labels[:3])), 1)
        self.assertEqual(len(set(labels[3:])), 1)
        self.assertNotEqual(labels[0], labels[3])


if __name__ == "__main__":
    unittest.main()
