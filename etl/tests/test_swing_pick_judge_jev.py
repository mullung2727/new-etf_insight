"""스윙 Jev 판정(judge_jev) 테스트.

Jev 클라이언트·fetch 함수는 가짜로 주입한다 (네트워크 금지).
DB는 TemporaryDirectory에 스키마 만들어 적재한다. telegram·youtube 테이블은
etl/docs/DB_SCHEMA.md 컬럼명 그대로(제약 없이) 만들고, report_api_facts는
scripts/report_metrics/storage.init_db로 만든다.

실행 (etl 폴더): PYTHONPATH=. uv run python -m unittest tests.test_swing_pick_judge_jev
"""
import hashlib
import sqlite3
import unittest
from datetime import datetime
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from scripts.report_metrics import storage
from scripts.swing_pick.judge_jev import (
    QUESTIONS,
    STATE_TOKEN_LIMIT,
    approx_tokens,
    ask_jev,
    build_input,
    collect_filings,
    judge_jev,
    load_reports,
    load_telegram_posts,
    load_youtube_items,
    risk_out_from,
    s1_from_score,
)

_TG_INSIGHTS_DDL = """CREATE TABLE telegram_stock_insights (
    date_kst TEXT, session TEXT, ticker TEXT, name TEXT,
    mention_channels TEXT, source_post_refs TEXT,
    discovery_reason TEXT, analysis TEXT, created_at TEXT, updated_at TEXT)"""
_TG_POSTS_DDL = """CREATE TABLE telegram_posts (
    channel TEXT, post_id INTEGER, post_ref TEXT, posted_at_utc TEXT,
    date_kst TEXT, text TEXT, links_json TEXT, raw_json TEXT,
    created_at TEXT, updated_at TEXT)"""
_YT_INSIGHTS_DDL = """CREATE TABLE youtube_stock_insights (
    date_kst TEXT, ticker TEXT, name TEXT, mention_channels TEXT,
    source_video_ids TEXT, discovery_reason TEXT, analysis TEXT,
    created_at TEXT, updated_at TEXT)"""
_YT_SUM_DDL = """CREATE TABLE youtube_video_summaries (
    channel_id TEXT, video_id TEXT, date_kst TEXT, model TEXT,
    summary_json TEXT, created_at TEXT, updated_at TEXT)"""


def _mkdb(path, ddl_list):
    con = sqlite3.connect(path)
    for ddl in ddl_list:
        con.execute(ddl)
    return con


class _ScoreAns:
    def __init__(self, score):
        self.score = score


class _NoulAns:
    def __init__(self, noul):
        self.noul = noul


class _Resp:
    def __init__(self, score, noul, usage):
        self.answers = {"sustain": _ScoreAns(score), "risk": _NoulAns(noul)}
        self.usage = usage


class _ObjUsage:
    def __init__(self, n):
        self.input_tokens = n


class MarkerClient:
    """본문 포함 마커 → (score, noul, 토큰). 값 대신 예외를 넣으면 raise."""

    def __init__(self, table, default=(1.0, 0.1, 10)):
        self.table = table
        self.default = default
        self.calls = []

    def system_one(self, text, questions, model=None):
        self.calls.append({"text": text, "questions": questions, "model": model})
        for marker, val in self.table.items():
            if marker in text:
                if isinstance(val, Exception):
                    raise val
                score, noul, tok = val
                return _Resp(score, noul, _ObjUsage(tok))
        score, noul, tok = self.default
        return _Resp(score, noul, _ObjUsage(tok))


class TestConvert(unittest.TestCase):
    def test_s1_boundaries(self):
        cases = [(0.66, 0), (0.67, 1), (1.32, 1), (1.33, 2), (0.0, 0), (2.0, 2)]
        for score, want in cases:
            self.assertEqual(s1_from_score(score), want, f"score={score}")

    def test_risk_out(self):
        self.assertFalse(risk_out_from(0.49))
        self.assertTrue(risk_out_from(0.5))


