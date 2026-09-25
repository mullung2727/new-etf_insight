"""state.py 단위 테스트 — 네트워크 없음, 인메모리 sqlite + 가짜 클라이언트만.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest research.jev_candidate.tests.test_state
"""
from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from typesafe_sdk import TypeSafeError

from research.jev_candidate import state
from research.jev_candidate.state import (
    AMBIG_HI,
    AMBIG_LO,
    FILTER_PASS,
    JEV_MODEL,
    KST,
    anonymize,
    as_of_kst,
    build_state,
    filter_cache_key,
    filter_posts,
    filter_question,
    load_candidate_posts,
    mentions_target,
    price_sentence,
    section_bounds,
    shuffle_posts,
)

DAY = "2026-09-22"
NEXT = "2026-09-23"
NAME = "테스트종목"


def _db(rows: list[tuple]) -> sqlite3.Connection:
    """인메모리 telegram_posts — (channel, post_id, posted_at_utc, date_kst, text)."""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE telegram_posts (channel TEXT, post_id TEXT, post_ref TEXT,"
        " posted_at_utc TEXT, date_kst TEXT, text TEXT,"
        " links_json TEXT, raw_json TEXT, created_at TEXT, updated_at TEXT)"
    )
    con.executemany(
        "INSERT INTO telegram_posts"
        " (channel, post_id, posted_at_utc, date_kst, text) VALUES (?,?,?,?,?)",
        rows,
    )
    return con


def _kst(day: str, hm: str) -> datetime:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=KST)
    h, m = map(int, hm.split(":"))
    return d.replace(hour=h, minute=m)


def _iso(day: str, hm: str) -> str:
    return _kst(day, hm).isoformat()


class FakeClient:
    """순서대로 noul 값 반환 — Exception 이면 그대로 던짐. 호출 기록 남김."""

    def __init__(self, script: list[Any]):
        self._script = list(script)
        self.calls: list[tuple] = []

    def system_one(self, st: str, questions: dict, model: str | None = None):
        self.calls.append((st, questions, model))
        v = self._script.pop(0)
        if isinstance(v, Exception):
            raise v
        return SimpleNamespace(answers={"about": SimpleNamespace(noul=v)})


def _post(text: str, day: str = DAY, hm: str = "10:00") -> dict:
    return {"post_id": hm, "posted_at_kst": _kst(day, hm), "text": text}


class TestSectionBounds(unittest.TestCase):
    def test_close_1509_in_1511_out(self):
        bounds = dict((lb, (s, e)) for lb, s, e in section_bounds(DAY, "close", "2026-09-18"))
        s, e = bounds["[신호일]"]
        con = _db(
            [
                ("ch1", "p1", _iso(DAY, "15:09"), DAY, f"{NAME} 급등"),
                ("ch1", "p2", _iso(DAY, "15:11"), DAY, f"{NAME} 급등"),
                ("ch1", "p3", _iso(DAY, "16:00"), DAY, f"{NAME} 급등"),
            ]
        )
        # date_kst 셋 다 같아도 posted_at_utc 차단선으로 갈림
        got = load_candidate_posts(con, [NAME], s, e)
        self.assertEqual([p["post_id"] for p in got], ["p1"])

    def test_open_3섹션(self):
        secs = section_bounds(DAY, "open", "2026-09-18", next_trading_date=NEXT)
        self.assertEqual([lb for lb, _, _ in secs], ["[이전 2일]", "[신호일]", "[신호일 장마감 후]"])
        past, today, after = secs
        self.assertEqual((past[1], past[2]), (_kst("2026-09-18", "00:00"), _kst(DAY, "00:00")))
        self.assertEqual((today[1], today[2]), (_kst(DAY, "00:00"), _kst(DAY, "15:30")))
        self.assertEqual((after[1], after[2]), (_kst(DAY, "15:30"), _kst(NEXT, "08:00")))

    def test_이전2일_거래일기준_월요일(self):
        # D=월 2026-01-12, prev2=목 2026-01-08 → 금·토 포함, 수요외
        bounds = dict(
            (lb, (s, e)) for lb, s, e in section_bounds("2026-01-12", "close", "2026-01-08")
        )
        s, e = bounds["[이전 2일]"]
        self.assertEqual((s, e), (_kst("2026-01-08", "00:00"), _kst("2026-01-12", "00:00")))
        con = _db(
            [
                ("ch1", "fri", _iso("2026-01-09", "10:00"), "2026-01-09", f"{NAME} 금"),
                ("ch1", "sat", _iso("2026-01-10", "10:00"), "2026-01-10", f"{NAME} 토"),
                ("ch1", "wed", _iso("2026-01-07", "10:00"), "2026-01-07", f"{NAME} 수"),
            ]
        )
        got = load_candidate_posts(con, [NAME], s, e)
        self.assertEqual([p["post_id"] for p in got], ["fri", "sat"])

    def test_open_next_없으면_ValueError(self):
        with self.assertRaises(ValueError):
            as_of_kst(DAY, "open")

    def test_오늘_문자열_없음(self):
        for track, kw in (("close", {}), ("open", {"next_trading_date": NEXT})):
            for lb, _, _ in section_bounds(DAY, track, "2026-09-18", **kw):
                self.assertNotIn("오늘", lb)
        text, _ = build_state(1.0, [("[신호일]", []), ("[이전 2일]", [])], {})
        self.assertNotIn("오늘", text)
        self.assertNotIn("오늘", str(filter_question()))


