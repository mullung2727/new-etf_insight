"""questions.py 단위 테스트 — 네트워크 없음, 가짜 응답만.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest research.jev_candidate.tests.test_questions
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from research.jev_candidate import questions
from research.jev_candidate.questions import (
    QUESTION_SET_VER,
    QUESTIONS,
    answer_cache_key,
    ask,
    jev_score,
    rule_buy,
)

SCORE_QIDS = ("q1", "q3", "q8", "q9", "q11")
NOUL_QIDS = ("q4", "q5", "q6", "q10")
QID_ORDER = ("q1", "q2", "q3", "q4", "q5", "q6", "q8", "q9", "q10", "q11")


def _answers(**over: object) -> dict:
    """기본값 가득 채운 가짜 answers — over 로 덮어쓴다."""
    ans: dict = {}
    for qid in SCORE_QIDS:
        ans[qid] = SimpleNamespace(score=1.6, confidence=0.9, probabilities={"0": 0.1, "1": 0.2, "2": 0.7})
    for qid in NOUL_QIDS:
        ans[qid] = SimpleNamespace(noul=0.8)
    ans["q2"] = SimpleNamespace(
        choice="macro", confidence=0.8, probabilities={"earnings": 0.1, "macro": 0.7, "other": 0.2}
    )
    ans.update(over)
    return ans


class FakeClient:
    """호출 1회 기록 — SimpleNamespace 응답 반환."""

    _DEFAULT = object()

    def __init__(self, answers: dict, usage: object = _DEFAULT):
        self._answers = answers
        self._usage = SimpleNamespace(input_tokens=10, output_tokens=5) if usage is self._DEFAULT else usage
        self.calls: list = []

    def system_one(self, st: str, qs: dict, model: str | None = None):
        self.calls.append((st, qs, model))
        return SimpleNamespace(answers=self._answers, usage=self._usage)


class DictCache:
    """ask 캐시 가짜 — get/put dict."""

    def __init__(self):
        self.d: dict = {}

    def get(self, key):
        return self.d.get(key)

    def put(self, key, rows, usage) -> None:
        self.d[key] = rows


def _rows(**over: object) -> list[dict]:
    """jev_score·rule_buy 용 행 직접 조립."""
    base: dict = {}
    for qid in SCORE_QIDS:
        base[qid] = {"qid": qid, "type": "score", "value": 1.6, "confidence": 0.9, "probs": {"1": 0.5}}
    for qid in NOUL_QIDS:
        base[qid] = {"qid": qid, "type": "noul", "value": 0.8, "confidence": None, "probs": {"yes": 0.8}}
    base["q2"] = {"qid": "q2", "type": "choice", "value": "macro", "confidence": 0.8, "probs": {}}
    base.update(over)
    return [base[q] for q in QUESTIONS]


class TestMeta(unittest.TestCase):
    def test_10개_형식(self):
        self.assertEqual(QUESTION_SET_VER, "v2")
        self.assertEqual(list(QUESTIONS), list(QID_ORDER))
        want = {
            "q1": "score", "q2": "choice", "q3": "score", "q4": "noul",
            "q5": "noul", "q6": "noul", "q8": "score",
            "q9": "score", "q10": "noul", "q11": "score",
        }
        self.assertEqual({k: v["type"] for k, v in QUESTIONS.items()}, want)
        self.assertNotIn("q7", QUESTIONS)

    def test_q2_7개_macro(self):
        crit = QUESTIONS["q2"]["criteria"]
        self.assertEqual(
            list(crit.values()),
            ["실적", "수주·계약", "정책·규제", "신제품·기술", "인수합병·지배구조", "산업·매크로", "수급·기타"],
        )
        self.assertIn("macro", crit)


class TestAsk(unittest.TestCase):
    def test_행정규화_usage(self):
        client = FakeClient(_answers())
        rows, usage = ask(client, "상태", model="m")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(usage, {"input_tokens": 10, "output_tokens": 5})
        by = {r["qid"]: r for r in rows}
        self.assertEqual([r["qid"] for r in rows], list(QID_ORDER))
        self.assertIsInstance(by["q1"]["value"], float)
        self.assertEqual(by["q2"]["value"], "macro")
        self.assertIsNone(by["q5"]["confidence"])
        self.assertEqual(by["q5"]["probs"], {"yes": 0.8})
        self.assertEqual(by["q1"]["probs"], {"0": 0.1, "1": 0.2, "2": 0.7})

    def test_usage_없어도(self):
        client = FakeClient(_answers(), usage=None)
        _rows_out, usage = ask(client, "상태")
        self.assertEqual(usage, {"input_tokens": None, "output_tokens": None})


class TestAskCache(unittest.TestCase):
    def test_미스후_히트(self):
        client = FakeClient(_answers())
        cache = DictCache()
        rows1, usage1 = ask(client, "상태", model="m", cache=cache)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(usage1, {"input_tokens": 10, "output_tokens": 5})
        rows2, usage2 = ask(client, "상태", model="m", cache=cache)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(rows2, rows1)
        self.assertEqual(usage2, {"input_tokens": 0, "output_tokens": 0, "cached": True})

    def test_키_결정성(self):
        self.assertEqual(answer_cache_key("s", "m"), answer_cache_key("s", "m"))
        self.assertNotEqual(answer_cache_key("s", "m"), answer_cache_key("t", "m"))
        self.assertNotEqual(answer_cache_key("s", "m1"), answer_cache_key("s", "m2"))

class TestScore(unittest.TestCase):
    def test_산술(self):
        # score 5개 × 1.6/2 = 4.0, + q5 0.8 − q4 0.2 − q10 0.1
        rows = _rows(
            q4={"qid": "q4", "type": "noul", "value": 0.2, "confidence": None, "probs": {}},
            q10={"qid": "q10", "type": "noul", "value": 0.1, "confidence": None, "probs": {}},
        )
        self.assertAlmostEqual(jev_score(rows), 4.0 + 0.8 - 0.2 - 0.1)

    def test_게이트(self):
        rows = _rows(
            q1={"qid": "q1", "type": "score", "value": 1.6, "confidence": 0.4, "probs": {}},
            q5={"qid": "q5", "type": "noul", "value": 0.5, "confidence": None, "probs": {}},
        )
        # q1 불명(0.8 빠짐) + q5 불명(0.8 빠짐) → 3.2 − 0.8 − 0.8
        self.assertAlmostEqual(jev_score(rows), 3.2 - 0.8 - 0.8)


class TestRule(unittest.TestCase):
    def test_참(self):
        rows = _rows(
            q1={"qid": "q1", "type": "score", "value": 1.6, "confidence": 0.9, "probs": {}},
            q11={"qid": "q11", "type": "score", "value": 0.6, "confidence": 0.9, "probs": {}},
            q10={"qid": "q10", "type": "noul", "value": 0.2, "confidence": None, "probs": {}},
        )
        self.assertTrue(rule_buy(rows))

    def test_경계_q1_1점0(self):
        rows = _rows(
            q1={"qid": "q1", "type": "score", "value": 1.0, "confidence": 0.9, "probs": {}},
            q11={"qid": "q11", "type": "score", "value": 0.5, "confidence": 0.9, "probs": {}},
            q10={"qid": "q10", "type": "noul", "value": 0.2, "confidence": None, "probs": {}},
        )
        self.assertTrue(rule_buy(rows))

    def test_q5_무관(self):
        # v2 — q5 조건 제거, 낮아도 참
        rows = _rows(
            q1={"qid": "q1", "type": "score", "value": 1.6, "confidence": 0.9, "probs": {}},
            q11={"qid": "q11", "type": "score", "value": 0.6, "confidence": 0.9, "probs": {}},
            q10={"qid": "q10", "type": "noul", "value": 0.2, "confidence": None, "probs": {}},
            q5={"qid": "q5", "type": "noul", "value": 0.1, "confidence": None, "probs": {}},
        )
        self.assertTrue(rule_buy(rows))

    def test_거짓들(self):
        base = dict(
            q1={"qid": "q1", "type": "score", "value": 1.6, "confidence": 0.9, "probs": {}},
            q11={"qid": "q11", "type": "score", "value": 0.6, "confidence": 0.9, "probs": {}},
            q10={"qid": "q10", "type": "noul", "value": 0.2, "confidence": None, "probs": {}},
        )
        # q1 미달
        self.assertFalse(rule_buy(_rows(**{**base, "q1": {**base["q1"], "value": 0.9}})))
        # q11 미달
        self.assertFalse(rule_buy(_rows(**{**base, "q11": {**base["q11"], "value": 0.4}})))
        # q10 반대재료
        self.assertFalse(rule_buy(_rows(**{**base, "q10": {**base["q10"], "value": 0.8}})))
        # q1 불명
        self.assertFalse(rule_buy(_rows(**{**base, "q1": {**base["q1"], "confidence": 0.4}})))

    def test_불명q10_거짓(self):
        rows = _rows(
            q1={"qid": "q1", "type": "score", "value": 1.9, "confidence": 0.9, "probs": {}},
            q11={"qid": "q11", "type": "score", "value": 1.0, "confidence": 0.9, "probs": {}},
            q10={"qid": "q10", "type": "noul", "value": 0.5, "confidence": None, "probs": {}},
        )
        self.assertFalse(rule_buy(rows))


if __name__ == "__main__":
    unittest.main()
