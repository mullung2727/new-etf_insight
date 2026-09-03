from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.collect_trading_result_evidence import (
    allowed_refs,
    cause_rows,
    save_causes,
    as_of_kst,
    collect_evidence,
    enforce_grade,
    grade_evidence,
    index_filings_by_ticker,
    load_telegram,
    make_cause_prompt,
)


DATE = "20260828"
DATE_DASH = "2026-08-28"


def _watchlist_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.executescript(
            """
            CREATE TABLE close_bet_orders (
                date TEXT, ticker TEXT, cntr_price REAL, cntr_qty INTEGER,
                sell_status TEXT, sell_price REAL, sell_qty INTEGER, sold_at TEXT,
                exit_reason TEXT, pnl_pct REAL, sell_cmsn INTEGER, sell_tax INTEGER,
                sell_pl_won INTEGER
            );
            CREATE TABLE pullback_orders (
                watchlist_date TEXT, ticker TEXT, buy_price REAL, buy_qty INTEGER,
                sell_status TEXT, sell_price REAL, sell_qty INTEGER, sold_at TEXT,
                exit_reason TEXT, pnl_pct REAL, sell_cmsn INTEGER, sell_tax INTEGER,
                sell_pl_won INTEGER, bought_at TEXT
            );
            CREATE TABLE llm_scores (
                date TEXT, ticker TEXT, name TEXT, score INTEGER, category TEXT,
                reason_summary TEXT, final_opinion TEXT
            );
            CREATE TABLE llm_catalyst_assessments (
                date TEXT, ticker TEXT, primary_status TEXT, primary_duration TEXT,
                primary_alive_score INTEGER, max_alive_score INTEGER,
                assessment_json TEXT, theme_scores_json TEXT, theme_event_direction TEXT
            );
            """
        )
        con.execute(
            "INSERT INTO close_bet_orders VALUES "
            "('2026-08-27','005930',70000,10,'filled',71000,10,?,'take_profit',0.0142,100,110,9790)",
            (f"{DATE_DASH} 15:19:03",),
        )
        # llm_scores.date 는 실 DB와 같이 하이픈 없는 YYYYMMDD 다. 하이픈으로 두면
        # 실제로는 한 행도 안 잡히는 질의가 테스트만 통과한다(실측 사고).
        con.executemany(
            "INSERT INTO llm_scores VALUES (?,?,?,?,?,?,?)",
            [
                ("20260820", "005930", "삼성전자", 60, "메모리", "예전 판정", "예전 의견"),
                ("20260827", "005930", "삼성전자", 72, "메모리", "HBM 공급계약 기대", "매수 의견"),
                ("20260831", "005930", "삼성전자", 80, "메모리", "매도일 이후", "이후 의견"),
            ],
        )
        # 사전 재료 판단. 스코어링 파이프라인이 llm_scores 와 같은 DB 에 남긴다.
        con.executemany(
            "INSERT INTO llm_catalyst_assessments VALUES (?,?,?,?,?,?,?,?,?)",
            [
                ("20260820", "005930", "uncertain", "intraday", 1, 1,
                 json.dumps({"primary_catalyst": {"label": "예전 재료"}}, ensure_ascii=False),
                 None, None),
                ("20260827", "005930", "alive", "one_week_or_more", 3, 3,
                 json.dumps(
                     {
                         "primary_catalyst": {
                             "label": "HBM 공급계약 기대",
                             "description": "HBM 물량 확대 기대",
                             "category_raw": "공급계약",
                             "status": "alive",
                             "expected_duration": "one_week_or_more",
                             "alive_score": 3,
                             "reason": "복수 매체 보도",
                             "invalidation": "계약 규모가 기대에 못 미치면 소멸",
                             "evidence_refs": ["https://news/scoring-day"],
                         },
                         "secondary_catalysts": [
                             {"label": "보조 재료", "category_raw": "수급",
                              "status": "uncertain", "expected_duration": "intraday",
                              "alive_score": 2, "reason": "생략돼야 함",
                              "evidence_refs": ["https://news/secondary"]},
                         ],
                     },
                     ensure_ascii=False,
                 ),
                 json.dumps({"sector": [{"name": "반도체", "score": 100}],
                             "event": [{"name": "공급계약", "score": 100}]},
                            ensure_ascii=False),
                 "positive"),
                ("20260831", "005930", "alive", "intraday", 3, 3,
                 json.dumps({"primary_catalyst": {"label": "매도일 이후"}}, ensure_ascii=False),
                 None, None),
            ],
        )
        con.commit()


