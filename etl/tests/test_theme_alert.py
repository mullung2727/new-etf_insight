"""run_theme_alert.py 1단계 단위 테스트 (DB 스키마·판정·문구, 순수 로직만).

Usage (from etl/):
    PYTHONPATH=. uv run python -m unittest tests.test_theme_alert -v
"""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from scripts.run_theme_alert import (
    ENV_PATH,
    classify,
    ensure_schema,
    format_message,
    is_processed,
    last_stage,
    load_config,
    resolve_theme,
    save_run,
    should_send_progress,
)

_BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _ref(label, posted, collected):
    return {
        "posted_at": posted,
        "collected_at": collected,
        "url": f"https://example.com/{label}",
        "label": label,
    }


class _DbCase(unittest.TestCase):
    def setUp(self):
        fd, self._path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(self._path)
        self.addCleanup(lambda: os.path.exists(self._path) and os.unlink(self._path))
        self.con = sqlite3.connect(self._path)
        self.addCleanup(self.con.close)
        ensure_schema(self.con)

    def _mention(self, theme_id, posted, source="telegram", ref="r1", collected=None):
        save_run(
            self.con,
            [
                {
                    "theme_id": theme_id,
                    "source": source,
                    "ref": ref,
                    "posted_at": posted,
                    "collected_at": collected or posted,
                }
            ],
            [],
            [],
        )

    def _table_count(self, table):
        return self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestClassifyBoundary(_DbCase):
    """1. 공백 경계: 29일/30일/31일 → progress_candidate/new/new."""

    def test_gap_boundary(self):
        tid = resolve_theme(self.con, "알래스카 LNG")
        self._mention(tid, _iso(_BASE))
        kind29, gap29 = classify(self.con, tid, _iso(_BASE + timedelta(days=29)), 30)
        kind30, gap30 = classify(self.con, tid, _iso(_BASE + timedelta(days=30)), 30)
        kind31, gap31 = classify(self.con, tid, _iso(_BASE + timedelta(days=31)), 30)
        self.assertEqual((kind29, gap29), ("progress_candidate", 29.0))
        self.assertEqual((kind30, gap30), ("new", 30.0))
        self.assertEqual((kind31, gap31), ("new", 31.0))


class TestDecision16(_DbCase):
    """2. 결정 16 예시: 9/1 → 9/20 저장 → 10/15 progress(25) → 10/21 new."""

    def test_repeat_mention_refreshes_last(self):
        tid = resolve_theme(self.con, "알래스카 LNG")
        self._mention(tid, "2026-09-01T00:00:00+00:00", ref="sep1")
        self._mention(tid, "2026-09-20T00:00:00+00:00", ref="sep20")
        kind_mid, gap_mid = classify(self.con, tid, "2026-10-15T00:00:00+00:00", 30)
        kind_late, gap_late = classify(self.con, tid, "2026-10-21T00:00:00+00:00", 30)
        self.assertEqual((kind_mid, gap_mid), ("progress_candidate", 25.0))
        self.assertEqual((kind_late, gap_late), ("new", 31.0))


class TestClassifyNoHistory(_DbCase):
    """3. 언급 이력 없는 테마 → ("new", None)."""

    def test_no_history_is_new(self):
        tid = resolve_theme(self.con, "처음 보는 테마")
        self.assertEqual(classify(self.con, tid, _iso(_BASE), 30), ("new", None))


class TestLoadConfig(_DbCase):
    """4. 임시 설정 quiet_days=10 → 11일 공백이 new. 기본 설정 파일 확인."""

    def test_custom_quiet_days(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"quiet_days": 10, "max_past_refs": 5}, f)
            cfg = load_config(path)
            self.assertEqual(cfg, {"quiet_days": 10, "max_past_refs": 5})
            tid = resolve_theme(self.con, "알래스카 LNG")
            self._mention(tid, _iso(_BASE))
            kind, gap = classify(
                self.con, tid, _iso(_BASE + timedelta(days=11)), cfg["quiet_days"]
            )
            self.assertEqual((kind, gap), ("new", 11.0))
        finally:
            os.unlink(path)

    def test_default_config_values(self):
        self.assertEqual(load_config(), {"quiet_days": 30, "max_past_refs": 10})


