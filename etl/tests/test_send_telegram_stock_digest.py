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

    def test_empty_highlights_and_stocks_returns_none(self):
        self.assertIsNone(format_digest("2026-07-18", "close", rows=[], highlights=[]))


if __name__ == "__main__":
    unittest.main()
