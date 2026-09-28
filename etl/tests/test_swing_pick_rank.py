"""스윙 순위(rank) 테스트.

실행 (etl 폴더): PYTHONPATH=. uv run python -m unittest tests.test_swing_pick_rank
"""
import unittest

from scripts.swing_pick.rank import pick_top, rank_candidates, total_score


def _row(ticker, s1=2, s3=2, s4=2, s5=2, sustain=1.5, risk_out=False,
         excluded=None, total=None):
    return {"ticker": ticker, "s1": s1, "s3": s3, "s4": s4, "s5": s5,
            "jev_sustain": sustain, "risk_out": risk_out,
            "excluded_reason": excluded, "total": total}


def _ok(ticker):
    return {"order_warning": "", "audit_info": "정상"}


class TestTotalScore(unittest.TestCase):
    def test_none_is_zero(self):
        self.assertEqual(total_score(_row("A")), 8)
        self.assertEqual(total_score(_row("A", s1=None, s4=None)), 4)
        self.assertEqual(
            total_score({"s1": None, "s3": None, "s4": None, "s5": None}), 0)


class TestRankCandidates(unittest.TestCase):
    def test_order_and_ties(self):
        rows = [
            _row("000003", s5=0, sustain=2.0),   # 6점
            _row("000002", s5=1, sustain=1.0),   # 7점 저sustain
            _row("000001", s5=1, sustain=1.9),   # 7점 고sustain
            _row("000004", s5=1, sustain=1.0),   # 7점 저sustain, 코드 큼
            _row("000005", s5=1, sustain=None),  # 7점 sustain 없음 → 꼴찌
        ]
        got = [r["ticker"] for r in rank_candidates(rows)]
        self.assertEqual(got, ["000001", "000002", "000004", "000005", "000003"])

    def test_excludes_risk_and_cut(self):
        rows = [
            _row("000001"),
            _row("000002", risk_out=True),          # 리스크 탈락
            _row("000003", risk_out=None, excluded="jev_error"),  # Jev 실패
            _row("000004", excluded="cut:low_value"),  # 1차 컷
        ]
        self.assertEqual(
            [r["ticker"] for r in rank_candidates(rows)], ["000001"])

    def test_input_not_mutated(self):
        rows = [_row("B", s5=0), _row("A")]
        before = [dict(r) for r in rows]
        rank_candidates(rows)
        self.assertEqual(rows, before)

    def test_risk_out_8pointer_not_in_top3(self):
        # 설계 §4 첫 줄: 2번 걸리면 점수 무관 탈락.
        rows = [_row(f"00000{i}", s5=0) for i in range(1, 5)]  # 6점 4개
        rows.append(_row("999999", risk_out=True))  # 8점 + 리스크
        got = [r["ticker"] for r in rank_candidates(rows)[:3]]
        self.assertNotIn("999999", got)
        self.assertEqual(len(got), 3)


class TestPickTop(unittest.TestCase):
    def test_skip_excluded_and_fill(self):
        ordered = [_row(t) for t in
                   ["000001", "000002", "000003", "000004", "000005"]]
        status = {
            "000001": {"order_warning": "2", "audit_info": "정상"},      # 정리매매
            "000002": {"order_warning": "", "audit_info": "관리종목"},   # 관리종목
            "000003": {"order_warning": "", "audit_info": "거래정지"},   # 거래정지
            "000004": _ok("000004"),
            "000005": _ok("000005"),
        }
        picked, reasons = pick_top(ordered, lambda t: status[t], k=3)
        # 3개를 못 채움 — 000004·000005만 통과, 앞에서 멈추지 않고 끝까지 봄
        self.assertEqual([r["ticker"] for r in picked], ["000004", "000005"])
        self.assertEqual(reasons["000001"], "정리매매")
        self.assertEqual(reasons["000002"], "관리종목")
        self.assertEqual(reasons["000003"], "거래정지")
        self.assertNotIn("000004", reasons)  # 깨끗한 통과는 기록 없음

    def test_unknown_passes_through(self):
        ordered = [_row(t) for t in ["000001", "000002"]]

        def flaky(t):
            if t == "000001":
                raise ConnectionError("down")
            return None
        picked, reasons = pick_top(ordered, flaky, k=3)
        self.assertEqual([r["ticker"] for r in picked], ["000001", "000002"])
        self.assertEqual(reasons, {"000001": "status_unknown",
                                   "000002": "status_unknown"})

    def test_max_checks(self):
        ordered = [_row(f"{i:06d}") for i in range(1, 15)]
        calls = []
        picked, _ = pick_top(
            ordered,
            lambda t: (calls.append(t), {"order_warning": "2",
                                         "audit_info": ""})[1],
            k=3, max_checks=10)
        # 전부 정리매매 → 10번 조회 후 멈춤, 선정 0개
        self.assertEqual(len(calls), 10)
        self.assertEqual(picked, [])

    def test_stops_when_filled(self):
        ordered = [_row(f"{i:06d}") for i in range(1, 10)]
        calls = []
        picked, _ = pick_top(
            ordered, lambda t: (calls.append(t), _ok(t))[1], k=3)
        self.assertEqual(len(picked), 3)
        self.assertEqual(len(calls), 3)  # 4번째는 조회 안 함


if __name__ == "__main__":
    unittest.main()