class TestResolveTheme(_DbCase):
    """5. 동의어 묶기 (결정 10)."""

    def test_alias_wins_over_llm_match(self):
        tid = resolve_theme(self.con, "알래스카 LNG")  # alias "알래스카lng" 생성
        other = resolve_theme(self.con, "다른테마")
        self.assertNotEqual(tid, other)
        self.assertEqual(
            resolve_theme(self.con, "알래스카 LNG", llm_match="다른테마"), tid
        )

    def test_llm_match_adds_alias(self):
        tid = resolve_theme(self.con, "알래스카 LNG")
        got = resolve_theme(self.con, "Alaska LNG", llm_match="알래스카 LNG")
        self.assertEqual(got, tid)
        row = self.con.execute(
            "SELECT theme_id, source FROM theme_aliases WHERE alias_norm = ?",
            ("alaskalng",),
        ).fetchone()
        self.assertEqual(row, (tid, "llm"))

    def test_new_name_creates_theme(self):
        tid = resolve_theme(self.con, "전고체 배터리")
        row = self.con.execute(
            "SELECT name, registered FROM themes WHERE theme_id = ?", (tid,)
        ).fetchone()
        self.assertEqual(row, ("전고체 배터리", 0))
        alias = self.con.execute(
            "SELECT theme_id FROM theme_aliases WHERE alias_norm = ?", ("전고체배터리",)
        ).fetchone()
        self.assertEqual(alias, (tid,))

    def test_sql_inserted_name_only_matches_normalized(self):
        cur = self.con.execute(
            "INSERT INTO themes (name, registered, created_at) VALUES (?, 1, ?)",
            ("알래스카 LNG", _iso(_BASE)),
        )
        theme_id = cur.lastrowid
        self.con.commit()
        got = resolve_theme(self.con, "알래스카LNG")
        self.assertEqual(got, theme_id)
        self.assertEqual(self._table_count("themes"), 1)
        self.assertEqual(self._table_count("theme_aliases"), 1)
        row = self.con.execute(
            "SELECT theme_id, source FROM theme_aliases WHERE alias_norm = ?",
            ("알래스카lng",),
        ).fetchone()
        self.assertEqual(row, (theme_id, "llm"))


class TestProgress(_DbCase):
    """6. 진전: baseline '협의' 기준 단계 비교."""

    def test_stage_change_only(self):
        tid = resolve_theme(self.con, "알래스카 LNG")
        save_run(
            self.con,
            [],
            [
                {
                    "theme_id": tid,
                    "kind": "baseline",
                    "stage": "협의",
                    "evidence_level": "증권사 전망",
                    "refs_json": "[]",
                    "message": "m",
                    "sent_at": None,
                }
            ],
            [],
        )
        self.assertEqual(last_stage(self.con, tid), "협의")
        self.assertFalse(should_send_progress("협의", "협의"))
        self.assertTrue(should_send_progress("협의", "계약·발주"))
        self.assertTrue(should_send_progress(None, "협의"))


class TestRerun(_DbCase):
    """7. 재실행: 같은 mentions/processed로 save_run 2번 → 행 수 그대로."""

    def test_save_run_idempotent(self):
        tid = resolve_theme(self.con, "알래스카 LNG")
        mentions = [
            {
                "theme_id": tid,
                "source": "telegram",
                "ref": "p1",
                "posted_at": _iso(_BASE),
                "collected_at": _iso(_BASE),
            }
        ]
        processed = [{"source": "telegram", "ref": "p1"}]
        save_run(self.con, mentions, [], processed)
        before = (self._table_count("theme_mentions"), self._table_count("theme_processed"))
        save_run(self.con, mentions, [], processed)
        after = (self._table_count("theme_mentions"), self._table_count("theme_processed"))
        self.assertEqual(before, (1, 1))
        self.assertEqual(after, before)
        self.assertTrue(is_processed(self.con, "telegram", "p1"))
        self.assertFalse(is_processed(self.con, "telegram", "p2"))