def _telegram_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.execute(
            """
            CREATE TABLE telegram_stock_insights (
                date_kst TEXT, session TEXT, ticker TEXT, mention_channels TEXT,
                source_post_refs TEXT, discovery_reason TEXT, analysis TEXT,
                created_at TEXT, updated_at TEXT
            )
            """
        )
        rows = [
            (DATE_DASH, "morning", "005930", '["ch_a"]', '["ref_morning"]',
             "장전 언급", '{"summary":"장전"}', f"{DATE_DASH}T08:40:00+09:00", None),
            (DATE_DASH, "close", "005930", '["ch_b"]', '["ref_close"]',
             "장마감 언급", '{"summary":"장마감"}', f"{DATE_DASH}T16:05:00+09:00", None),
            (DATE_DASH, "close", "005930", '["ch_c"]', '["ref_late"]',
             "차단선 이후", '{"summary":"늦음"}', f"{DATE_DASH}T17:30:00+09:00", None),
        ]
        con.executemany("INSERT INTO telegram_stock_insights VALUES (?,?,?,?,?,?,?,?,?)", rows)
        con.commit()


class EvidenceCollectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.watchlist = self.tmp / "watchlist.sqlite3"
        self.telegram = self.tmp / "telegram.sqlite3"
        _watchlist_db(self.watchlist)
        _telegram_db(self.telegram)
        self.addCleanup(self._tmp.cleanup)

    def _collect(self, *, news_fn=None, filings_fn=None) -> dict:
        return collect_evidence(
            DATE,
            watchlist_db=self.watchlist,
            telegram_db=self.telegram,
            news_fn=news_fn or (lambda name, ticker, as_of: [
                {"title": "삼성전자 HBM 공급 확대", "link": "https://news/1",
                 "published_at": f"{DATE_DASH}T11:00:00+09:00"}
            ]),
            filings_fn=filings_fn or (lambda date_kst: [
                {"stock_code": "005930", "corp_name": "삼성전자",
                 "report_nm": "단일판매·공급계약체결", "rcept_no": "20260828000111",
                 "flr_nm": "삼성전자", "rcept_dt": "20260828"},
                {"stock_code": "000660", "corp_name": "다른회사",
                 "report_nm": "무관", "rcept_no": "20260828000222",
                 "flr_nm": "다른회사", "rcept_dt": "20260828"},
            ]),
        )

    def test_collects_all_three_sources_with_buy_rationale(self) -> None:
        evidence = self._collect()

        self.assertEqual(evidence["as_of"], f"{DATE_DASH}T16:20:00+09:00")
        (record,) = evidence["tickers"]
        self.assertEqual(record["ticker"], "005930")
        self.assertEqual(record["name"], "삼성전자")
        self.assertEqual(len(record["filings"]), 1)
        self.assertEqual(record["filings"][0]["rcept_no"], "20260828000111")
        self.assertEqual(len(record["news"]), 1)
        self.assertEqual(record["buy_rationale"]["final_opinion"], "매수 의견")
        self.assertEqual(record["buy_rationale"]["scored_date"], "20260827")
        self.assertEqual(record["trades"][0]["exit_reason"], "take_profit")
        self.assertEqual(evidence["warnings"], [])

    def test_buy_rationale_carries_latest_catalyst_without_scoring_day_refs(self) -> None:
        (record,) = self._collect()["tickers"]
        catalyst = record["buy_rationale"]["catalyst"]

        # 매도일 이전 가장 최근 판정. 20260820(과거)도 20260831(이후)도 아니다.
        self.assertEqual(catalyst["assessed_date"], "20260827")
        self.assertEqual(catalyst["primary_duration"], "one_week_or_more")
        self.assertEqual(catalyst["primary_alive_score"], 3)
        self.assertEqual(catalyst["theme_event_direction"], "positive")
        self.assertEqual(catalyst["theme_scores"]["sector"][0]["name"], "반도체")

        primary = catalyst["primary_catalyst"]
        self.assertEqual(primary["label"], "HBM 공급계약 기대")
        self.assertEqual(primary["invalidation"], "계약 규모가 기대에 못 미치면 소멸")
        # 스코어링 당일 링크는 오늘의 근거가 아니다. 넣으면 LLM이 인용해 환각 판정에 걸린다.
        self.assertNotIn("evidence_refs", primary)
        self.assertNotIn("https://news/scoring-day", make_cause_prompt(DATE, record))

        (secondary,) = catalyst["secondary_catalysts"]
        self.assertEqual(secondary["label"], "보조 재료")
        self.assertNotIn("evidence_refs", secondary)
        self.assertNotIn("reason", secondary)  # 보조는 요약만 넣어 프롬프트를 줄인다

    def test_missing_catalyst_table_does_not_break_collection(self) -> None:
        with closing(sqlite3.connect(self.watchlist)) as con:
            con.execute("DROP TABLE llm_catalyst_assessments")
            con.commit()

        (record,) = self._collect()["tickers"]

        self.assertNotIn("catalyst", record["buy_rationale"])
        self.assertEqual(record["buy_rationale"]["final_opinion"], "매수 의견")

    def test_telegram_includes_close_session_but_not_after_cutoff(self) -> None:
        (record,) = self._collect()["tickers"]

        sessions = [item["session"] for item in record["telegram"]]
        self.assertEqual(sessions, ["morning", "close"])
        refs = [ref for item in record["telegram"] for ref in item["post_refs"]]
        self.assertIn("ref_close", refs)
        self.assertNotIn("ref_late", refs)

    def test_source_failure_warns_and_keeps_other_sources(self) -> None:
        def boom(date_kst):
            raise RuntimeError("DART down")

        evidence = self._collect(filings_fn=boom)
        (record,) = evidence["tickers"]

        self.assertEqual(record["filings"], [])
        self.assertEqual(len(record["news"]), 1)
        self.assertEqual(len(record["telegram"]), 2)
        self.assertIn("filings_fetch_failed:RuntimeError", evidence["warnings"])

    def test_filings_of_other_tickers_are_excluded(self) -> None:
        indexed = index_filings_by_ticker(
            [{"stock_code": "000660", "rcept_no": "1", "report_nm": "x"}], {"005930"}
        )
        self.assertEqual(indexed, {"005930": []})

    def test_prompt_renders_without_stray_placeholders(self) -> None:
        (record,) = self._collect()["tickers"]
        prompt = make_cause_prompt(DATE, record)

        self.assertIn(DATE_DASH, prompt)
        self.assertIn("005930", prompt)
        self.assertNotIn("{ticker_json}", prompt)