class TestLoadAndFilter(unittest.TestCase):
    def test_awake_제외_중복제거(self):
        head = NAME + "가" * 195  # 앞 200자 동일
        con = _db(
            [
                ("awake_realtimeCheck", "bot", _iso(DAY, "10:00"), DAY, f"{NAME} 봇글"),
                ("ch1", "early", _iso(DAY, "10:00"), DAY, head + "꼬리1"),
                ("ch1", "late", _iso(DAY, "11:00"), DAY, head + "꼬리2"),
                ("ch2", "other", _iso(DAY, "10:30"), DAY, f"{NAME} 단독글"),
            ]
        )
        got = load_candidate_posts(con, [NAME], _kst(DAY, "00:00"), _kst(DAY, "15:10"))
        # 봇 채널 0건 + 중복은 최초 1건
        self.assertEqual([p["post_id"] for p in got], ["early", "other"])
        self.assertTrue(all(p["channel"] != "awake_realtimeCheck" for p in got))
        for p in got:
            self.assertIn("posted_at_kst", p)
            self.assertIn("text_hash", p)

    def test_filter_noul_판정(self):
        posts = [_post(f"{NAME} 글1"), _post(f"{NAME} 글2"), _post(f"{NAME} 글3")]
        client = FakeClient([0.4, 0.7, TypeSafeError("끊김")])
        got = filter_posts(client, posts, {NAME: "종목A"})
        self.assertEqual(
            [(r["about_noul"], r["passed"], r["ambiguous"]) for r in got],
            [(0.4, False, True), (0.7, True, False), (None, False, False)],
        )
        self.assertEqual(got[2]["error"], "TypeSafeError")
        # 경계값 그대로
        self.assertGreaterEqual(0.7, FILTER_PASS)
        self.assertTrue(AMBIG_LO <= 0.4 <= AMBIG_HI)
        # 익명화돼서 넘어가고 모델 고정
        for st_text, _q, model in client.calls:
            self.assertNotIn(NAME, st_text)
            self.assertEqual(model, JEV_MODEL)


class TestAnonymize(unittest.TestCase):
    def test_긴키_우선(self):
        mapping = {"SK하이닉스": "종목B", "SK": "종목C", "삼성전자": "종목A", "005930": "종목A"}
        out = anonymize("SK하이닉스와 SK, 삼성전자(005930)", mapping)
        self.assertEqual(out, "종목B와 종목C, 종목A(종목A)")
        for real in mapping:
            self.assertNotIn(real, out)


class TestPriceSentence(unittest.TestCase):
    def test_경계(self):
        self.assertEqual(price_sentence(0), "[가격] 신호일 0~+5% 상승")
        self.assertEqual(price_sentence(10), "[가격] 신호일 +10~+20% 상승")
        self.assertEqual(price_sentence(-10), "[가격] 신호일 0~-10% 하락")
        self.assertEqual(price_sentence(-10.01), "[가격] 신호일 -10% 미만 하락")


class TestBuildState(unittest.TestCase):
    def test_기본_최신순_빈섹션(self):
        mapping = {NAME: "종목A"}
        secs = [
            ("[신호일]", [_post(f"{NAME} 옛글", hm="09:00"), _post(f"{NAME} 새글", hm="10:00")]),
            ("[이전 2일]", []),
        ]
        text, stats = build_state(12.3, secs, mapping)
        lines = text.split("\n")
        self.assertEqual(lines[0], "[가격] 신호일 +10~+20% 상승")
        self.assertLess(lines.index("- 종목A 새글"), lines.index("- 종목A 옛글"))
        self.assertIn("관련 글 없음", text)
        self.assertEqual(stats["n_passed"], 2)
        self.assertEqual(stats["n_included"], 2)
        self.assertFalse(stats["truncated"])
        self.assertNotIn(NAME, text)

    def test_자름_뒤섹션_중단(self):
        big = "가" * 4000  # 약 2000토큰씩
        secs = [
            ("[신호일]", [_post(big, hm="09:00"), _post(big, hm="10:00"), _post(big, hm="11:00")]),
            ("[이전 2일]", [_post(big, hm="08:00"), _post(big, hm="07:00")]),
        ]
        text, stats = build_state(1.0, secs, {})
        self.assertTrue(stats["truncated"])
        # 신호일 1건만 들어가고 이전 2일은 라벨 + 관련 글 없음
        self.assertEqual(stats["n_by_section"]["[신호일]"], 1)
        self.assertEqual(stats["n_by_section"]["[이전 2일]"], 0)
        tail = text.split("[이전 2일]")[1]
        self.assertIn("관련 글 없음", tail)
        self.assertNotIn("오늘", text)