class TestMessageLinks(unittest.TestCase):
    """8. 문구: refs 7개 섞어서 → 링크 5개 이른 순 + '- 외 2건'. KST 변환."""

    def test_links_sorted_capped_and_kst(self):
        refs = [
            _ref(f"L{i}", _iso(_BASE + timedelta(hours=i)), _iso(_BASE + timedelta(hours=i, minutes=53)))
            for i in [6, 3, 0, 5, 1, 4, 2]  # 게시 순서 섞음
        ]
        # 가장 이른 게시를 UTC 00:12 로 고정 → KST 09:12
        refs[2] = _ref("L0", "2026-09-01T00:12:00+00:00", "2026-09-01T01:05:00+00:00")
        msg = format_message("X", 0, "new", "언급/전망", "요약문", "증권사 전망", refs)
        self.assertIn("- 게시 09/01 09:12 · 수집 09/01 10:05", msg)
        pos = [msg.find(f"- [L{i}]") for i in range(5)]
        self.assertTrue(all(p >= 0 for p in pos), msg)
        self.assertEqual(pos, sorted(pos))  # 이른 순
        self.assertIn("- 외 2건", msg)
        self.assertNotIn("- [L5]", msg)
        self.assertNotIn("- [L6]", msg)


class TestMessageHeadline(unittest.TestCase):
    """9·10. 등록 ⭐ + [신규 테마]/[진전] 머리말."""

    def _one_ref(self):
        return [_ref("L0", _iso(_BASE), _iso(_BASE))]

    def test_registered_star(self):
        starred = format_message(
            "X", 1, "new", "언급/전망", "s", "증권사 전망", self._one_ref()
        )
        plain = format_message(
            "X", 0, "new", "언급/전망", "s", "증권사 전망", self._one_ref()
        )
        self.assertTrue(starred.startswith("⭐ [신규 테마]"), starred)
        self.assertFalse("⭐" in plain, plain)

    def test_new_headline_gap_variants(self):
        first = format_message(
            "X", 0, "new", "언급/전망", "s", "증권사 전망", self._one_ref()
        )
        reappear = format_message(
            "X", 0, "new", "언급/전망", "s", "증권사 전망", self._one_ref(), gap_days=31.0
        )
        self.assertTrue(first.startswith("[신규 테마] X (첫 관측)"), first)
        self.assertTrue(reappear.startswith("[신규 테마] X (31일 만에 재등장)"), reappear)

    def test_progress_headline(self):
        msg = format_message(
            "X",
            0,
            "progress",
            "계약·발주",
            "s",
            "공식 확정",
            self._one_ref(),
            prev_stage="협의",
        )
        self.assertTrue(msg.startswith("[진전] X — 협의 → 계약·발주"), msg)

    def test_progress_headline_no_prev_stage(self):
        msg = format_message(
            "X",
            0,
            "progress",
            "검토",
            "s",
            "증권사 전망",
            self._one_ref(),
            prev_stage=None,
        )
        self.assertTrue(msg.startswith("[진전] X — 기록 없음 → 검토"), msg)
        self.assertNotIn("None", msg)


# ── 2단계 테스트: 가짜 llm_fn 주입 + 임시 DB (진짜 codex 호출 없음) ──
import contextlib
import io
import re
from pathlib import Path
from unittest import mock

from scripts import notify as notify_mod
from scripts.run_theme_alert import connect_ro, main


class _FakeLlm:
    """프롬프트 내용 보고 정해진 JSON 반환. extract/summary는 dict 또는 prompt->dict."""

    def __init__(self, extract=None, summary=None):
        self.extract = extract if extract is not None else {"themes": []}
        self.summary = summary if summary is not None else {"themes": []}
        self.calls = []  # [(prompt, schema_path)]

    def __call__(self, prompt, schema_path):
        self.calls.append((prompt, str(schema_path)))
        resp = self.extract if "theme_extract" in str(schema_path) else self.summary
        if callable(resp):
            resp = resp(prompt)
        return json.dumps(resp, ensure_ascii=False)