class TestAskJev(unittest.TestCase):
    def test_usage_styles(self):
        for usage, want in [(_ObjUsage(11), 11), ({"input_tokens": 22}, 22), (None, None)]:
            resp = _Resp(1.5, 0.2, usage)

            class C:
                def system_one(self, text, questions, model=None):
                    self.got = (questions, model)
                    return resp

            c = C()
            out = ask_jev(c, "본문")
            self.assertEqual(out, {"jev_sustain": 1.5, "jev_risk": 0.2, "input_tokens": want})
            self.assertIs(c.got[0], QUESTIONS)
            self.assertEqual(c.got[1], "jev-1.13.0")


class TestBuildInput(unittest.TestCase):
    def _full(self):
        return dict(
            telegram_posts=[{
                "post_ref": "c/1", "channel": "c",
                "posted_at_utc": "2026-09-27T06:00:00+00:00", "text": "급등  재료\n발생",
            }],
            youtube_items=[{"headline": "H", "analysis": "A", "discovery_reason": "D"}],
            reports=[{
                "report_date": "2026-09-28", "broker": "B증권", "title": "T",
                "opinion": "매수", "goal_price": 10000, "prev_target": 9000,
                "content_html": "<p>본문</p>",
            }],
            filings=[{"rcept_dt": "20260928", "report_nm": "사업보고서"}],
            news=[{"published_at": "2026-09-28T10:00:00+09:00", "title": "N"}],
        )

    def test_empty_sections(self):
        text = build_input("AAA", "에이", telegram_posts=[], youtube_items=[],
                           reports=[], filings=[], news=[])
        self.assertEqual(
            text,
            "[텔레그램]\n(없음)\n[유튜브]\n(없음)\n[증권사 리포트]\n(없음)"
            "\n[공시]\n(없음)\n[뉴스]\n(없음)",
        )

    def test_section_order_and_lines(self):
        text = build_input("AAA", "에이", **self._full())
        kinds = ["[텔레그램]", "[유튜브]", "[증권사 리포트]", "[공시]", "[뉴스]"]
        self.assertEqual(sorted(kinds, key=text.index), kinds)
        # 텔레그램: KST 시각 + 공백 뭉갬. 06:00Z = 15:00 KST.
        self.assertIn("- 09-27 15:00 급등 재료 발생", text)
        self.assertIn("- H: A", text)
        self.assertIn(
            "- 2026-09-28 B증권 T | 의견 매수 | 목표가 10000 (직전 9000) | 본문", text)
        self.assertIn("- 20260928 사업보고서", text)
        self.assertIn("- 2026-09-28 N", text)

    def test_telegram_order_and_cut(self):
        posts = [
            {"post_ref": "c/2", "channel": "c",
             "posted_at_utc": "2026-09-28T00:00:00+00:00", "text": "나중글"},
            {"post_ref": "c/1", "channel": "c",
             "posted_at_utc": "2026-09-27T00:00:00+00:00", "text": "먼저글" + "가" * 700},
        ]
        text = build_input("AAA", "에이", telegram_posts=posts, youtube_items=[],
                           reports=[], filings=[], news=[])
        # 오래된 순 + 본문 600자 자름.
        self.assertLess(text.index("먼저글"), text.index("나중글"))
        line = [ln for ln in text.splitlines() if "먼저글" in ln][0]
        self.assertEqual(len(line.split(" ", 3)[3]), 600)

    def test_youtube_discovery_fallback(self):
        items = [{"headline": None, "analysis": "", "discovery_reason": "발굴사유"}]
        text = build_input("AAA", "에이", telegram_posts=[], youtube_items=items,
                           reports=[], filings=[], news=[])
        self.assertIn("- : 발굴사유", text)

    def test_anonymize_name_and_ticker(self):
        posts = [{"post_ref": "c/1", "channel": "c",
                  "posted_at_utc": "2026-09-27T00:00:00+00:00",
                  "text": "삼성전자(005930) 급등"}]
        text = build_input("005930", "삼성전자", telegram_posts=posts, youtube_items=[],
                           reports=[], filings=[], news=[])
        self.assertNotIn("삼성전자", text)
        self.assertNotIn("005930", text)
        self.assertIn("종목A(종목A) 급등", text)

    def test_anonymize_longest_first(self):
        # 티커가 이름의 부분문자열 — 긴 키 먼저 치환돼야 "종목A우"가 안 남는다.
        posts = [{"post_ref": "c/1", "channel": "c",
                  "posted_at_utc": "2026-09-27T00:00:00+00:00",
                  "text": "ABC우 급등 ABC 동반"}]
        text = build_input("ABC", "ABC우", telegram_posts=posts, youtube_items=[],
                           reports=[], filings=[], news=[])
        self.assertIn("종목A 급등 종목A 동반", text)
        self.assertNotIn("ABC우", text)

    def test_html_strip(self):
        reports = [{
            "report_date": "2026-09-28", "broker": "B", "title": "T",
            "opinion": "매수", "goal_price": 1, "prev_target": None,
            "content_html": "<p>목표가 <b>인상</b>&nbsp;&amp; 유지</p>",
        }]
        text = build_input("AAA", "에이", telegram_posts=[], youtube_items=[],
                           reports=reports, filings=[], news=[])
        self.assertIn("목표가 인상 & 유지", text)
        self.assertNotIn("<p>", text)
        self.assertNotIn("&nbsp;", text)