class TestShuffle(unittest.TestCase):
    def test_같은구간_맞바꿈_단독유지_결정성(self):
        groups = {
            "a": [_post("a글")],
            "b": [_post("b글")],
            "c": [_post("c글")],
        }
        buckets = {"a": "X", "b": "X", "c": "Y"}
        assign, unswapped = shuffle_posts(groups, buckets, seed=7)
        # 다수 구간은 derangement, 단독 구간은 identity + 보고
        self.assertEqual(assign["a"], "b")
        self.assertEqual(assign["b"], "a")
        self.assertEqual(assign["c"], "c")
        self.assertEqual(unswapped, ["c"])
        again, _ = shuffle_posts(groups, buckets, seed=7)
        self.assertEqual(assign, again)

    def test_빈글도_참가(self):
        groups = {"a": [_post("a글")], "b": [], "c": [_post("c글")]}
        buckets = {"a": "X", "b": "X", "c": "X"}
        assign, unswapped = shuffle_posts(groups, buckets, seed=3)
        self.assertEqual(unswapped, [])
        for t in groups:
            self.assertNotEqual(assign[t], t)


class TestMentionsTarget(unittest.TestCase):
    def test_증권사만_False(self):
        self.assertFalse(mentions_target("현대차증권 김종배", ["현대차"]))

    def test_괄호약어만_False(self):
        self.assertFalse(mentions_target("(현대차) 2분기부터가 진짜다", ["현대차"]))

    def test_작성자_False(self):
        self.assertFalse(mentions_target("작성자: SK증권 (리서치센터)", ["SK증권"]))

    def test_섞이면_True(self):
        self.assertTrue(mentions_target("현대차증권 리포트 ... 현대차 목표가 상향", ["현대차"]))

    def test_보통_True(self):
        self.assertTrue(mentions_target("현대차 (005380) 급등", ["현대차"]))

    def test_load_오탐버림(self):
        con = _db(
            [
                ("ch1", "fake", _iso(DAY, "10:00"), DAY, "현대차증권 김종배"),
                ("ch1", "real", _iso(DAY, "10:01"), DAY, "현대차 목표가 상향"),
            ]
        )
        got = load_candidate_posts(con, ["현대차"], _kst(DAY, "00:00"), _kst(DAY, "15:10"))
        self.assertEqual([p["post_id"] for p in got], ["real"])


class _DictFilterCache:
    """filter_posts 캐시 가짜 — get/put dict."""

    def __init__(self):
        self.d: dict[str, float] = {}

    def get(self, key: str):
        return self.d.get(key)

    def put(self, key: str, noul: float) -> None:
        self.d[key] = noul


class TestFilterCache(unittest.TestCase):
    def test_두번째는_호출없음(self):
        posts = [_post(f"{NAME} 글1")]
        client = FakeClient([0.7])
        cache = _DictFilterCache()
        got1 = filter_posts(client, posts, {NAME: "종목A"}, cache=cache)
        got2 = filter_posts(client, posts, {NAME: "종목A"}, cache=cache)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(got1[0]["about_noul"], 0.7)
        self.assertEqual(got2[0]["about_noul"], 0.7)
        self.assertTrue(got2[0]["passed"])

    def test_에러는_캐시안함(self):
        posts = [_post(f"{NAME} 글1")]
        client = FakeClient([TypeSafeError("끊김"), 0.7])
        cache = _DictFilterCache()
        got1 = filter_posts(client, posts, {NAME: "종목A"}, cache=cache)
        self.assertIsNone(got1[0]["about_noul"])
        self.assertEqual(cache.d, {})
        got2 = filter_posts(client, posts, {NAME: "종목A"}, cache=cache)
        self.assertEqual(got2[0]["about_noul"], 0.7)
        got3 = filter_posts(client, posts, {NAME: "종목A"}, cache=cache)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(got3[0]["about_noul"], 0.7)

    def test_키_익명화_결정성(self):
        k1 = filter_cache_key(f"{NAME} 글", {NAME: "종목A"})
        k2 = filter_cache_key(f"{NAME} 글", {NAME: "종목A"})
        k3 = filter_cache_key(f"{NAME} 글", {NAME: "종목B"})
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)


if __name__ == "__main__":
    unittest.main()