class GradeEnforcementTest(unittest.TestCase):
    def _record(self, *, filings=(), news=(), telegram=()) -> dict:
        return {
            "ticker": "005930",
            "filings": list(filings),
            "news": list(news),
            "telegram": list(telegram),
        }

    def _judgement(self, grade: str, refs: list[str]) -> dict:
        return {
            "grade": grade,
            "cause": "공급계약 체결",
            "evidence_refs": refs,
            "buy_rationale_match": "same_sustained",
            "reasoning": "근거",
        }

    def test_allowed_refs_covers_all_three_sources(self) -> None:
        record = self._record(
            filings=[{"rcept_no": "R1", "link": "https://dart/R1"}],
            news=[{"link": "https://news/1"}],
            telegram=[{"post_refs": ["ref_close"]}],
        )
        self.assertEqual(
            allowed_refs(record), {"R1", "https://dart/R1", "https://news/1", "ref_close"}
        )

    def test_grade_a_with_collected_ref_is_kept(self) -> None:
        record = self._record(filings=[{"rcept_no": "R1", "link": "https://dart/R1"}])
        result, warnings = enforce_grade(self._judgement("A", ["R1"]), record)

        self.assertEqual(result["grade"], "A")
        self.assertEqual(warnings, [])

    def test_grade_b_with_collected_ref_is_kept(self) -> None:
        record = self._record(news=[{"link": "https://news/1"}])
        result, warnings = enforce_grade(self._judgement("B", ["https://news/1"]), record)

        self.assertEqual(result["grade"], "B")
        self.assertEqual(warnings, [])

    def test_grade_d_without_ref_is_kept(self) -> None:
        # D 는 정황 등급이라 링크 없이도 정상이다. 강등 대상은 A·B 뿐이다.
        record = self._record(news=[{"link": "https://news/1"}])
        result, warnings = enforce_grade(self._judgement("D", []), record)

        self.assertEqual(result["grade"], "D")
        self.assertEqual(warnings, [])

    def test_grade_a_without_any_ref_falls_to_d_not_e(self) -> None:
        # 소스는 있었으므로 '근거 없음'(E)이 아니라 '정황'(D)이다.
        record = self._record(news=[{"link": "https://news/1"}])
        result, warnings = enforce_grade(self._judgement("A", []), record)

        self.assertEqual(result["grade"], "D")
        self.assertIn("downgraded_without_ref:005930:A", warnings)

    def test_grade_b_without_any_ref_is_downgraded_too(self) -> None:
        record = self._record(news=[{"link": "https://news/1"}])
        result, warnings = enforce_grade(self._judgement("B", []), record)

        self.assertEqual(result["grade"], "D")
        self.assertIn("downgraded_without_ref:005930:B", warnings)

    def test_grade_a_with_hallucinated_ref_is_downgraded(self) -> None:
        record = self._record(news=[{"link": "https://news/1"}])
        result, warnings = enforce_grade(self._judgement("A", ["https://news/999"]), record)

        self.assertEqual(result["grade"], "D")
        self.assertEqual(result["evidence_refs"], [])
        self.assertIn("hallucinated_ref:005930:https://news/999", warnings)
        self.assertIn("downgraded_hallucinated_ref:005930:A", warnings)

    def test_no_source_forces_e_even_when_llm_claims_a(self) -> None:
        result, warnings = enforce_grade(self._judgement("A", ["R1"]), self._record())

        self.assertEqual(result["grade"], "E")
        self.assertEqual(result["buy_rationale_match"], "unknown")
        self.assertIn("downgraded_no_source:005930:A", warnings)

    def test_no_source_e_is_accepted_without_warning(self) -> None:
        result, warnings = enforce_grade(self._judgement("E", []), self._record())

        self.assertEqual(result["grade"], "E")
        self.assertEqual(warnings, [])