class TestTokenCap(unittest.TestCase):
    def test_oldest_telegram_dropped_reports_kept(self):
        posts = [{
            "post_ref": f"c/{i}", "channel": "c",
            "posted_at_utc": f"2026-09-27T{i // 60:02d}:{i % 60:02d}:00+00:00",
            "text": f"P{i:02d} " + "가" * 700,
        } for i in range(30)]
        reports = [{
            "report_date": "2026-09-28", "broker": "B", "title": "REPORT_MARK",
            "opinion": "매수", "goal_price": 1, "prev_target": None,
            "content_html": "짧은 본문",
        }]
        text = build_input("AAA", "에이", telegram_posts=posts, youtube_items=[],
                           reports=reports, filings=[], news=[])
        self.assertLessEqual(approx_tokens(text), STATE_TOKEN_LIMIT)
        self.assertNotIn("P00 ", text)   # 가장 오래된 글부터 빠진다
        self.assertIn("P29 ", text)       # 최신 글은 남는다
        self.assertIn("REPORT_MARK", text)  # 리포트는 마지막까지 유지


class TestLoadTelegram(unittest.TestCase):
    def test_union_exclude_order(self):
        with TemporaryDirectory() as d:
            path = f"{d}/tg.sqlite3"
            con = _mkdb(path, [_TG_INSIGHTS_DDL, _TG_POSTS_DDL])
            con.executemany(
                "INSERT INTO telegram_stock_insights (date_kst, session, ticker, source_post_refs)"
                " VALUES (?,?,?,?)",
                [("2026-09-27", "morning", "AAA", '["c/1","c/2"]'),
                 ("2026-09-28", "close", "AAA", '["c/2","awake_realtimeCheck/5","c/3"]'),
                 ("2026-09-28", "close", "BBB", '["c/9"]')],
            )
            con.executemany(
                "INSERT INTO telegram_posts (channel, post_id, post_ref, posted_at_utc, text)"
                " VALUES (?,?,?,?,?)",
                [("c", 1, "c/1", "2026-09-27T01:00:00+00:00", "글1"),
                 ("c", 2, "c/2", "2026-09-27T00:00:00+00:00", "글2"),
                 ("awake_realtimeCheck", 5, "awake_realtimeCheck/5",
                  "2026-09-28T00:00:00+00:00", "봇글"),
                 ("c", 3, "c/3", "2026-09-28T01:00:00+00:00", "글3"),
                 ("c", 9, "c/9", "2026-09-28T02:00:00+00:00", "다른종목글")],
            )
            con.commit()
            con.close()
            got = load_telegram_posts(path, "AAA", ["2026-09-27", "2026-09-28"])
        # 합집합 + 봇 제외 + 시간 오름차순. BBB 글(c/9)은 안 들어온다.
        self.assertEqual([p["post_ref"] for p in got], ["c/2", "c/1", "c/3"])
        self.assertEqual(got[0]["text"], "글2")
        self.assertTrue(all(p["channel"] != "awake_realtimeCheck" for p in got))

    def test_no_refs(self):
        with TemporaryDirectory() as d:
            path = f"{d}/tg.sqlite3"
            con = _mkdb(path, [_TG_INSIGHTS_DDL, _TG_POSTS_DDL])
            con.execute(
                "INSERT INTO telegram_stock_insights (date_kst, session, ticker, source_post_refs)"
                " VALUES ('2026-09-28','close','AAA','[]')")
            con.commit()
            con.close()
            self.assertEqual(load_telegram_posts(path, "AAA", ["2026-09-28"]), [])
            self.assertEqual(load_telegram_posts(path, "AAA", []), [])


