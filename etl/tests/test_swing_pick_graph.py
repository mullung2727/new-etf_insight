"""스윙 그래프(graph)·러너(run_swing_pick) 테스트.

네트워크·Jev·GPT·broker 전부 가짜 주입. DB는 TemporaryDirectory에 만든다.
ohlcv·재무·인사이트 테이블은 실측 스키마 컬럼명 그대로 만든다.

실행 (etl 폴더): PYTHONPATH=. uv run python -m unittest tests.test_swing_pick_graph
"""
import json
import re
import sqlite3
import unittest
from datetime import date, datetime, timedelta
from tempfile import TemporaryDirectory
from unittest import mock

import duckdb

from scripts.report_metrics import storage as report_storage
from scripts.swing_pick.graph import (
    Deps,
    build_graph,
    expected_prev_trading_day,
    format_message,
    node_collect,
)
from scripts.swing_pick.store import connect_ro

TODAY = "2026-09-28"  # 월요일. 직전 거래일은 헬퍼로 계산 (달력 의존 제거)

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


class _JevAns:
    def __init__(self, score=None, noul=None):
        self.score = score
        self.noul = noul


class _JevResp:
    def __init__(self, score, noul, tokens):
        self.answers = {"sustain": _JevAns(score=score),
                        "risk": _JevAns(noul=noul)}
        self.usage = {"input_tokens": tokens}


class FakeJev:
    """본문 마커 → 판정. RISK 있으면 리스크 탈락, SCORE0이면 s1=0."""

    def __init__(self):
        self.calls = 0

    def system_one(self, text, questions, model=None):
        self.calls += 1
        score = 0.1 if "SCORE0" in text else 1.9
        noul = 0.9 if "RISK" in text else 0.1
        return _JevResp(score, noul, 10)


class _HttpResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class FakeHttp:
    """URL로 분기. calls에 (url, params) 기록."""

    def __init__(self, prices, investors, themes, fail_themes=()):
        self.prices = prices
        self.investors = investors
        self.themes = themes
        self.fail_themes = set(fail_themes)
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url.rsplit("/", 1)[-1] == "quotes":
            codes = (params or {}).get("codes", "").split(",")
            return _HttpResp([{"stk_cd": t, "cur_prc": self.prices[t]}
                              for t in codes if t in self.prices])
        ticker = url.split("/quotes/")[1].split("/")[0]
        if url.endswith("/investor-sum"):
            return _HttpResp(self.investors[ticker])
        if url.endswith("/status"):
            return _HttpResp({"order_warning": "", "audit_info": "정상"})
        if "/themes" in url:
            if ticker in self.fail_themes:
                raise RuntimeError("themes_down")
            return _HttpResp(self.themes.get(ticker, []))
        raise AssertionError(f"unexpected url {url}")

    def tickers(self, suffix):
        return {u.split("/quotes/")[1].split("/")[0]
                for u, _ in self.calls if suffix in u}


class FakeGen:
    """호출 기록 + 프롬프트 속 payload 순서대로 picks 응답."""

    def __init__(self):
        self.calls = []

    def __call__(self, prompt, output_schema_path=None, search=False):
        self.calls.append(prompt)
        tickers = []
        for m in re.finditer(r'"ticker": "(\d{6})"', prompt):
            if m.group(1) not in tickers:
                tickers.append(m.group(1))
        return json.dumps({
            "overview": "오늘 후보 전체 한 줄",
            "picks": [{"ticker": t, "thesis": f"{t} 테제",
                       "risks": [f"{t} 리스크1", f"{t} 리스크2"]}
                      for t in tickers],
        })


class FakeNotify:
    def __init__(self):
        self.calls = []

    def __call__(self, message, channel="batch"):
        self.calls.append((message, channel))
        return True