class GradeEvidenceTest(unittest.TestCase):
    def _evidence(self, records: list[dict]) -> dict:
        return {"date": DATE_DASH, "as_of": f"{DATE_DASH}T16:20:00+09:00",
                "tickers": records, "warnings": []}

    def _record(self, ticker: str, *, sourced: bool = True) -> dict:
        return {
            "ticker": ticker,
            "filings": [{"rcept_no": "R1", "link": "https://dart/R1"}] if sourced else [],
            "news": [],
            "telegram": [],
        }

    def _judgement(self, grade: str) -> str:
        return json.dumps({
            "grade": grade, "cause": f"{grade} 원인", "evidence_refs": ["R1"],
            "buy_rationale_match": "same_sustained", "reasoning": "설명",
        })

    def test_grade_counts_and_per_ticker_isolation(self) -> None:
        records = [self._record("005930"), self._record("000660", sourced=False)]
        calls = []

        def generate(prompt: str) -> str:
            calls.append(prompt)
            if "000660" in prompt:
                raise RuntimeError("codex died")
            return self._judgement("A")

        result = grade_evidence(self._evidence(records), generate=generate, repeat=1)

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["tickers"][0]["judgement"]["grade"], "A")
        self.assertIsNone(result["tickers"][1]["judgement"])
        self.assertIn("grade_failed:000660:RuntimeError", result["warnings"])
        self.assertEqual(
            result["grade_counts"], {"A": 1, "B": 0, "C": 0, "D": 0, "E": 0}
        )

    def test_repeats_each_ticker_and_takes_median_grade(self) -> None:
        # 셋 중 둘이 같으면 그것이 답이다. 튀는 한 번에 끌려가지 않는다.
        grades = iter(["A", "C", "C"])

        def generate(prompt: str) -> str:
            return self._judgement(next(grades))

        result = grade_evidence(
            self._evidence([self._record("005930")]), generate=generate, repeat=3
        )
        record = result["tickers"][0]

        self.assertEqual(record["judgement"]["grade"], "C")
        self.assertEqual(record["judgement"]["cause"], "C 원인")
        self.assertEqual(sorted(record["grade_runs"]), ["A", "C", "C"])
        self.assertEqual(record["grade_spread"], 2)
        self.assertEqual(result["repeat"], 3)

    def test_two_step_spread_is_warned_but_one_step_is_not(self) -> None:
        for runs, expect_warning in ((["B", "C", "C"], False), (["B", "C", "D"], True)):
            with self.subTest(runs=runs):
                grades = iter(runs)
                result = grade_evidence(
                    self._evidence([self._record("005930")]),
                    generate=lambda prompt: self._judgement(next(grades)),
                    repeat=3,
                )
                unstable = [w for w in result["warnings"] if w.startswith("grade_unstable")]
                self.assertEqual(bool(unstable), expect_warning)

    def test_partial_failure_still_grades_from_surviving_runs(self) -> None:
        attempts = iter([RuntimeError("codex died"), "B", "B"])

        def generate(prompt: str) -> str:
            item = next(attempts)
            if isinstance(item, Exception):
                raise item
            return self._judgement(item)

        result = grade_evidence(
            self._evidence([self._record("005930")]), generate=generate, repeat=3
        )
        record = result["tickers"][0]

        self.assertEqual(record["judgement"]["grade"], "B")
        self.assertEqual(record["grade_runs"], ["B", "B"])
        self.assertIn("grade_partial:005930:1/3", result["warnings"])

    def test_repeated_downgrade_warning_is_recorded_once(self) -> None:
        """회차마다 같은 강등 경고가 나온다 — 보고문에 같은 줄이 세 번 찍히면 안 된다."""
        records = [self._record("005930", sourced=False)]

        def generate(prompt: str) -> str:
            return self._judgement("A")

        result = grade_evidence(self._evidence(records), generate=generate, repeat=3)

        downgrades = [w for w in result["warnings"] if "005930" in w and "A" in w]
        self.assertEqual(len(downgrades), len(set(downgrades)))
        self.assertEqual(len(downgrades), 1)

    def test_even_repeat_picks_the_weaker_side(self) -> None:
        # 가운데가 둘이면 근거가 약한 쪽(뒤 글자)을 택한다. 과대평가를 막는다.
        grades = iter(["B", "C"])
        result = grade_evidence(
            self._evidence([self._record("005930")]),
            generate=lambda prompt: self._judgement(next(grades)),
            repeat=2,
        )

        self.assertEqual(result["tickers"][0]["judgement"]["grade"], "C")


