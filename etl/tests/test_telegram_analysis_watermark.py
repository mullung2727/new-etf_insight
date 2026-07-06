"""telegram_analysis_watermark.py 단위 테스트.

검증 항목:
  1. ensure_schema 멱등 / 빈 테이블 read = {}
  2. advance_watermarks: 채널별 전진, 재실행 시 max()로만 전진(뒤로 안 밀림)
  3. read 후 advance 후 read 라운드트립
"""
import sqlite3
import unittest

from scripts.telegram_analysis_watermark import (
    advance_watermarks,
    ensure_schema,
    read_watermarks,
)


class WatermarkTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        ensure_schema(self.con)

    def test_empty_read(self):
        self.assertEqual(read_watermarks(self.con), {})

    def test_advance_and_read(self):
        advance_watermarks(self.con, {"getfeed": 100, "corevalue": 50})
        self.assertEqual(read_watermarks(self.con), {"getfeed": 100, "corevalue": 50})

    def test_advance_moves_forward_only(self):
        advance_watermarks(self.con, {"getfeed": 100})
        advance_watermarks(self.con, {"getfeed": 80})  # 낮은 값 → 무시
        self.assertEqual(read_watermarks(self.con)["getfeed"], 100)
        advance_watermarks(self.con, {"getfeed": 150})  # 높은 값 → 전진
        self.assertEqual(read_watermarks(self.con)["getfeed"], 150)

    def test_ensure_schema_idempotent(self):
        ensure_schema(self.con)
        ensure_schema(self.con)


if __name__ == "__main__":
    unittest.main()
