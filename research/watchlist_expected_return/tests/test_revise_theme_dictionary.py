from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

from research.watchlist_expected_return.revise_theme_dictionary import (
    _next_version,
    apply_revision,
    collect_candidates,
    collect_usage,
    make_revision_prompt,
    revise,
    validate_revision,
    write_migrations,
)


def base_dictionary() -> dict:
    return {
        "version": "2026-08-28",
        "note": "테스트 사전",
        "special": [
            {"name": "재료없음", "description": "재료 없음"},
            {"name": "사전에없음", "description": "사전 밖"},
        ],
        "theme_sector": [
            {"name": "메모리·반도체", "members": ["HBM"]},
            {"name": "방산·우주항공", "members": ["K2 전차"]},
        ],
        "theme_event": [{"name": "실적·가이던스", "members": ["2분기 실적"]}],
        "excluded_axes": [{"axis": "수급 주체", "reason": "테마 아님", "members": ["기관 순매수"]}],
    }


def make_db(path: Path, rows: list[tuple]) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.execute("""
            CREATE TABLE llm_catalyst_assessments (
                date TEXT NOT NULL, ticker TEXT NOT NULL,
                new_theme_candidate TEXT, theme_scores_json TEXT,
                PRIMARY KEY (date, ticker)
            )
        """)
        con.executemany(
            "INSERT INTO llm_catalyst_assessments"
            " (date,ticker,new_theme_candidate,theme_scores_json) VALUES (?,?,?,?)",
            rows,
        )
        con.commit()