def _mkohlcv(path, end_compact, tickers_cfg, days=40):
    end = datetime.strptime(end_compact, "%Y%m%d").date()
    dates = [(end - timedelta(days=i)).strftime("%Y%m%d")
             for i in range(days - 1, -1, -1)]
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE ohlcv (ticker VARCHAR, date VARCHAR, "
                "close INTEGER, volume INTEGER, trading_value BIGINT, "
                "market_cap BIGINT)")
    rows = [(t, d, 100, 1000, cfg["tv"], cfg["mcap"])
            for t, cfg in tickers_cfg.items() for d in dates]
    con.executemany("INSERT INTO ohlcv VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.close()


def _mkfin(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE accounts (stock_code TEXT, bsns_year TEXT, "
                "reprt_code TEXT, fs_div TEXT, account_nm TEXT, amount REAL)")
    con.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _op_rows(ticker, cur, yoy):
    return [(ticker, "2026", "11013", "CFS", "영업이익", cur),
            (ticker, "2025", "11013", "CFS", "영업이익", yoy)]


def _mktg(path, insights, posts):
    con = sqlite3.connect(path)
    con.execute(_TG_INSIGHTS_DDL)
    con.execute(_TG_POSTS_DDL)
    for ticker, name, refs in insights:
        con.execute(
            "INSERT INTO telegram_stock_insights VALUES (?,?,?,?,?,?,?,?,?,?)",
            (TODAY, "close", ticker, name, "[]", json.dumps(refs),
             "", "", "", ""))
    for ref, text in posts:
        con.execute(
            "INSERT INTO telegram_posts VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("ch", 1, ref, f"{TODAY}T10:00:00+09:00", TODAY, text,
             "[]", None, "", ""))
    con.commit()
    con.close()


def _mkyt(path, tickers):
    con = sqlite3.connect(path)
    con.execute(_YT_INSIGHTS_DDL)
    con.execute(_YT_SUM_DDL)
    for ticker in tickers:
        con.execute(
            "INSERT INTO youtube_stock_insights VALUES (?,?,?,?,?,?,?,?,?)",
            (TODAY, ticker, f"테스트{ticker}", "[]", "[]", "", "유튜브 분석",
             "", ""))
    con.commit()
    con.close()


def _mkreport(path):
    report_storage.init_db(path)
    with report_storage.connect_rw(path) as con:
        report_storage.upsert_api_fact(con, {
            "pdf_key": "R1", "research_id": "1", "stock_code": "000001",
            "stock_name": "테스트000001", "broker": "가증권",
            "report_date": TODAY, "title": "리포트", "opinion": "BUY",
            "goal_price": 100, "price_at_write": 90,
            "content_html": "<p>본문</p>",
        })


def _mkhigh52(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE high52_screen (date TEXT, ticker TEXT, "
                "name TEXT, cand INTEGER)")
    con.executemany("INSERT INTO high52_screen VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()


def _full_deps(tmp, **over):
    """풀 픽스처. 000001(8점)·000002(7점)·000006(6점) 선정,
    000003 컷, 000004 리스크, 000005 저점 탈락."""
    exp = expected_prev_trading_day(TODAY)
    P = lambda n: f"{tmp}/{n}"
    _mkohlcv(P("ohlcv.duckdb"), exp, {
        "000001": {"tv": 6_000_000_000, "mcap": 1111},
        "000002": {"tv": 6_000_000_000, "mcap": 100},
        "000003": {"tv": 1_000, "mcap": 100},  # 거래대금 부족 → 컷
        "000004": {"tv": 6_000_000_000, "mcap": 100},
        "000005": {"tv": 6_000_000_000, "mcap": 100},
        "000006": {"tv": 6_000_000_000, "mcap": 100},
    })
    # 000001만 TTM 완성 → PER 1111/100 = 11.11. 000005는 적자.
    _mkfin(P("fin.sqlite3"), [
        *_op_rows("000001", 100, 50),
        ("000001", "2026", "11013", "CFS", "당기순이익(손실)", 10),
        ("000001", "2025", "11011", "CFS", "당기순이익(손실)", 100),
        ("000001", "2025", "11013", "CFS", "당기순이익(손실)", 10),
        ("000001", "2025", "11012", "CFS", "당기순이익(손실)", 10),
        ("000001", "2025", "11014", "CFS", "당기순이익(손실)", 10),
        *_op_rows("000002", 100, 50),
        *_op_rows("000006", 100, 50),
        ("000005", "2026", "11013", "CFS", "영업이익", -10),
    ])
    tg_text = {
        "000001": "2차전지 수주 재료 강함",
        "000002": "실적 서프라이즈 재료",
        "000003": "소재 국산화 재료",
        "000004": "RISK 유상증자 예정 물량 부담",
        "000005": "SCORE0 재료 없음 단순 급등",
        "000006": "신제품 출시 재료",
    }
    _mktg(P("tg.sqlite3"),
          [(t, f"테스트{t}", [f"ch:{t}"]) for t in tg_text]
          + [("ABC", "미국주", []), ("12345", "오타주", [])],  # 무효 티커
          [(f"ch:{t}", text) for t, text in tg_text.items()])
    _mkyt(P("yt.sqlite3"), ["000001", "000003"])
    _mkreport(P("report.sqlite3"))
    _mkhigh52(P("watch.sqlite3"), [
        (TODAY.replace("-", ""), "000001", "테스트000001", 1),
        (TODAY.replace("-", ""), "000005", "테스트000005", 0),  # cand=0 제외
        ("20200101", "000002", "테스트000002", 1),  # 다른 날 제외
    ])
    jev = FakeJev()
    http = FakeHttp(
        prices={"000001": 101, "000002": 101, "000005": 101, "000006": 90},
        investors={
            "000001": {"frgnr_invsr": 5, "orgn": 5},
            "000002": {"frgnr_invsr": 5, "orgn": -3},
            "000005": {"frgnr_invsr": -1, "orgn": -2},
            "000006": {"frgnr_invsr": 5, "orgn": 5},
        },
        themes={"000001": [
            {"name": "로봇", "period_return": -1.0, "rising": 2, "falling": 9},
            {"name": "이차전지", "period_return": 8.1, "rising": 5, "falling": 1},
            {"name": "바이오", "period_return": 3.3, "rising": 4, "falling": 4},
            {"name": "반도체", "period_return": 5.2, "rising": 8, "falling": 2},
        ]},
        fail_themes=("000006",),  # 테마 실패 → 빈 리스트 + errors 기록
    )
    gen = FakeGen()
    notify = FakeNotify()
    deps = Deps(
        tg_db=P("tg.sqlite3"), yt_db=P("yt.sqlite3"),
        report_db=P("report.sqlite3"), ohlcv_db=P("ohlcv.duckdb"),
        fin_db=P("fin.sqlite3"), high52_db=P("watch.sqlite3"),
        broker_url="http://fake", jev_client=jev,
        fetch_filings=lambda day: [], fetch_news=lambda *a: [],
        http_get=http, generate_fn=gen, notify_fn=notify,
        store_path=P("swing.sqlite3"),
    )
    for k, v in over.items():
        setattr(deps, k, v)
    fakes = {"jev": jev, "http": http, "gen": gen, "notify": notify}
    return deps, fakes


def _db_rows(store_path):
    with connect_ro(store_path) as con:
        cands = {r["ticker"]: dict(r) for r in con.execute(
            "SELECT * FROM swing_candidates WHERE date_kst = ?", (TODAY,))}
        run = con.execute(
            "SELECT * FROM swing_runs WHERE date_kst = ?", (TODAY,)).fetchone()
    return cands, dict(run)


class TestFullRun(unittest.TestCase):
    def test_all_stages(self):
        with TemporaryDirectory() as tmp:
            deps, f = _full_deps(tmp)
            final = build_graph(deps).invoke({"today": TODAY})

            # 수집: 4소스 병합·정렬, 무효 티커 제거
            cands = {c["ticker"]: c for c in final["candidates"]}
            self.assertEqual(
                cands["000001"]["sources"],
                ["high52", "report", "telegram", "youtube"])
            self.assertEqual(cands["000003"]["sources"],
                             ["telegram", "youtube"])
            self.assertNotIn("ABC", cands)
            self.assertNotIn("12345", cands)
            self.assertEqual(len(cands), 6)

            # 선정·점수·Jev 호출 (컷 1개 제외 5회)
            self.assertEqual(final["picks"], ["000001", "000002", "000006"])
            self.assertEqual(f["jev"].calls, 5)
            self.assertEqual(len(f["gen"].calls), 1)
            self.assertNotIn("krx_prev_stale", str(final["warnings"]))

            # 투자자 조회: 컷·리스크 종목 없음
            inv = f["http"].tickers("/investor-sum")
            self.assertNotIn("000003", inv)
            self.assertNotIn("000004", inv)
            # 오늘 가격 일괄 호출에도 컷·리스크 제외
            for url, params in f["http"].calls:
                if url.endswith("/quotes"):
                    self.assertNotIn("000003", params["codes"])
                    self.assertNotIn("000004", params["codes"])
            # 상태·테마는 선정 종목만 (저점 000005 조회 없음)
            self.assertEqual(f["http"].tickers("/status"),
                             {"000001", "000002", "000006"})
            self.assertEqual(
                {u.split("/quotes/")[1].split("/")[0]
                 for u, _ in f["http"].calls if "/themes" in u},
                {"000001", "000002", "000006"})

            # 저장 검증
            rows, run = _db_rows(deps.store_path)
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows["000003"]["excluded_reason"], "cut:low_value")
            self.assertEqual(rows["000004"]["excluded_reason"], "risk")
            self.assertIsNone(rows["000005"]["excluded_reason"])  # 저점인데 탈락 아님
            self.assertIsNone(rows["000005"]["rank"])
            self.assertEqual(
                (rows["000001"]["rank"], rows["000002"]["rank"],
                 rows["000006"]["rank"]), (1, 2, 3))
            self.assertEqual(
                (rows["000001"]["total"], rows["000002"]["total"],
                 rows["000006"]["total"], rows["000005"]["total"]),
                (8, 7, 6, 2))
            self.assertAlmostEqual(rows["000001"]["per"], 11.11)
            self.assertEqual(json.loads(rows["000001"]["themes"]),
                             [{"name": "이차전지", "ret": 8.1, "up": 5, "down": 1},
                              {"name": "반도체", "ret": 5.2, "up": 8, "down": 2},
                              {"name": "바이오", "ret": 3.3, "up": 4, "down": 4}])
            self.assertEqual(json.loads(rows["000006"]["themes"]), [])
            self.assertEqual(json.loads(rows["000006"]["errors"]),
                             {"themes": "RuntimeError"})
            self.assertEqual(
                (run["n_candidates"], run["n_risk_out"], run["n_errors"]),
                (6, 1, 1))
            self.assertEqual(run["jev_input_tokens"], 50)
            self.assertEqual(json.loads(run["summary"])["overview"],
                             "오늘 후보 전체 한 줄")
            self.assertEqual(run["notified"], 1)
            self.assertTrue(final["saved"])
            self.assertTrue(final["notified"])

            # 전송 채널
            self.assertEqual(len(f["notify"].calls), 1)
            self.assertEqual(f["notify"].calls[0][1], "batch")
            self.assertIn("[스윙 후보] 2026-09-28", f["notify"].calls[0][0])

    def test_collect_calls_no_llm(self):
        with TemporaryDirectory() as tmp:
            deps, f = _full_deps(tmp)
            out = node_collect({"today": TODAY}, deps)
            self.assertEqual(len(out["candidates"]), 6)
            self.assertEqual(f["jev"].calls, 0)
            self.assertEqual(len(f["gen"].calls), 0)

    def test_no_picks_no_summary(self):
        with TemporaryDirectory() as tmp:
            deps, f = _full_deps(tmp)
            # 빈 소스: 텔레그램 무효 티커만 남김
            con = sqlite3.connect(deps.tg_db)
            con.execute("DELETE FROM telegram_stock_insights "
                        "WHERE ticker != 'ABC'")
            con.commit()
            con.close()
            con = sqlite3.connect(deps.yt_db)
            con.execute("DELETE FROM youtube_stock_insights")
            con.commit()
            con.close()
            with report_storage.connect_rw(deps.report_db) as con:
                con.execute("DELETE FROM report_api_facts")
            con = sqlite3.connect(deps.high52_db)
            con.execute("DELETE FROM high52_screen")
            con.commit()
            con.close()
            final = build_graph(deps).invoke({"today": TODAY})
            self.assertEqual(final["candidates"], [])
            self.assertEqual(final["picks"], [])
            self.assertIsNone(final["summary"])
            self.assertEqual(len(f["gen"].calls), 0)
            self.assertEqual(f["jev"].calls, 0)
            self.assertIn("오늘 조건을 통과한 후보 없음", final["message"])


class TestFailureIsolation(unittest.TestCase):
    def test_summary_failure_continues(self):
        def boom(prompt, output_schema_path=None, search=False):
            raise RuntimeError("gpt_down")

        with TemporaryDirectory() as tmp:
            deps, f = _full_deps(tmp, generate_fn=boom)
            final = build_graph(deps).invoke({"today": TODAY})
            self.assertIsNone(final["summary"])
            self.assertIn("summary_failed:RuntimeError", final["warnings"])
            self.assertTrue(final["saved"])
            self.assertTrue(final["notified"])
            _, run = _db_rows(deps.store_path)
            self.assertIsNone(run["summary"])

    def test_dry_run(self):
        with TemporaryDirectory() as tmp:
            deps, f = _full_deps(tmp, dry_run=True)
            final = build_graph(deps).invoke({"today": TODAY})
            import os

            self.assertFalse(os.path.exists(deps.store_path))
            self.assertEqual(f["notify"].calls, [])
            self.assertFalse(final["saved"])
            self.assertFalse(final["notified"])
            self.assertIn("[스윙 후보]", final["message"])

    def test_high52_missing_table(self):
        with TemporaryDirectory() as tmp:
            deps, f = _full_deps(tmp)
            con = sqlite3.connect(deps.high52_db)
            con.execute("DROP TABLE high52_screen")
            con.commit()
            con.close()
            final = build_graph(deps).invoke({"today": TODAY})
            self.assertIn("source_failed:high52:OperationalError",
                          final["warnings"])
            # 나머지는 정상 — 000001이 high52 없이도 수집됨
            cands = {c["ticker"]: c for c in final["candidates"]}
            self.assertEqual(cands["000001"]["sources"],
                             ["report", "telegram", "youtube"])
            self.assertEqual(final["picks"], ["000001", "000002", "000006"])


class TestFormatMessage(unittest.TestCase):
    def _state(self):
        rows = {
            "005930": {"ticker": "005930", "name": "삼성전자", "total": 7,
                       "s1": 2, "s3": 2, "s4": 1, "s5": 2,
                       "sources": ["report", "telegram"], "per": 11.11,
                       "per_note": None,
                       "themes": [{"name": "반도체", "ret": 5.2, "up": 8,
                                   "down": 2}],
                       "errors": {}, "excluded_reason": None},
            "000660": {"ticker": "000660", "name": "하이닉스", "total": 6,
                       "s1": 2, "s3": 1, "s4": 1, "s5": 2,
                       "sources": ["youtube"], "per": None,
                       "per_note": "적자", "themes": [], "errors": {"s4": "x"},
                       "excluded_reason": None},
            "000001": {"ticker": "000001", "name": "컷주",
                       "excluded_reason": "cut:low_value", "errors": {}},
            "000002": {"ticker": "000002", "name": "리스크주",
                       "excluded_reason": "risk", "errors": {}},
        }
        return {
            "today": TODAY, "prev_date": "20260925",
            "candidates": [{}, {}, {}, {}], "rows": rows,
            "picks": ["005930", "000660"],
            "summary": {
                "overview": "전체 한 줄",
                "picks": [
                    {"ticker": "005930", "thesis": "수주 테제",
                     "risks": ["리스크a", "리스크b"]},
                    {"ticker": "000660", "thesis": "실적 테제",
                     "risks": ["리스크c"]},
                ],
            },
            "warnings": ["w1"], "jev_tokens": 1234,
        }

    def test_full_format(self):
        msg = format_message(self._state())
        self.assertIn("[스윙 후보] 2026-09-28 (시세 기준 20260925)", msg)
        self.assertIn("후보 4 · 1차컷 1 · 리스크탈락 1 · 오류 1 · Jev 토큰 1234",
                      msg)
        self.assertIn("1. 삼성전자(005930) 7/8점 "
                      "[재료2 실적2 수급1 차트2] 소스: 리포트·텔레그램", msg)
        self.assertIn("PER 11.1 · 테마: 반도체(+5.2%)", msg)
        self.assertIn("→ 수주 테제", msg)
        self.assertIn("⚠ 리스크a / 리스크b", msg)
        self.assertIn("PER 적자 · 테마: 없음", msg)
        self.assertIn("전체 한 줄", msg)
        self.assertIn("경고:\n- w1", msg)

    def test_no_picks(self):
        state = self._state()
        state["picks"] = []
        state["summary"] = None
        msg = format_message(state)
        self.assertIn("오늘 조건을 통과한 후보 없음", msg)
        self.assertNotIn("→", msg)


class TestPrevDate(unittest.TestCase):
    def test_expected_prev_properties(self):
        exp = expected_prev_trading_day(TODAY)
        self.assertLess(exp, TODAY.replace("-", ""))
        from scripts.check_krx_trading_day import is_krx_trading_day

        self.assertTrue(is_krx_trading_day(exp))
        # 사이 날은 전부 휴장일
        day = date.fromisoformat(exp) + timedelta(days=1)
        end = date.fromisoformat(TODAY)
        while day < end:
            self.assertFalse(is_krx_trading_day(day.strftime("%Y%m%d")))
            day += timedelta(days=1)

    def _collect_with_ohlcv_end(self, tmp, end_compact):
        _mkohlcv(f"{tmp}/o.duckdb", end_compact, {"000001": {"tv": 1, "mcap": 1}})
        deps = Deps(
            tg_db=f"{tmp}/nope1.sqlite3", yt_db=f"{tmp}/nope2.sqlite3",
            report_db=f"{tmp}/nope3.sqlite3", ohlcv_db=f"{tmp}/o.duckdb",
            fin_db=f"{tmp}/nope4.sqlite3", high52_db=f"{tmp}/nope5.sqlite3",
            jev_client=FakeJev(),
        )
        return node_collect({"today": TODAY}, deps)

    def test_stale_warning(self):
        exp = expected_prev_trading_day(TODAY)
        day = date.fromisoformat(exp[:4] + "-" + exp[4:6] + "-" + exp[6:])
        from scripts.check_krx_trading_day import is_krx_trading_day

        older = day - timedelta(days=1)
        while not is_krx_trading_day(older.strftime("%Y%m%d")):
            older -= timedelta(days=1)
        with TemporaryDirectory() as tmp:
            out = self._collect_with_ohlcv_end(
                tmp, older.strftime("%Y%m%d"))
            self.assertEqual(out["prev_date"], older.strftime("%Y%m%d"))
            self.assertIn(
                f"krx_prev_stale: {older.strftime('%Y%m%d')} "
                f"(expected {exp})", out["warnings"])

    def test_fresh_no_warning(self):
        with TemporaryDirectory() as tmp:
            out = self._collect_with_ohlcv_end(
                tmp, expected_prev_trading_day(TODAY))
            self.assertFalse([w for w in out["warnings"]
                              if w.startswith("krx_prev_stale")])


class TestRunnerHoliday(unittest.TestCase):
    def test_holiday_skips_graph(self):
        from scripts import run_swing_pick as runner

        with mock.patch.object(runner, "build_graph") as bg:
            rc = runner.main(["--date", "2026-09-26"])  # 토요일
        self.assertEqual(rc, 0)
        bg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