class TestLoadYoutube(unittest.TestCase):
    def test_headline_and_broken(self):
        with TemporaryDirectory() as d:
            path = f"{d}/yt.sqlite3"
            con = _mkdb(path, [_YT_INSIGHTS_DDL, _YT_SUM_DDL])
            con.executemany(
                "INSERT INTO youtube_stock_insights"
                " (date_kst, ticker, source_video_ids, discovery_reason, analysis)"
                " VALUES (?,?,?,?,?)",
                [("2026-09-27", "AAA", '["v1","v2"]', "발굴1", "분석1"),
                 ("2026-09-28", "AAA", '["v3"]', "발굴2", "분석2"),
                 ("2026-09-28", "AAA", '["v9"]', "발굴3", "분석3"),
                 ("2026-09-28", "BBB", '["v1"]', "발굴B", "분석B")],
            )
            con.executemany(
                "INSERT INTO youtube_video_summaries (channel_id, video_id, summary_json)"
                " VALUES (?,?,?)",
                [("ch", "v1", '{"headline": "H1"}'),
                 ("ch", "v2", '{"headline": "H2"}'),
                 ("ch", "v3", "깨진 json{{{")],
            )
            con.commit()
            con.close()
            got = load_youtube_items(path, "AAA", ["2026-09-27", "2026-09-28"])
        # 여러 영상이면 첫 영상 headline. 깨진 JSON·요약 없음이면 None.
        self.assertEqual(
            [(g["headline"], g["analysis"]) for g in got],
            [("H1", "분석1"), (None, "분석2"), (None, "분석3")],
        )
        self.assertEqual(load_youtube_items(path, "AAA", []), [])


class TestLoadReports(unittest.TestCase):
    def test_period_and_prev_target(self):
        with TemporaryDirectory() as d:
            path = f"{d}/report.sqlite3"
            storage.init_db(path)
            con = sqlite3.connect(path)
            rows = [
                ("k0", "r0", "AAA", "B증권", "2026-09-20", "T0", "매수", 10000),
                ("k1", "r1", "AAA", "B증권", "2026-09-25", "T1", "매수", 20000),
                ("k2", "r2", "AAA", "B증권", "2026-09-28", "T2", "매수", 30000),
                ("k3", "r3", "AAA", "B증권", "2026-09-29", "T3", "매수", 40000),
                ("k4", "r4", "BBB", "B증권", "2026-09-28", "TB", "매수", 50000),
            ]
            con.executemany(
                "INSERT INTO report_api_facts (pdf_key, research_id, stock_code, broker,"
                " report_date, title, opinion, goal_price, fetched_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                [r + ("2026-09-28T00:00:00",) for r in rows],
            )
            con.commit()
            con.close()
            got = load_reports(path, "AAA", "2026-09-25", "2026-09-28")
        # (start, end] — start 당일은 제외, end 당일은 포함. 다른 종목 제외.
        self.assertEqual([r["report_date"] for r in got], ["2026-09-28"])
        self.assertEqual(got[0]["prev_target"], 20000)  # 직전(09-25) 목표가
        self.assertEqual(got[0]["goal_price"], 30000)