def _summary_for_prompt(prompt, stage="언급/전망", evidence="증권사 전망"):
    ids = [int(m) for m in re.findall(r"### 테마 (\d+):", prompt)]
    return {
        "themes": [
            {
                "theme_id": i,
                "stage": stage,
                "evidence_level": evidence,
                "summary": f"요약{i}",
            }
            for i in ids
        ]
    }


def _extract_all_refs(prompt, name="알래스카 LNG"):
    refs = re.findall(r"^\[([^]\s]+) · ", prompt, re.M)
    if not refs:
        return {"themes": []}
    return {"themes": [{"name": name, "match_existing": None, "refs": refs}]}


class _ThemeAlertCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tg_path = str(Path(self._tmp.name) / "telegram_public.sqlite3")
        self.yt_path = str(Path(self._tmp.name) / "youtube_public.sqlite3")
        self.state_path = str(Path(self._tmp.name) / "theme_alert.sqlite3")
        con = sqlite3.connect(self.tg_path)
        con.execute(
            "CREATE TABLE telegram_posts (channel TEXT, post_id INTEGER, post_ref TEXT,"
            " posted_at_utc TEXT, date_kst TEXT, text TEXT, links_json TEXT,"
            " raw_json TEXT, created_at TEXT, updated_at TEXT)"
        )
        con.commit()
        con.close()
        con = sqlite3.connect(self.yt_path)
        con.execute(
            "CREATE TABLE youtube_videos (channel_id TEXT, video_id TEXT, title TEXT,"
            " published_at_utc TEXT, date_kst TEXT, url TEXT, transcript TEXT,"
            " transcript_lang TEXT, transcript_source TEXT, raw_json TEXT,"
            " created_at TEXT, updated_at TEXT)"
        )
        con.execute(
            "CREATE TABLE youtube_video_summaries (channel_id TEXT, video_id TEXT,"
            " date_kst TEXT, model TEXT, summary_json TEXT, created_at TEXT, updated_at TEXT)"
        )
        con.commit()
        con.close()
        self.sent = []

    def _add_post(self, channel, post_id, date_kst, text, hour=1):
        post_ref = f"{channel}/{post_id}"
        posted = f"{date_kst}T{hour:02d}:00:00+00:00"
        con = sqlite3.connect(self.tg_path)
        con.execute(
            "INSERT INTO telegram_posts VALUES (?, ?, ?, ?, ?, ?, '[]', NULL, ?, ?)",
            (channel, post_id, post_ref, posted, date_kst, text, posted, posted),
        )
        con.commit()
        con.close()
        return post_ref

    def _add_video(self, channel_id, video_id, date_kst, title, summary=None, hour=1):
        published = f"{date_kst}T{hour:02d}:00:00+00:00"
        con = sqlite3.connect(self.yt_path)
        con.execute(
            "INSERT INTO youtube_videos VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)",
            (
                channel_id, video_id, title, published, date_kst,
                f"https://youtube.com/watch?v={video_id}", published, published,
            ),
        )
        con.commit()
        con.close()
        if summary is not None:
            self._add_summary(channel_id, video_id, date_kst, summary)
        return video_id

    def _add_summary(self, channel_id, video_id, date_kst, summary):
        stamped = f"{date_kst}T01:00:00+00:00"
        con = sqlite3.connect(self.yt_path)
        con.execute(
            "INSERT INTO youtube_video_summaries VALUES (?, ?, ?, 'fake', ?, ?, ?)",
            (
                channel_id, video_id, date_kst,
                json.dumps(summary, ensure_ascii=False), stamped, stamped,
            ),
        )
        con.commit()
        con.close()

    def _send(self, message):
        self.sent.append(message)
        return True

    def _run(self, argv, llm, channels=None, config_path=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return main(
                argv,
                llm_fn=llm,
                send_fn=self._send,
                state_db_path=self.state_path,
                telegram_db_path=self.tg_path,
                youtube_db_path=self.yt_path,
                config_path=config_path,
                channels=channels,
            )

    def _state(self):
        con = sqlite3.connect(self.state_path)
        self.addCleanup(con.close)
        return con


class TestTelegramEndToEnd(_ThemeAlertCase):
    """1. 텔레그램 end-to-end(send): 비탐색 무시, 신규 1통, processed 전부."""

    def test_send_new_theme(self):
        self._add_post("ch1", 1, "2026-09-15", "알래스카 LNG 본문 " * 10)
        self._add_post("ch1", 2, "2026-09-15", "알래스카 LNG 후속")
        self._add_post("other", 1, "2026-09-15", "비탐색 채널 글")
        llm = _FakeLlm(
            extract={
                "themes": [
                    {
                        "name": "알래스카 LNG",
                        "match_existing": None,
                        "refs": ["ch1/1", "ch1/2"],
                    }
                ]
            },
            summary=_summary_for_prompt,
        )
        rc = self._run(
            ["--source", "telegram", "--date", "2026-09-15"], llm, channels=["ch1"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("[신규 테마]", self.sent[0])
        self.assertIn("https://telegram.me/", self.sent[0])
        con = self._state()
        processed = {r[0] for r in con.execute("SELECT ref FROM theme_processed")}
        self.assertEqual(processed, {"ch1/1", "ch1/2"})
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_mentions").fetchone()[0], 2
        )


class TestRerunNoop(_ThemeAlertCase):
    """2. 재실행: 같은 인자 → llm 0회, send 0회."""

    def test_second_run_is_noop(self):
        self._add_post("ch1", 1, "2026-09-15", "알래스카 LNG 본문")
        llm = _FakeLlm(
            extract={
                "themes": [
                    {"name": "알래스카 LNG", "match_existing": None, "refs": ["ch1/1"]}
                ]
            },
            summary=_summary_for_prompt,
        )
        argv = ["--source", "telegram", "--date", "2026-09-15"]
        self._run(argv, llm, channels=["ch1"])
        self.assertEqual((len(llm.calls), len(self.sent)), (2, 1))
        self._run(argv, llm, channels=["ch1"])
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(len(self.sent), 1)


class TestUnknownRefDropped(_ThemeAlertCase):
    """3. 입력에 없는 ref를 LLM이 내면 버려짐."""

    def test_unknown_ref_dropped(self):
        self._add_post("ch1", 1, "2026-09-15", "알래스카 LNG 본문")
        self._add_post("ch1", 2, "2026-09-15", "무관한 글")
        llm = _FakeLlm(
            extract={
                "themes": [
                    {
                        "name": "알래스카 LNG",
                        "match_existing": None,
                        "refs": ["ch1/1", "nope/999"],
                    }
                ]
            },
            summary=_summary_for_prompt,
        )
        self._run(["--source", "telegram", "--date", "2026-09-15"], llm, channels=["ch1"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("ch1/1", self.sent[0])
        self.assertNotIn("nope", self.sent[0])
        con = self._state()
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_mentions").fetchone()[0], 1
        )
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_processed").fetchone()[0], 2
        )


class TestYoutubeWaitsForSummary(_ThemeAlertCase):
    """4. 유튜브: 요약 없는 영상은 처리 안 됨 → 요약 생기면 처리됨."""

    def _summary(self):
        return {
            "headline": "알래스카 LNG 진전",
            "issues": [{"title": "계약", "summary": "MOU 체결", "time_hint": None}],
            "bullets": [],
            "risk_or_caveat": None,
        }

    def test_waits_then_processes(self):
        self._add_video("c1", "v1", "2026-09-15", "LNG 영상")
        llm = _FakeLlm(extract=_extract_all_refs, summary=_summary_for_prompt)
        argv = ["--source", "youtube", "--date", "2026-09-15"]
        self._run(argv, llm)
        self.assertEqual(llm.calls, [])
        con = self._state()
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_processed").fetchone()[0], 0
        )
        self._add_summary("c1", "v1", "2026-09-15", self._summary())
        self._run(argv, llm)
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("알래스카 LNG 진전", llm.calls[0][0])  # headline이 원문에 들어감
        self.assertIn("계약", llm.calls[0][0])
        con = self._state()
        processed = {r[0] for r in con.execute("SELECT ref FROM theme_processed")}
        self.assertEqual(processed, {"v1"})


class TestNoSend(_ThemeAlertCase):
    """5. --no-send: 전송 0, sent_at NULL, mentions 저장."""

    def test_no_send(self):
        self._add_post("ch1", 1, "2026-09-15", "알래스카 LNG 본문")
        llm = _FakeLlm(
            extract={
                "themes": [
                    {"name": "알래스카 LNG", "match_existing": None, "refs": ["ch1/1"]}
                ]
            },
            summary=_summary_for_prompt,
        )
        rc = self._run(
            ["--source", "telegram", "--date", "2026-09-15", "--no-send"],
            llm,
            channels=["ch1"],
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])
        con = self._state()
        row = con.execute("SELECT kind, sent_at, message FROM theme_alerts").fetchone()
        self.assertEqual(row[0], "new")
        self.assertIsNone(row[1])
        self.assertIn("[신규 테마]", row[2])
        self.assertGreater(
            con.execute("SELECT COUNT(*) FROM theme_mentions").fetchone()[0], 0
        )