class ReviseThemeDictionaryTest(unittest.TestCase):
    def test_candidate_gate_requires_repetition_and_multiple_tickers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "watchlist.sqlite3"
            make_db(db_path, [
                ("20260901", "000001", "양자컴퓨팅", None),
                ("20260902", "000002", "양자컴퓨팅", None),
                ("20260903", "000003", "양자컴퓨팅", None),
                # 한 종목에서만 3회 - 그 종목 고유명사일 가능성이 높다
                ("20260901", "000009", "청주 P&T7", None),
                ("20260902", "000009", "청주 P&T7", None),
                ("20260903", "000009", "청주 P&T7", None),
                # 두 종목이지만 2회뿐
                ("20260904", "000004", "심해채굴", None),
                ("20260905", "000005", "심해채굴", None),
            ])
            promoted, rejected = collect_candidates(db_path, min_count=3, min_tickers=2)
            self.assertEqual([item["name"] for item in promoted], ["양자컴퓨팅"])
            self.assertEqual(promoted[0]["ticker_count"], 3)
            self.assertEqual(
                sorted(item["name"] for item in rejected), ["심해채굴", "청주 P&T7"]
            )

    def test_usage_counts_only_scored_names_inside_window(self) -> None:
        payload = json.dumps({
            "sector": [{"name": "메모리·반도체", "score": 100},
                       {"name": "방산·우주항공", "score": 0}],
            "event": [{"name": "실적·가이던스", "score": 100}],
        }, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "watchlist.sqlite3"
            make_db(db_path, [
                ("20990101", "000001", None, payload),
                ("19900101", "000002", None, payload),
            ])
            usage = collect_usage(db_path, unused_days=36500)
            self.assertEqual(usage["sector"]["메모리·반도체"], 2)
            self.assertNotIn("방산·우주항공", usage["sector"])

            recent = collect_usage(db_path, unused_days=1)
            self.assertEqual(recent["sector"]["메모리·반도체"], 1)

    def test_revision_rejects_dropped_theme_without_migration(self) -> None:
        theme_dict = base_dictionary()
        revision = {
            "version": "2026-09-30",
            "theme_sector": [{"name": "메모리·반도체", "members": ["HBM"]}],
            "theme_event": [{"name": "실적·가이던스", "members": ["2분기 실적"]}],
            "migrations": [
                {"axis": "sector", "old_value": "메모리·반도체",
                 "new_value": "메모리·반도체", "action": "kept"},
            ],
            "reasoning": "방산을 지웠지만 매핑을 남기지 않았다",
        }
        with self.assertRaises(ValueError):
            validate_revision(revision, theme_dict)

        revision["migrations"].append({
            "axis": "sector", "old_value": "방산·우주항공",
            "new_value": "메모리·반도체", "action": "retired",
        })
        revision["migrations"].append({
            "axis": "event", "old_value": "실적·가이던스",
            "new_value": "실적·가이던스", "action": "kept",
        })
        validate_revision(revision, theme_dict)

    def test_revision_rejects_retire_without_target_and_special_values(self) -> None:
        theme_dict = base_dictionary()
        revision = {
            "version": "2026-09-30",
            "theme_sector": [item.copy() for item in theme_dict["theme_sector"]],
            "theme_event": [item.copy() for item in theme_dict["theme_event"]],
            "migrations": [
                {"axis": "sector", "old_value": "방산·우주항공",
                 "new_value": None, "action": "retired"},
            ],
            "reasoning": "폐기 대상이 갈 곳이 없다",
        }
        with self.assertRaises(ValueError):
            validate_revision(revision, theme_dict)

        revision["migrations"] = []
        revision["theme_sector"].append({"name": "재료없음", "members": []})
        with self.assertRaises(ValueError):
            validate_revision(revision, theme_dict)

    def test_revision_rejects_same_version(self) -> None:
        theme_dict = base_dictionary()
        with self.assertRaises(ValueError):
            validate_revision({
                "version": theme_dict["version"],
                "theme_sector": theme_dict["theme_sector"],
                "theme_event": theme_dict["theme_event"],
                "migrations": [], "reasoning": "",
            }, theme_dict)

    def test_apply_revision_preserves_special_and_excluded(self) -> None:
        theme_dict = base_dictionary()
        applied = apply_revision(theme_dict, {
            "version": "2026-09-30",
            "theme_sector": [{"name": "양자컴퓨팅", "members": ["큐비트"]}],
            "theme_event": [{"name": "실적·가이던스", "members": []}],
        })
        self.assertEqual(applied["version"], "2026-09-30")
        self.assertEqual(applied["special"], theme_dict["special"])
        self.assertEqual(applied["excluded_axes"], theme_dict["excluded_axes"])
        self.assertEqual(applied["theme_sector"][0]["name"], "양자컴퓨팅")

    def test_revise_is_noop_without_candidates_or_unused_themes(self) -> None:
        payload = json.dumps({
            "sector": [{"name": "메모리·반도체", "score": 60},
                       {"name": "방산·우주항공", "score": 40}],
            "event": [{"name": "실적·가이던스", "score": 100}],
        }, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "watchlist.sqlite3"
            dict_path = Path(tmpdir) / "theme_dictionary.json"
            dict_path.write_text(
                json.dumps(base_dictionary(), ensure_ascii=False), encoding="utf-8"
            )
            make_db(db_path, [("20990101", "000001", None, payload)])

            def fail_fn(prompt: str) -> str:
                raise AssertionError("LLM must not be called")

            result = revise(db_path, dict_path, unused_days=36500, revise_fn=fail_fn)
            self.assertFalse(result["changed"])

    def test_revise_calls_llm_and_records_migrations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "watchlist.sqlite3"
            dict_path = Path(tmpdir) / "theme_dictionary.json"
            dict_path.write_text(
                json.dumps(base_dictionary(), ensure_ascii=False), encoding="utf-8"
            )
            make_db(db_path, [
                ("20260901", "000001", "양자컴퓨팅", None),
                ("20260902", "000002", "양자컴퓨팅", None),
                ("20260903", "000003", "양자컴퓨팅", None),
            ])
            seen = {}

            def revise_fn(prompt: str) -> str:
                seen["prompt"] = prompt
                return json.dumps({
                    "version": "2026-09-30",
                    "theme_sector": [
                        {"name": "메모리·반도체", "members": ["HBM"]},
                        {"name": "방산·우주항공", "members": ["K2 전차"]},
                        {"name": "양자컴퓨팅", "members": ["양자컴퓨팅"]},
                    ],
                    "theme_event": [{"name": "실적·가이던스", "members": ["2분기 실적"]}],
                    "migrations": [
                        {"axis": "sector", "old_value": "메모리·반도체",
                         "new_value": "메모리·반도체", "action": "kept"},
                        {"axis": "sector", "old_value": "방산·우주항공",
                         "new_value": "방산·우주항공", "action": "kept"},
                        {"axis": "sector", "old_value": "양자컴퓨팅",
                         "new_value": "양자컴퓨팅", "action": "added"},
                        {"axis": "event", "old_value": "실적·가이던스",
                         "new_value": "실적·가이던스", "action": "kept"},
                    ],
                    "reasoning": "양자컴퓨팅 후보가 3종목에서 반복됐다",
                })

            result = revise(db_path, dict_path, revise_fn=revise_fn)
            self.assertTrue(result["changed"])
            self.assertIn("양자컴퓨팅", seen["prompt"])
            self.assertIn("recent_use_count", seen["prompt"])
            self.assertEqual(result["to_version"], "2026-09-30")
            self.assertEqual(
                [item["name"] for item in result["dictionary"]["theme_sector"]][-1], "양자컴퓨팅"
            )

            written = write_migrations(
                db_path, result["from_version"], result["to_version"], result["migrations"]
            )
            self.assertEqual(written, 4)
            with closing(sqlite3.connect(db_path)) as con:
                rows = con.execute(
                    "SELECT axis,old_value,new_value,action FROM theme_dict_migrations"
                    " ORDER BY axis, old_value"
                ).fetchall()
            self.assertIn(("sector", "양자컴퓨팅", "양자컴퓨팅", "added"), rows)
            self.assertEqual(len(rows), 4)

    def test_cold_db_without_theme_columns_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "watchlist.sqlite3"
            dict_path = Path(tmpdir) / "theme_dictionary.json"
            dict_path.write_text(
                json.dumps(base_dictionary(), ensure_ascii=False), encoding="utf-8"
            )
            with closing(sqlite3.connect(db_path)) as con:
                con.execute(
                    "CREATE TABLE llm_catalyst_assessments"
                    " (date TEXT, ticker TEXT, primary_category_raw TEXT)"
                )
                con.commit()

            def fail_fn(prompt: str) -> str:
                raise AssertionError("LLM must not be called on a cold DB")

            result = revise(db_path, dict_path, revise_fn=fail_fn)
            self.assertFalse(result["changed"])
            self.assertIn("not enough usage samples", result["reason"])
            self.assertEqual(result["promoted"], [])

    def test_small_usage_sample_does_not_trigger_retirement(self) -> None:
        """실측 사고: 판정 2건 상태에서 사전 전체가 폐기 후보로 잡혀 LLM을 불렀다."""
        payload = json.dumps({
            "sector": [{"name": "재료없음", "score": 100}],
            "event": [{"name": "재료없음", "score": 100}],
        }, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "watchlist.sqlite3"
            dict_path = Path(tmpdir) / "theme_dictionary.json"
            dict_path.write_text(
                json.dumps(base_dictionary(), ensure_ascii=False), encoding="utf-8"
            )
            make_db(db_path, [("20990101", "000001", None, payload)])

            def fail_fn(prompt: str) -> str:
                raise AssertionError("LLM must not be called on a tiny sample")

            result = revise(db_path, dict_path, unused_days=36500, revise_fn=fail_fn)
            self.assertFalse(result["changed"])
            self.assertIn("not enough usage samples", result["reason"])

    def test_next_version_avoids_collision_on_same_day(self) -> None:
        today = dt.datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
        self.assertEqual(_next_version("2020-01-01"), today)
        self.assertEqual(_next_version(today), f"{today}.2")
        self.assertEqual(_next_version(f"{today}.2"), f"{today}.3")

    def test_revise_uses_non_colliding_version(self) -> None:
        theme_dict = base_dictionary()
        today = dt.datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
        theme_dict["version"] = today
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "watchlist.sqlite3"
            dict_path = Path(tmpdir) / "theme_dictionary.json"
            dict_path.write_text(json.dumps(theme_dict, ensure_ascii=False), encoding="utf-8")
            make_db(db_path, [
                ("20260901", "000001", "양자컴퓨팅", None),
                ("20260902", "000002", "양자컴퓨팅", None),
                ("20260903", "000003", "양자컴퓨팅", None),
            ])
            seen = {}

            def revise_fn(prompt: str) -> str:
                seen["prompt"] = prompt
                return json.dumps({
                    "version": f"{today}.2",
                    "theme_sector": [
                        *[{"name": i["name"], "members": i["members"]}
                          for i in theme_dict["theme_sector"]],
                        {"name": "양자컴퓨팅", "members": ["양자컴퓨팅"]},
                    ],
                    "theme_event": [{"name": "실적·가이던스", "members": ["2분기 실적"]}],
                    "migrations": [
                        {"axis": "sector", "old_value": "메모리·반도체",
                         "new_value": "메모리·반도체", "action": "kept"},
                        {"axis": "sector", "old_value": "방산·우주항공",
                         "new_value": "방산·우주항공", "action": "kept"},
                        {"axis": "sector", "old_value": "양자컴퓨팅",
                         "new_value": "양자컴퓨팅", "action": "added"},
                        {"axis": "event", "old_value": "실적·가이던스",
                         "new_value": "실적·가이던스", "action": "kept"},
                    ],
                    "reasoning": "",
                })

            result = revise(db_path, dict_path, revise_fn=revise_fn)
            self.assertTrue(result["changed"])
            self.assertEqual(result["to_version"], f"{today}.2")
            self.assertIn(f"{today}.2", seen["prompt"])

    def test_prompt_carries_next_version_and_input(self) -> None:
        prompt = make_revision_prompt({"current_version": "2026-08-28"}, "2026-09-30")
        self.assertIn("2026-09-30", prompt)
        self.assertIn("current_version", prompt)
        self.assertIn("매핑은 필수다", prompt)


if __name__ == "__main__":
    unittest.main()
