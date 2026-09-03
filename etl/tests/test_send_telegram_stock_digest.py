import unittest

from scripts.send_telegram_stock_digest import format_digest


class FormatTelegramDigestTest(unittest.TestCase):
    def _highlight(self, score=82):
        return {
            "title": "AI 인프라 수요 논쟁",
            "summary": "저비용 AI 모델과 GPU·HBM 수요 전망이 함께 부각됐다.",
            "category": "산업",
            "importance_reason": "글로벌 반도체 투자심리와 연결된다.",
            "score_total": score,
            "source_channels": ["getfeed", "infomarketopen"],
        }

    def test_highlight_only_still_creates_digest(self):
        msg = format_digest(
            "2026-07-18", "close", rows=[], highlights=[self._highlight()]
        )
        self.assertIsNotNone(msg)
        self.assertIn("🧭 텔레그램 세션 개괄", msg)
        self.assertIn("[82점] AI 인프라 수요 논쟁", msg)
        self.assertIn("가치: 글로벌 반도체 투자심리", msg)
        self.assertIn("정보가치 점수", msg)

    def test_highlights_are_added_before_existing_stock_section(self):
        rows = [{
            "ticker": "005930", "name": "삼성전자", "channels": ["getfeed"],
            "change_type": "new", "change_summary": "메모리 수요 부각", "themes": ["반도체"],
        }]
        msg = format_digest(
            "2026-07-18", "close", rows=rows, highlights=[self._highlight()]
        )
        self.assertLess(msg.index("🧭 텔레그램 세션 개괄"), msg.index("📊 종목 요약"))
        self.assertIn("삼성전자(005930)", msg)

    def test_multiline_summary_becomes_one_bullet_per_line(self):
        item = self._highlight()
        item["summary"] = "저비용 AI 모델 확산\nGPU·HBM 수요 전망 상향"
        msg = format_digest("2026-07-18", "close", rows=[], highlights=[item])
        self.assertIn("  - 저비용 AI 모델 확산", msg)
        self.assertIn("  - GPU·HBM 수요 전망 상향", msg)

    def test_single_paragraph_summary_stays_one_line(self):
        """개조식 이전에 쌓인 행은 기계적으로 쪼개지 않는다."""
        msg = format_digest("2026-07-18", "close", rows=[], highlights=[self._highlight()])
        summary_lines = [ln for ln in msg.splitlines() if ln.startswith("  - ")]
        self.assertEqual(len(summary_lines), 1)

    def test_llm_supplied_bullet_marker_is_not_doubled(self):
        item = self._highlight()
        item["summary"] = "- 저비용 AI 모델 확산\n• GPU 수요 상향"
        msg = format_digest("2026-07-18", "close", rows=[], highlights=[item])
        self.assertIn("  - 저비용 AI 모델 확산", msg)
        self.assertIn("  - GPU 수요 상향", msg)
        self.assertNotIn("- - ", msg)

    def test_empty_highlights_and_stocks_returns_none(self):
        self.assertIsNone(format_digest("2026-07-18", "close", rows=[], highlights=[]))


if __name__ == "__main__":
    unittest.main()