class TestSendFailure(_ThemeAlertCase):
    """5b. 전송 실패: send_fn이 False → 저장 건너뛰고 다음 실행에서 재시도."""

    def test_failed_send_stores_null_sent_at(self):
        self._add_post("ch1", 1, "2026-09-15", "알래스카 LNG 본문")
        llm = _FakeLlm(
            extract={
                "themes": [
                    {"name": "알래스카 LNG", "match_existing": None, "refs": ["ch1/1"]}
                ]
            },
            summary=_summary_for_prompt,
        )

        def fail_send(message):
            self.sent.append(message)
            return False

        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(
                ["--source", "telegram", "--date", "2026-09-15"],
                llm_fn=llm,
                send_fn=fail_send,
                state_db_path=self.state_path,
                telegram_db_path=self.tg_path,
                youtube_db_path=self.yt_path,
                channels=["ch1"],
            )
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        # 1회차: 전송 실패 → alerts·mentions·processed 전부 저장 안 됨
        con = self._state()
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_alerts").fetchone()[0], 0
        )
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_mentions").fetchone()[0], 0
        )
        self.assertEqual(
            con.execute(
                "SELECT COUNT(*) FROM theme_processed WHERE ref = ?", ("ch1/1",)
            ).fetchone()[0],
            0,
        )
        calls_after_first = len(llm.calls)
        self.assertEqual(calls_after_first, 2)

        # 2회차: 같은 입력, 전송 성공 → llm 재호출·[신규 테마] 전송·저장
        self.sent.clear()
        rc = self._run(
            ["--source", "telegram", "--date", "2026-09-15"], llm, channels=["ch1"]
        )
        self.assertEqual(rc, 0)
        self.assertGreater(len(llm.calls), calls_after_first)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("[신규 테마]", self.sent[0])
        con = self._state()
        row = con.execute("SELECT sent_at FROM theme_alerts").fetchone()
        self.assertIsNotNone(row[0])
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_mentions").fetchone()[0], 1
        )
        self.assertEqual(
            con.execute(
                "SELECT COUNT(*) FROM theme_processed WHERE ref = ?", ("ch1/1",)
            ).fetchone()[0],
            1,
        )

    def test_shared_ref_partial_failure(self):
        self._add_post("ch1", 1, "2026-09-15", "두 테마 본문")
        llm = _FakeLlm(
            extract={
                "themes": [
                    {"name": "테마A", "match_existing": None, "refs": ["ch1/1"]},
                    {"name": "테마B", "match_existing": None, "refs": ["ch1/1"]},
                ]
            },
            summary=_summary_for_prompt,
        )

        def flaky_send(message):
            self.sent.append(message)
            return "테마B" not in message

        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(
                ["--source", "telegram", "--date", "2026-09-15"],
                llm_fn=llm,
                send_fn=flaky_send,
                state_db_path=self.state_path,
                telegram_db_path=self.tg_path,
                youtube_db_path=self.yt_path,
                channels=["ch1"],
            )
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 2)
        con = self._state()
        tid_a = con.execute(
            "SELECT theme_id FROM themes WHERE name = ?", ("테마A",)
        ).fetchone()[0]
        tid_b = con.execute(
            "SELECT theme_id FROM themes WHERE name = ?", ("테마B",)
        ).fetchone()[0]
        self.assertEqual(
            con.execute(
                "SELECT COUNT(*) FROM theme_mentions WHERE theme_id = ?", (tid_a,)
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            con.execute(
                "SELECT COUNT(*) FROM theme_mentions WHERE theme_id = ?", (tid_b,)
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            con.execute(
                "SELECT COUNT(*) FROM theme_alerts WHERE theme_id = ?", (tid_a,)
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_alerts").fetchone()[0], 1
        )
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_processed").fetchone()[0], 0
        )


class TestBackfill(_ThemeAlertCase):
    """6. --backfill: 전송 0·baseline 저장 → 같은 단계 무시, 상승만 [진전]."""

    def test_backfill_then_progress(self):
        self._add_post("ch1", 1, "2026-09-01", "LNG 첫 언급")
        self._add_post("ch1", 2, "2026-09-02", "LNG 후속")
        self._add_post("ch1", 3, "2026-09-03", "LNG 또 후속")
        llm_b = _FakeLlm(
            extract=_extract_all_refs,
            summary=lambda p: _summary_for_prompt(p, stage="협의"),
        )
        rc = self._run(
            [
                "--source", "telegram",
                "--start-date", "2026-09-01", "--date", "2026-09-03",
                "--backfill",
            ],
            llm_b,
            channels=["ch1"],
        )
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [])
        con = self._state()
        rows = con.execute(
            "SELECT kind, stage, sent_at, message FROM theme_alerts"
        ).fetchall()
        self.assertEqual(rows, [("baseline", "협의", None, "")])

        self._add_post("ch1", 4, "2026-09-10", "LNG 반복")
        llm_same = _FakeLlm(
            extract=_extract_all_refs,
            summary=lambda p: _summary_for_prompt(p, stage="협의"),
        )
        self._run(["--source", "telegram", "--date", "2026-09-10"], llm_same, channels=["ch1"])
        self.assertEqual(self.sent, [])

        self._add_post("ch1", 5, "2026-09-11", "LNG 계약")
        llm_up = _FakeLlm(
            extract=_extract_all_refs,
            summary=lambda p: _summary_for_prompt(p, stage="계약·발주"),
        )
        self._run(["--source", "telegram", "--date", "2026-09-11"], llm_up, channels=["ch1"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("[진전]", self.sent[0])
        self.assertIn("협의 → 계약·발주", self.sent[0])


class TestFailureAlert(_ThemeAlertCase):
    """7. 실패: llm 예외 → 실패 1통, 반환 0, processed 0행."""

    def test_llm_error_sends_failure(self):
        self._add_post("ch1", 1, "2026-09-15", "LNG")

        def boom(prompt, schema_path):
            raise RuntimeError("codex 다운")

        with contextlib.redirect_stderr(io.StringIO()):
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(
                    ["--source", "telegram", "--date", "2026-09-15"],
                    llm_fn=boom,
                    send_fn=self.sent.append,
                    state_db_path=self.state_path,
                    telegram_db_path=self.tg_path,
                    youtube_db_path=self.yt_path,
                    channels=["ch1"],
                )
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("[테마 알림 실패]", self.sent[0])
        con = self._state()
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM theme_processed").fetchone()[0], 0
        )


class TestReadOnly(_ThemeAlertCase):
    """8. connect_ro로 연 원본 DB에 INSERT → OperationalError."""

    def test_insert_raises(self):
        con = connect_ro(Path(self.tg_path))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute(
                    "INSERT INTO telegram_posts VALUES"
                    " ('x', 1, 'x/1', 't', 'd', 't', '[]', NULL, 't', 't')"
                )
        finally:
            con.close()


class TestRegisteredTheme(_ThemeAlertCase):
    """9. 등록 테마: 추출 프롬프트 '반드시 확인할 테마' 절 + 알림 ⭐."""

    def test_registered_listed_and_starred(self):
        con = sqlite3.connect(self.state_path)
        ensure_schema(con)
        con.execute(
            "INSERT INTO themes (name, registered, created_at) VALUES (?, 1, ?)",
            ("알래스카 LNG", "2026-09-01T00:00:00+00:00"),
        )
        con.commit()
        con.close()
        self._add_post("ch1", 1, "2026-09-15", "가스관 얘기")
        llm = _FakeLlm(
            extract={
                "themes": [
                    {
                        "name": "알래스카 LNG",
                        "match_existing": "알래스카 LNG",
                        "refs": ["ch1/1"],
                    }
                ]
            },
            summary=_summary_for_prompt,
        )
        self._run(["--source", "telegram", "--date", "2026-09-15"], llm, channels=["ch1"])
        extract_prompt = llm.calls[0][0]
        self.assertIn("반드시 확인할 테마", extract_prompt)
        section = extract_prompt[extract_prompt.index("반드시 확인할 테마") :]
        self.assertIn("알래스카 LNG", section)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].startswith("⭐ "), self.sent[0])