class AsOfTest(unittest.TestCase):
    def test_cutoff_is_1620_kst(self) -> None:
        self.assertEqual(as_of_kst("20260828").isoformat(), f"{DATE_DASH}T16:20:00+09:00")
        self.assertEqual(as_of_kst(DATE_DASH), as_of_kst("20260828"))


class TelegramLoadTest(unittest.TestCase):
    def test_empty_ticker_set_skips_query(self) -> None:
        self.assertEqual(load_telegram(Path("missing.sqlite3"), set(), DATE, as_of_kst(DATE)), {})


class SaveCausesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.watchlist = self.tmp / "watchlist.sqlite3"
        _watchlist_db(self.watchlist)
        self.addCleanup(self._tmp.cleanup)

    def _evidence(self, *, judgement: dict | None, trades: list[dict] | None = None) -> dict:
        return {
            "date": DATE_DASH,
            "as_of": f"{DATE_DASH}T16:20:00+09:00",
            "warnings": [],
            "tickers": [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "trades": trades if trades is not None else [
                        {"strategy": "종가베팅", "pnl_pct": 1.42,
                         "exit_reason": "take_profit", "bought_date": "20260827"},
                    ],
                    "buy_rationale": {
                        "catalyst": {
                            "assessed_date": "20260827",
                            "primary_status": "alive",
                            "primary_duration": "one_week_or_more",
                            "primary_catalyst": {"category_raw": "공급계약"},
                            "theme_scores": {"sector": [{"name": "반도체", "score": 100}]},
                            "theme_event_direction": "positive",
                        }
                    },
                    "filings": [{"rcept_no": "1"}],
                    "news": [{"link": "https://news/1"}],
                    "telegram": [],
                    "judgement": judgement,
                    "grade_runs": ["B", "C", "B"],
                    "grade_spread": 1,
                }
            ],
        }

    def _saved(self) -> list[sqlite3.Row]:
        with closing(sqlite3.connect(self.watchlist)) as con:
            con.row_factory = sqlite3.Row
            return con.execute("SELECT * FROM trading_result_causes").fetchall()

    def test_saves_judgement_with_catalyst_and_holding_period(self) -> None:
        judgement = {
            "grade": "B", "cause": "공급계약 기대 지속",
            "evidence_refs": ["https://news/1"],
            "buy_rationale_match": "same_sustained", "reasoning": "단일 출처",
        }

        self.assertEqual(save_causes(self.watchlist, self._evidence(judgement=judgement)), 1)
        (row,) = self._saved()

        self.assertEqual(row["date"], DATE)
        self.assertEqual(row["strategy"], "종가베팅")
        self.assertEqual(row["held_days"], 1)
        self.assertEqual(row["grade"], "B")
        self.assertEqual(row["buy_rationale_match"], "same_sustained")
        self.assertEqual(json.loads(row["evidence_refs_json"]), ["https://news/1"])
        self.assertEqual(row["catalyst_date"], "20260827")
        self.assertEqual(row["catalyst_expected_duration"], "one_week_or_more")
        self.assertEqual(row["catalyst_category_raw"], "공급계약")
        self.assertEqual(row["theme_event_direction"], "positive")
        self.assertEqual(json.loads(row["theme_scores_json"])["sector"][0]["name"], "반도체")
        self.assertEqual((row["filing_count"], row["news_count"], row["telegram_count"]), (1, 1, 0))
        self.assertEqual(json.loads(row["grade_runs_json"]), ["B", "C", "B"])
        self.assertEqual(row["grade_spread"], 1)

    def test_missing_column_on_an_older_table_is_migrated(self) -> None:
        """CREATE TABLE IF NOT EXISTS 는 기존 테이블을 안 고친다 — ALTER 로 채워야 한다."""
        with closing(sqlite3.connect(self.watchlist)) as con:
            con.execute(
                "CREATE TABLE trading_result_causes ("
                " date TEXT NOT NULL, ticker TEXT NOT NULL, strategy TEXT NOT NULL,"
                " grade TEXT NOT NULL, cause TEXT NOT NULL,"
                " buy_rationale_match TEXT NOT NULL, evidence_refs_json TEXT NOT NULL,"
                " grade_runs_json TEXT NOT NULL, filing_count INTEGER NOT NULL,"
                " news_count INTEGER NOT NULL, telegram_count INTEGER NOT NULL,"
                " as_of TEXT NOT NULL, generated_at TEXT NOT NULL,"
                " PRIMARY KEY (date, ticker, strategy))"
            )
            con.commit()
        judgement = {"grade": "B", "cause": "공급계약", "evidence_refs": ["https://news/1"],
                     "buy_rationale_match": "same_sustained", "reasoning": "단일 출처"}

        self.assertEqual(save_causes(self.watchlist, self._evidence(judgement=judgement)), 1)

        (row,) = self._saved()
        self.assertEqual(row["catalyst_date"], "20260827")
        self.assertEqual(row["held_days"], 1)

    def test_rerun_overwrites_instead_of_duplicating(self) -> None:
        first = {"grade": "C", "cause": "원인 불명", "evidence_refs": [],
                 "buy_rationale_match": "unknown", "reasoning": "근거 없음"}
        second = {"grade": "B", "cause": "재판정", "evidence_refs": ["https://news/1"],
                  "buy_rationale_match": "same_sustained", "reasoning": "다시 봄"}

        save_causes(self.watchlist, self._evidence(judgement=first))
        save_causes(self.watchlist, self._evidence(judgement=second))

        (row,) = self._saved()
        self.assertEqual(row["grade"], "B")
        self.assertEqual(row["cause"], "재판정")

    def test_same_ticker_in_both_strategies_keeps_each_return(self) -> None:
        judgement = {"grade": "B", "cause": "테마 강세", "evidence_refs": ["https://news/1"],
                     "buy_rationale_match": "different", "reasoning": "단일 출처"}
        trades = [
            {"strategy": "종가베팅", "pnl_pct": 1.42, "exit_reason": "tp", "bought_date": "20260827"},
            {"strategy": "눌림목", "pnl_pct": -0.5, "exit_reason": "sl", "bought_date": "20260819"},
        ]

        self.assertEqual(
            save_causes(self.watchlist, self._evidence(judgement=judgement, trades=trades)), 2
        )

        rows = {row["strategy"]: row for row in self._saved()}
        self.assertEqual(rows["종가베팅"]["held_days"], 1)
        self.assertEqual(rows["눌림목"]["held_days"], 9)
        self.assertAlmostEqual(rows["눌림목"]["pnl_pct"], -0.5)

    def test_unjudged_ticker_is_not_saved(self) -> None:
        # LLM 판정이 실패한 종목까지 남기면 등급 통계가 오염된다.
        self.assertEqual(save_causes(self.watchlist, self._evidence(judgement=None)), 0)
        self.assertEqual(cause_rows(self._evidence(judgement=None)), [])


if __name__ == "__main__":
    unittest.main()
