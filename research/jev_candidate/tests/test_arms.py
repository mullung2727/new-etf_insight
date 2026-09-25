"""arms.py + run_days._i_state 단위 테스트 — 네트워크 없음, :memory: 만.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest research.jev_candidate.tests.test_arms
"""
from __future__ import annotations

import unittest

from research.jev_candidate import arms, questions, state, store
from research.jev_candidate.run_days import _i_state

DAY = "20260409"
VER = "v2"
RUN = f"backtest-open-{DAY}-{VER}"

SCORE_QIDS = ("q1", "q3", "q8", "q9", "q11")


def _rows(s: float = 1.6, q5: float = 0.0, q4: float = 0.0, q10: float = 0.2) -> list[dict]:
    """jev_score = 5*s/2 + q5 − q4 − q10 으로 고정되는 행."""
    rows = []
    for qid in SCORE_QIDS:
        rows.append({"qid": qid, "type": "score", "value": s, "confidence": 0.9, "probs": {}})
    rows.append({"qid": "q2", "type": "choice", "value": "macro", "confidence": 0.9, "probs": {}})
    rows.append({"qid": "q6", "type": "noul", "value": 0.0, "confidence": None, "probs": {}})
    rows.append({"qid": "q5", "type": "noul", "value": q5, "confidence": None, "probs": {}})
    rows.append({"qid": "q4", "type": "noul", "value": q4, "confidence": None, "probs": {}})
    rows.append({"qid": "q10", "type": "noul", "value": q10, "confidence": None, "probs": {}})
    return rows


def _buy(s: float = 1.6, q11: float = 0.6) -> list[dict]:
    """규칙 통과 행 — q1≥1.0, q11≥0.5, q10=0.2."""
    rows = _rows(s=s, q10=0.2)
    by = {r["qid"]: r for r in rows}
    by["q11"]["value"] = q11
    return rows


def _nobuys(s: float = 1.6) -> list[dict]:
    """규칙 탈락 행 — q1 낮음."""
    rows = _rows(s=0.4, q10=0.2)
    return rows


def _db_with(main_ans: dict, i_ans: dict, states: list[dict]):
    """decide_day 용 :memory: DB."""
    con = store.connect(":memory:")
    store.ensure_schema(con)
    for t, rows in main_ans.items():
        store.save_answers(con, RUN, DAY, t, rows)
    for t, rows in i_ans.items():
        store.save_answers(con, RUN + "-I", DAY, t, rows)
    for s in states:
        store.save_state(con, {"run_id": RUN, "date": DAY, **s})
    return con


def _st(ticker: str, price_pct: float = 1.0, n_passed: int = 1) -> dict:
    return {
        "ticker": ticker, "name": f"종목{ticker}", "price_pct": price_pct,
        "state_text": "t", "state_hash": "h", "anon_map_json": "{}",
        "n_candidates": n_passed, "n_passed": n_passed, "n_ambiguous": 0,
        "n_included": 1, "truncated": False,
    }


class TestPickA(unittest.TestCase):
    def test_상위3_동률_티커순(self):
        ans = {
            "000004": _rows(s=0.0),  # 0.0 − 0.2 = −0.2
            "000002": _rows(s=1.0),  # 2.5 − 0.2 = 2.3
            "000003": _rows(s=1.0),  # 동률
            "000001": _rows(s=2.0),  # 5.0 − 0.2 = 4.8
        }
        got = arms.pick_A(ans)
        self.assertEqual([p["ticker"] for p in got], ["000001", "000002", "000003"])
        self.assertEqual([p["rank"] for p in got], [1, 2, 3])
        self.assertTrue(all(p["decision"] == "BUY" for p in got))
        self.assertAlmostEqual(got[0]["score"], questions.jev_score(ans["000001"]))


class TestPickB(unittest.TestCase):
    def test_규칙만_점수순(self):
        ans = {
            "000001": _buy(s=1.0, q11=0.6),  # 통과, 낮음
            "000002": _nobuys(),  # 탈락
            "000003": _buy(s=2.0, q11=1.8),  # 통과, 높음
        }
        got = arms.pick_B(ans)
        self.assertEqual([p["ticker"] for p in got], ["000003", "000001"])

    def test_없으면_빈목록(self):
        self.assertEqual(arms.pick_B({"000001": _nobuys()}), [])