class TestPastRefsCap(_ThemeAlertCase):
    """10. 과거 원문 상한: max_past_refs=3, 과거 5건 → 요약 프롬프트에 3건만."""

    def test_max_past_refs(self):
        cfg_path = str(Path(self._tmp.name) / "theme_alert.json")
        Path(cfg_path).write_text(
            json.dumps({"quiet_days": 30, "max_past_refs": 3}), encoding="utf-8"
        )
        con = sqlite3.connect(self.state_path)
        ensure_schema(con)
        tid = resolve_theme(con, "알래스카 LNG")
        con.close()
        for i in range(1, 6):
            self._add_post("ch-old", i, f"2026-08-0{i}", f"과거 {i}")
        con = sqlite3.connect(self.state_path)
        save_run(
            con,
            [
                {
                    "theme_id": tid,
                    "source": "telegram",
                    "ref": f"ch-old/{i}",
                    "posted_at": f"2026-08-0{i}T01:00:00+00:00",
                    "collected_at": f"2026-08-0{i}T01:00:00+00:00",
                }
                for i in range(1, 6)
            ],
            [],
            [],
        )
        con.close()
        self._add_post("ch1", 1, "2026-09-15", "LNG 재등장")
        llm = _FakeLlm(
            extract={
                "themes": [
                    {
                        "name": "알래스카 LNG",
                        "match_existing": "알래스카 LNG",
                        "refs": ["ch1/1"],
                    }
                ]
            },
            summary=_summary_for_prompt,
        )
        self._run(
            ["--source", "telegram", "--date", "2026-09-15"],
            llm,
            channels=["ch1"],
            config_path=cfg_path,
        )
        summary_prompt = llm.calls[1][0]
        for i in (3, 4, 5):
            self.assertIn(f"ch-old/{i}", summary_prompt)
        for i in (1, 2):
            self.assertNotIn(f"ch-old/{i}", summary_prompt)


class TestThemeAlertChannel(unittest.TestCase):
    """11. notify(..., channel="theme_alert") → send_theme_alert."""

    def test_dispatch(self):
        with mock.patch.object(notify_mod, "send_theme_alert", return_value=True) as m:
            notify_mod.notify("x", channel="theme_alert")
        m.assert_called_once_with("x")

    def test_sender_uses_webhook_env(self):
        with mock.patch.object(notify_mod, "send_discord", return_value=True) as m:
            with mock.patch.dict(
                os.environ, {"THEME_ALERT_DISCORD_WEBHOOK_URL": "https://hook/x"}
            ):
                notify_mod.send_theme_alert("m")
        m.assert_called_once_with("m", webhook_url="https://hook/x")


class TestEnvPath(unittest.TestCase):
    """12. ENV_PATH가 저장소 루트의 .env를 가리킴."""

    def test_env_path_points_to_repo_root(self):
        self.assertEqual(ENV_PATH.name, ".env")
        self.assertTrue((ENV_PATH.parent / "etl").is_dir())
        self.assertTrue((ENV_PATH.parent / "ops").is_dir())


if __name__ == "__main__":
    unittest.main()