class TestCollectFilings(unittest.TestCase):
    def test_per_date_call_and_failure(self):
        calls = []

        def fake_fetch(day):
            calls.append(day)
            if day == "2026-09-27":
                raise ConnectionError("dart down")
            return [
                {"stock_code": "AAA", "rcept_dt": day.replace("-", ""),
                 "report_nm": f"보고{day}"},
                {"stock_code": "", "rcept_dt": "x", "report_nm": "무코드"},
            ]

        by_ticker, failed = collect_filings(
            ["2026-09-27", "2026-09-28"], fake_fetch)
        self.assertEqual(calls, ["2026-09-27", "2026-09-28"])  # 날짜별 1회
        self.assertEqual(failed, ["2026-09-27"])
        self.assertEqual(
            by_ticker,
            {"AAA": [{"rcept_dt": "20260928", "report_nm": "보고2026-09-28"}]},
        )


class TestJudgeJev(unittest.TestCase):
    def _dbs(self, d):
        tg, yt, rep = f"{d}/tg.sqlite3", f"{d}/yt.sqlite3", f"{d}/rep.sqlite3"
        con = _mkdb(tg, [_TG_INSIGHTS_DDL, _TG_POSTS_DDL])
        tickers = {"111111": ("가짜전자", "사과"), "222222": ("진짜산업", "바나나"),
                   "333333": ("예외물산", "체리"), "444444": ("뉴스폭탄", "두리안")}
        for i, (ticker, (name, marker)) in enumerate(tickers.items()):
            con.execute(
                "INSERT INTO telegram_stock_insights (date_kst, session, ticker, source_post_refs)"
                " VALUES ('2026-09-28','close',?,?)",
                (ticker, f'["c/{i}"]'))
            con.execute(
                "INSERT INTO telegram_posts (channel, post_id, post_ref, posted_at_utc, text)"
                " VALUES ('c',?, ?, '2026-09-28T06:00:00+00:00', ?)",
                (i, f"c/{i}", f"{marker} {name} 급등"))
        con.commit()
        con.close()
        con = _mkdb(yt, [_YT_INSIGHTS_DDL, _YT_SUM_DDL])
        for ticker, (name, marker) in tickers.items():
            con.execute(
                "INSERT INTO youtube_stock_insights"
                " (date_kst, ticker, source_video_ids, discovery_reason, analysis)"
                " VALUES ('2026-09-28',?,?,?,?)",
                (ticker, f'["v{ticker}"]', "발굴", f"분석 {ticker}"))
            con.execute(
                "INSERT INTO youtube_video_summaries (channel_id, video_id, summary_json)"
                " VALUES ('ch',?,?)",
                (f"v{ticker}", f'{{"headline": "헤드{ticker}"}}'))
        con.commit()
        con.close()
        storage.init_db(rep)
        con = sqlite3.connect(rep)
        for ticker in tickers:
            con.execute(
                "INSERT INTO report_api_facts (pdf_key, research_id, stock_code, broker,"
                " report_date, title, opinion, goal_price, fetched_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (f"k{ticker}", f"r{ticker}", ticker, "B증권",
                 "2026-09-28", f"리포트{ticker}", "매수", 1000, "2026-09-28T00:00:00"))
        con.commit()
        con.close()
        return tg, yt, rep

    def test_full(self):
        with TemporaryDirectory() as d:
            tg, yt, rep = self._dbs(d)
            client = MarkerClient({
                "사과": (1.5, 0.9, 100),    # s1=2, 탈락
                "바나나": (0.5, 0.2, 200),   # s1=0, 통과
                "체리": RuntimeError("jev down"),
                "두리안": (1.0, 0.1, 50),    # s1=1
            })
            filing_calls = []

            def fake_filings(day):
                filing_calls.append(day)
                if day == "2026-09-20":
                    raise ConnectionError("dart down")
                if day == "2026-09-28":
                    return [{"stock_code": "111111", "rcept_dt": "20260928",
                             "report_nm": "유상증자결정"}]
                return []

            news_calls = []

            def fake_news(name, ticker, as_of, limit):
                news_calls.append((name, ticker, as_of, limit))
                if ticker == "444444":
                    raise TimeoutError("rss down")
                return [{"title": f"뉴스{ticker}",
                         "published_at": "2026-09-28T10:00:00+09:00"}]

            out = judge_jev(
                [{"ticker": t, "name": n} for t, (n, _) in
                 {"111111": ("가짜전자", 0), "222222": ("진짜산업", 0),
                  "333333": ("예외물산", 0), "444444": ("뉴스폭탄", 0)}.items()],
                today="2026-09-28", prev_trading_date="2026-09-25",
                report_start_exclusive="2026-09-25",
                client=client, tg_db=tg, yt_db=yt, report_db=rep,
                fetch_filings=fake_filings, fetch_news=fake_news,
            )
        results = out["results"]
        # 종목별 매핑
        self.assertEqual(results["111111"]["s1"], 2)
        self.assertTrue(results["111111"]["risk_out"])
        self.assertEqual(results["111111"]["jev_sustain"], 1.5)
        self.assertEqual(results["222222"]["s1"], 0)
        self.assertFalse(results["222222"]["risk_out"])
        # 한 종목 예외 → 그 종목만 errors["jev"], 나머지 정상
        bad = results["333333"]
        self.assertIsNone(bad["s1"])
        self.assertIsNone(bad["risk_out"])
        self.assertEqual(bad["errors"], {"jev": "RuntimeError"})
        self.assertIsNotNone(bad["jev_input_hash"])
        # 뉴스 실패 → errors["news"], s1은 정상 계산
        dw = results["444444"]
        self.assertEqual(dw["errors"], {"news": "TimeoutError"})
        self.assertEqual(dw["s1"], 1)
        self.assertNotIn("뉴스444444", dw["input_text"])
        # 토큰 합산 (예외 종목 제외)
        self.assertEqual(out["input_tokens_total"], 350)
        # 공시 실패일 전파 + 날짜별 1회 × 10일
        self.assertEqual(out["failed_filing_dates"], ["2026-09-20"])
        self.assertEqual(len(filing_calls), 10)
        self.assertEqual(filing_calls[-1], "2026-09-28")
        self.assertEqual(filing_calls[0], "2026-09-19")
        # 뉴스 as_of = today 19:00 KST, limit 전달
        for _, _, as_of, limit in news_calls:
            self.assertEqual(
                as_of, datetime(2026, 9, 28, 19, 0, tzinfo=ZoneInfo("Asia/Seoul")))
            self.assertEqual(limit, 8)
        # 익명화: client가 받은 text에 실제 종목명·코드 없음
        for call in client.calls:
            for leak in ["가짜전자", "진짜산업", "예외물산", "뉴스폭탄",
                         "111111", "222222", "333333", "444444"]:
                self.assertNotIn(leak, call["text"])
            self.assertIn("종목A", call["text"])
            self.assertIs(call["questions"], QUESTIONS)
            self.assertEqual(call["model"], "jev-1.13.0")
        # 해시 = 입력 묶음 sha1
        for ticker, row in results.items():
            self.assertEqual(
                row["jev_input_hash"],
                hashlib.sha1(row["input_text"].encode("utf-8")).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