class TestPickGH(unittest.TestCase):
    def test_G_등락률_동률_티커순(self):
        states = [_st("000002", 5.0), _st("000001", 5.0), _st("000003", 9.0), _st("000004", -1.0)]
        got = arms.pick_G(states)
        self.assertEqual([p["ticker"] for p in got], ["000003", "000001", "000002"])

    def test_H_언급량_동률_등락률순(self):
        states = [_st("a", 1.0, 5), _st("b", 9.0, 5), _st("c", 20.0, 2)]
        got = arms.pick_H(states)
        self.assertEqual([p["ticker"] for p in got], ["b", "a", "c"])
        self.assertEqual(got[0]["score"], 5.0)


class TestDecideDay(unittest.TestCase):
    def test_I는_별도run_저장은_주run(self):
        main_ans = {"000001": _buy(s=2.0), "000002": _buy(s=1.0)}
        i_ans = {"000002": _buy(s=2.0), "000001": _buy(s=1.0)}
        con = _db_with(main_ans, i_ans, [_st("000001", 3.0, 4), _st("000002", 1.0, 9)])
        rows = arms.decide_day(con, DAY, VER)
        by_arm = {}
        for r in rows:
            by_arm.setdefault(r["arm"], []).append(r["ticker"])
        self.assertEqual(by_arm["A"], ["000001", "000002"])
        self.assertEqual(by_arm["I_A"], ["000002", "000001"])  # -I 답 기준
        self.assertEqual(by_arm["I_B"], ["000002", "000001"])
        self.assertEqual(by_arm["G"], ["000001", "000002"])
        self.assertEqual(by_arm["H"], ["000002", "000001"])
        self.assertEqual(by_arm["E"], ["000001", "000002"])
        self.assertTrue(all(r["run_id"] == RUN for r in rows))  # I arm 도 주 run_id
        store.save_decisions(con, rows)
        n = con.execute("SELECT COUNT(*) FROM decisions WHERE run_id = ?", (RUN,)).fetchone()[0]
        self.assertEqual(n, len(rows))
        # loader 왕복 — score·choice 타입 복원
        loaded = store.load_answers(con, RUN)
        by = {r["qid"]: r for r in loaded["000001"]}
        self.assertIsInstance(by["q1"]["value"], float)
        self.assertIsInstance(by["q2"]["value"], str)
        self.assertEqual([s["ticker"] for s in store.load_states(con, RUN)], ["000001", "000002"])


class TestIState(unittest.TestCase):
    def test_가격dst_글src_익명화(self):
        top30 = [{"ticker": "111111", "name": "갑종목"}, {"ticker": "222222", "name": "을종목"}]
        dst = {"ticker": "111111", "name": "갑종목"}
        src = {"ticker": "222222", "name": "을종목"}
        src_posts = {"[신호일]": [{"text": "을종목 수주 대박", "posted_at_kst": "x", "section": "[신호일]"}]}
        text, stats, mapping = _i_state(dst, src, 12.3, src_posts, top30)
        self.assertIn("종목A", text)
        self.assertNotIn("을종목", text)
        self.assertNotIn("222222", text)
        self.assertEqual(text.split("\n")[0], state.price_sentence(12.3))
        self.assertEqual(mapping["src"], "222222")

    def test_다수버킷_dst와src다름(self):
        groups = {"a": [{"text": "x"}], "b": [{"text": "y"}]}
        buckets = {"a": "[가격] 신호일 0~+5% 상승", "b": "[가격] 신호일 0~+5% 상승"}
        assign, unswapped = state.shuffle_posts(groups, buckets, seed=int(DAY))
        self.assertEqual(unswapped, [])
        for dst, src in assign.items():
            self.assertNotEqual(dst, src)


if __name__ == "__main__":
    unittest.main()
