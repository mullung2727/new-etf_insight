"""outcomes.py 단위 테스트 — compute_open 순수 계산 + T8 분리.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest research.jev_candidate.tests.test_outcomes
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from research.jev_candidate import outcomes
from research.jev_candidate.outcomes import compute_open

D0 = 10000


def _d1(open_, high=10500, low=10100, close=10400, volume=1000) -> dict:
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _bars(*pairs) -> list[dict]:
    return [{"time": t, "close": c} for t, c in pairs]


class TestComputeOpen(unittest.TestCase):
    def test_정상(self):
        r = compute_open(D0, _d1(10200), _bars(("092500", 10300), ("092900", 10350)))
        self.assertIsNone(r["excluded_open"])
        self.assertEqual(r["d1_0930"], 10350)
        self.assertAlmostEqual(r["ret_open_0930"], 10350 / 10200 - 1)
        self.assertAlmostEqual(r["ret_open_close"], 10400 / 10200 - 1)
        self.assertAlmostEqual(r["max_ret_o"], 10500 / 10200 - 1)
        self.assertAlmostEqual(r["min_ret_o"], 10100 / 10200 - 1)

    def test_0930경계_직전봉(self):
        r = compute_open(
            D0, _d1(10200), _bars(("092900", 10300), ("093000", 10400), ("093100", 10500))
        )
        self.assertEqual(r["d1_0930"], 10300)

    def test_권리락갭(self):
        r = compute_open(D0, _d1(6500), _bars(("092900", 6500)))
        self.assertEqual(r["excluded_open"], "gap_artifact")
        self.assertIsNone(r["ret_open_close"])
        self.assertEqual(r["d1_open"], 6500)  # 원시가는 유지

    def test_상한가시가(self):
        r = compute_open(D0, _d1(13000), _bars(("092900", 13100)))
        self.assertEqual(r["excluded_open"], "limit_up")
        self.assertAlmostEqual(r["ret_open_close"], 10400 / 13000 - 1)  # 계산 유지

    def test_분봉없음(self):
        r = compute_open(D0, _d1(10200), [])
        self.assertIsNone(r["d1_0930"])
        self.assertIsNone(r["ret_open_0930"])
        self.assertIsNone(r["excluded_open"])
        self.assertAlmostEqual(r["ret_open_close"], 10400 / 10200 - 1)

    def test_거래정지(self):
        r = compute_open(D0, None, [])
        self.assertEqual(r["excluded_open"], "no_trade")
        self.assertIsNone(r["d1_open"])
        r = compute_open(D0, _d1(10200, volume=0), [])
        self.assertEqual(r["excluded_open"], "no_trade")
        self.assertIsNone(r["ret_open_close"])

    def test_T8_판단모듈_outcomes미참조(self):
        # §12 T8: import·조회 금지 (grep). arms.py 독스트링 언급("조회 없음")은 허용 —
        # bare-string 단언은 기존 문구와 충돌해 쓸 수 없다.
        pat = re.compile(r"(import|from)\s+[\w.]*outcomes|load_outcomes|save_outcomes|FROM outcomes")
        base = Path(outcomes.__file__).resolve().parent
        for name in ("arms.py", "questions.py", "state.py"):
            text = (base / name).read_text(encoding="utf-8")
            self.assertIsNone(pat.search(text), name)


if __name__ == "__main__":
    unittest.main()
